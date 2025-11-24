from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from app.adapters import get_adapter
from app.core.config import settings
from app.models.behavior import Behavior
from app.models.behavior_contract import BehaviorContract
from app.models.behavior_implementation import BehaviorImplementation
from app.services.github_client import get_github_client, GitHubError


class TestHarnessError(Exception):
    pass


def _get_behavior(db: Session, behavior_id: int) -> Behavior:
    behavior = db.get(Behavior, behavior_id)
    if not behavior:
        raise TestHarnessError(f"Behavior {behavior_id} not found")
    return behavior


def _get_source_implementation(
    db: Session,
    behavior_id: int,
    language: str,
) -> BehaviorImplementation:
    q = (
        db.query(BehaviorImplementation)
        .filter(
            BehaviorImplementation.behavior_id == behavior_id,
            BehaviorImplementation.language == language,
            BehaviorImplementation.status.in_(["source", "validated", "converted"]),
        )
        .order_by(BehaviorImplementation.created_at.desc())
    )
    impl = q.first()
    if not impl:
        raise TestHarnessError(
            f"No source implementation found for behavior {behavior_id} in language '{language}'"
        )
    return impl


def _get_contract(
    db: Session,
    behavior_id: int,
    contract_id: Optional[int],
) -> Optional[BehaviorContract]:
    if contract_id is not None:
        contract = db.get(BehaviorContract, contract_id)
        if not contract or contract.behavior_id != behavior_id:
            raise TestHarnessError(
                f"Contract {contract_id} not found for behavior {behavior_id}"
            )
        return contract

    q = (
        db.query(BehaviorContract)
        .filter(BehaviorContract.behavior_id == behavior_id)
        .order_by(BehaviorContract.created_at.desc())
    )
    return q.first()


def _derive_tests_repo_name(source_impl: BehaviorImplementation, language: str) -> str:
    """
    Derive a GitHub repo name for the test harness.

    Example:
      source repo: https://github.com/rlee-boids/perl-plot-project
      -> multilang-converted-tests-perl-plot-project-perl
    """
    prefix = settings.GITHUB_REPO_PREFIX or "multilang-converted-"

    base = "unknown"
    if source_impl.repo_url:
        # naive parse: last path segment of URL, without .git
        parts = source_impl.repo_url.rstrip("/").split("/")
        if parts:
            base = parts[-1]
            if base.endswith(".git"):
                base = base[:-4]

    return f"{prefix}tests-{base}-{language.lower()}"


async def build_legacy_test_harness(
    db: Session,
    behavior_id: int,
    language: str,
    contract_id: Optional[int] = None,
    target_repo_name: Optional[str] = None,
) -> BehaviorImplementation:
    """
    Build a *separate* GitHub repo containing unit tests for a legacy implementation.

    Flow:
      - Fetch Behavior + legacy BehaviorImplementation
      - Fetch BehaviorContract (optional, latest if not specified)
      - Use LanguageAdapter.generate_test_code_from_contract() to create tests
        in a local workspace directory
      - Create or ensure a GitHub repo for the harness
      - Upload all files from the workspace to that repo
      - Create a BehaviorImplementation row (currently status='candidate')
    """
    behavior = _get_behavior(db, behavior_id)
    source_impl = _get_source_implementation(db, behavior_id, language)
    contract = _get_contract(db, behavior_id, contract_id)

    try:
        adapter = get_adapter(language)
    except KeyError:
        raise TestHarnessError(f"No LanguageAdapter registered for '{language}'")

    # --- Prepare local harness workspace ---
    root = Path(settings.ANALYZER_WORKSPACE_ROOT or "./workspace")
    harness_root = root / "harness" / f"behavior_{behavior_id}_{language.lower()}"
    if harness_root.exists():
        # Blow away previous harness for now; later we might diff/update instead.
        import shutil

        shutil.rmtree(harness_root)
    harness_root.mkdir(parents=True, exist_ok=True)

    # Let the adapter generate tests into this directory.
    adapter.generate_test_code_from_contract(contract, output_path=str(harness_root))

    # Optional metadata file for humans
    info_file = harness_root / "HARNESS_INFO.md"
    info_lines = [
        f"# Legacy Test Harness for Behavior {behavior_id}",
        "",
        f"- Behavior name: {behavior.name}",
        f"- Language: {language}",
        f"- Source repo: {source_impl.repo_url or '<none>'}",
        f"- Source revision: {source_impl.revision or '<none>'}",
    ]
    if contract:
        info_lines.append(f"- Contract id: {contract.id} (version {contract.version})")
        info_lines.append(f"- Contract name: {contract.name}")
    else:
        info_lines.append("- Contract: none (placeholder tests only)")
    info_lines.append("")
    info_lines.append(
        "This repo contains test harness code used to validate legacy and converted "
        "implementations in containerized runtimes."
    )
    info_file.write_text("\n".join(info_lines))

    # --- GitHub repo creation + upload ---
    gh = get_github_client()

    repo_name = target_repo_name or _derive_tests_repo_name(source_impl, language)
    try:
        repo = await gh.ensure_repo(repo_name=repo_name, private=True)
    except GitHubError as exc:
        raise TestHarnessError(f"GitHub repo creation failed: {exc}")

    # Walk the harness_root and push all files
    for path in harness_root.rglob("*"):
        if path.is_dir():
            continue
        rel = path.relative_to(harness_root)
        content = path.read_text()
        commit_msg = f"Add test harness file for behavior {behavior_id}"
        try:
            await gh.create_or_update_file(
                repo=repo,
                path=str(rel),
                content=content,
                commit_message=commit_msg,
            )
        except GitHubError as exc:
            raise TestHarnessError(
                f"GitHub file upload failed for {rel}: {exc}"
            ) from exc

    # --- DB record for harness implementation ---
    now = datetime.utcnow()
    notes_lines = [
        "# Legacy Test Harness",
        "",
        f"- Behavior: {behavior.id} ({behavior.name})",
        f"- Language: {language}",
        f"- Source implementation id: {source_impl.id}",
        f"- Harness repo (HTML): {repo.html_url}",
        f"- Harness repo (clone): https://github.com/{repo.owner}/{repo.name}.git",
        f"- Harness root: tests/ (and other files)",
    ]
    if contract:
        notes_lines.append(f"- Contract id: {contract.id} (version {contract.version})")
    else:
        notes_lines.append("- Contract: none (placeholder tests only)")
    notes_lines.append("")
    notes_lines.append(
        "This BehaviorImplementation represents a *test harness* repo, not the legacy code itself. "
        "Status is 'candidate' only because the enum doesn't yet include a dedicated harness value."
    )
    notes_md = "\n".join(notes_lines)

    harness_impl = BehaviorImplementation(
        behavior_id=behavior.id,
        language=language,
        repo_url=repo.html_url,
        revision=repo.default_branch or "main",
        file_path="tests/",
        # IMPORTANT: use an existing enum value; we treat this semantically as a harness.
        status="candidate",
        notes=notes_md,
        created_at=now,
        updated_at=now,
    )

    db.add(harness_impl)
    db.commit()
    db.refresh(harness_impl)

    return harness_impl
