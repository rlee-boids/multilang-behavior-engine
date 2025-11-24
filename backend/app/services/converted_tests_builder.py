from __future__ import annotations

import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app.adapters import get_adapter
from app.models.behavior import Behavior
from app.models.behavior_contract import BehaviorContract
from app.models.behavior_implementation import BehaviorImplementation
from app.services.github_client import get_github_client, GitHubError, GitHubRepoInfo


class ConvertedTestsError(Exception):
    pass


def _get_behavior(db: Session, behavior_id: int) -> Behavior:
    behavior = db.get(Behavior, behavior_id)
    if not behavior:
        raise ConvertedTestsError(f"Behavior {behavior_id} not found")
    return behavior


def _get_implementation(db: Session, implementation_id: int) -> BehaviorImplementation:
    impl = db.get(BehaviorImplementation, implementation_id)
    if not impl:
        raise ConvertedTestsError(f"BehaviorImplementation {implementation_id} not found")
    return impl


def _get_contract(
    db: Session,
    behavior_id: int,
    contract_id: Optional[int],
) -> Optional[BehaviorContract]:
    if contract_id is not None:
        contract = db.get(BehaviorContract, contract_id)
        if not contract or contract.behavior_id != behavior_id:
            raise ConvertedTestsError(
                f"Contract {contract_id} not found for behavior {behavior_id}"
            )
        return contract

    q = (
        db.query(BehaviorContract)
        .filter(BehaviorContract.behavior_id == behavior_id)
        .order_by(BehaviorContract.created_at.desc())
    )
    return q.first()


def _parse_repo_from_url(repo_url: str, revision: Optional[str]) -> GitHubRepoInfo:
    """
    Parse owner/name from a standard GitHub HTML or clone URL.

    Examples:
      https://github.com/rlee-boids/perl-plot-project-python-port
      https://github.com/rlee-boids/perl-plot-project-python-port.git
    """
    if not repo_url:
        raise ConvertedTestsError("Implementation repo_url is empty; cannot build tests")

    parsed = urlparse(repo_url)
    if not parsed.netloc or "github.com" not in parsed.netloc:
        raise ConvertedTestsError(
            f"Unsupported repo_url for converted tests: {repo_url!r} "
            "(expected GitHub URL)"
        )

    parts = parsed.path.strip("/").split("/")
    if len(parts) < 2:
        raise ConvertedTestsError(
            f"Cannot parse owner/repo from URL path: {parsed.path!r}"
        )

    owner, name = parts[0], parts[1]
    if name.endswith(".git"):
        name = name[:-4]

    html_url = f"https://github.com/{owner}/{name}"
    clone_url = f"https://github.com/{owner}/{name}.git"
    default_branch = revision or "main"

    return GitHubRepoInfo(
        owner=owner,
        name=name,
        html_url=html_url,
        clone_url=clone_url,
        default_branch=default_branch,
    )


async def build_converted_tests_for_implementation(
    db: Session,
    implementation_id: int,
    contract_id: Optional[int] = None,
) -> BehaviorImplementation:
    """
    Generate contract-driven tests *inside the converted code repo*.

    Flow:
      - Load BehaviorImplementation (converted or candidate)
      - Load associated Behavior + BehaviorContract (latest if contract_id is None)
      - Use LanguageAdapter.generate_test_code_from_contract() into a temp dir
      - Push generated test files to the existing GitHub repo via Contents API
      - Update BehaviorImplementation.notes and updated_at
      - Return the updated BehaviorImplementation
    """
    impl = _get_implementation(db, implementation_id)
    behavior = _get_behavior(db, impl.behavior_id)
    contract = _get_contract(db, behavior.id, contract_id)

    try:
        adapter = get_adapter(impl.language)
    except KeyError:
        raise ConvertedTestsError(f"No LanguageAdapter registered for '{impl.language}'")

    if not impl.repo_url:
        raise ConvertedTestsError(
            f"Implementation {impl.id} has no repo_url; cannot publish tests"
        )

    # --- Generate tests into a temporary local directory ---
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        adapter.generate_test_code_from_contract(contract, output_path=str(tmp_path))

        # --- Push generated tests into the converted repo via GitHub Contents API ---
        gh = get_github_client()
        repo_info = _parse_repo_from_url(impl.repo_url, impl.revision)

        # Walk the temp directory and upload files
        for path in tmp_path.rglob("*"):
            if path.is_dir():
                continue
            rel = path.relative_to(tmp_path)
            content = path.read_text()
            commit_msg = (
                f"Add contract-driven tests for implementation {impl.id} "
                f"(behavior {behavior.id})"
            )
            try:
                await gh.create_or_update_file(
                    repo=repo_info,
                    path=str(rel),
                    content=content,
                    commit_message=commit_msg,
                )
            except GitHubError as exc:
                raise ConvertedTestsError(
                    f"GitHub file upload failed for {rel}: {exc}"
                ) from exc

    # --- Update BehaviorImplementation metadata ---
    now = datetime.utcnow()

    notes_lines = []
    if impl.notes:
        notes_lines.append(impl.notes.rstrip())
        notes_lines.append("")
        notes_lines.append("---")
        notes_lines.append("")

    notes_lines.append("# Converted Tests")
    notes_lines.append("")
    notes_lines.append(f"- Behavior: {behavior.id} ({behavior.name})")
    notes_lines.append(f"- Implementation id: {impl.id}")
    notes_lines.append(f"- Language: {impl.language}")
    notes_lines.append(f"- Repo: {impl.repo_url}")
    if contract:
        notes_lines.append(
            f"- Contract id: {contract.id} (version {contract.version})"
        )
    else:
        notes_lines.append("- Contract: none (tests are generic scaffolds)")
    notes_lines.append("")
    notes_lines.append(
        "Contract-driven pytest tests have been generated and committed into this repo. "
        "They live under `tests/` or `test_*.py` files and are intended to be run via "
        "the runtime/test-implementation Podman harness."
    )

    impl.notes = "\n".join(notes_lines)
    impl.updated_at = now

    db.add(impl)
    db.commit()
    db.refresh(impl)

    return impl
