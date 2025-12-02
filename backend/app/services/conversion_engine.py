from __future__ import annotations

from datetime import datetime
from pathlib import PurePosixPath
from typing import Dict, Any, List, Optional

from sqlalchemy.orm import Session

from app.adapters import get_adapter
from app.core.config import settings
from app.models.behavior import Behavior
from app.models.behavior_contract import BehaviorContract
from app.models.behavior_implementation import BehaviorImplementation
from app.services.github_client import get_github_client, GitHubError
from app.services.ai_conversion import generate_target_code_from_ai, AIConversionError


class ConversionError(Exception):
    pass


def _get_behavior(db: Session, behavior_id: int) -> Behavior:
    behavior = db.get(Behavior, behavior_id)
    if not behavior:
        raise ConversionError(f"Behavior {behavior_id} not found")
    return behavior


def _get_source_implementation(
    db: Session,
    behavior_id: int,
    source_language: str,
) -> BehaviorImplementation:
    q = (
        db.query(BehaviorImplementation)
        .filter(
            BehaviorImplementation.behavior_id == behavior_id,
            BehaviorImplementation.language == source_language,
            BehaviorImplementation.status.in_(["source", "validated", "converted"]),
        )
        .order_by(BehaviorImplementation.created_at.desc())
    )
    impl = q.first()
    if not impl:
        raise ConversionError(
            f"No source implementation found for behavior {behavior_id} in language '{source_language}'"
        )
    if not impl.repo_url or not impl.file_path or not impl.revision:
        raise ConversionError(
            f"Source implementation {impl.id} is missing repo_url, file_path, or revision"
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
            raise ConversionError(
                f"Contract {contract_id} not found for behavior {behavior_id}"
            )
        return contract

    q = (
        db.query(BehaviorContract)
        .filter(BehaviorContract.behavior_id == behavior_id)
        .order_by(BehaviorContract.created_at.desc())
    )
    return q.first()


def _derive_target_repo_name(behavior: Behavior, target_language: str) -> str:
    """
    Default repo-name derivation if the caller does not provide an explicit
    target_repo_name.

    For example:
      behavior.name == "Generate Plot"
      target_language == "python"
      -> "<GITHUB_REPO_PREFIX>generate_plot-python"
    """
    base = behavior.name.replace(" ", "_").lower()
    return f"{settings.GITHUB_REPO_PREFIX}{base}-{target_language.lower()}"


def _map_file_path_for_target_language(
    source_path: str,
    source_language: str,
    target_language: str,
) -> str:
    """
    Map the source file path to a reasonable target-language path.

    Goals:

    - Preserve relative layout for simple library modules (e.g. lib/Plot/Generator.pm -> lib/Plot/Generator.py).
    - Keep executable scripts under a predictable location (e.g. bin/foo.pl -> bin/foo.py).
    - For CGI-style UIs, move away from `cgi-bin/` into a more Pythonic layout while still
      keeping a 1:1 entrypoint that our runtime can execute in a container.

    NOTE: This mapping is deliberately conservative and filesystem-only. Any richer structural
    refactoring should be expressed in the AI prompt (see ai_conversion._build_conversion_prompt).
    """
    p = PurePosixPath(source_path)
    src = source_language.lower()
    tgt = target_language.lower()

    # Special handling for Perl -> Python conversion so we can keep consistent
    # behaviour across the whole repo (lib, bin, cgi).
    if src == "perl" and tgt == "python":
        p_str = str(p)

        # Library modules: keep under lib/ with a .py suffix.
        if p_str.startswith("lib/"):
            return str(p.with_suffix(".py"))

        # Executable scripts: keep under bin/, but with .py.
        if p_str.startswith("bin/"):
            return str(PurePosixPath("bin") / f"{p.stem}.py")

        # CGI / UI entrypoints: move into a small app/ui/ subpackage.
        # Example: cgi-bin/plot_ui.cgi -> app/ui/plot_ui.py
        if p_str.startswith("cgi-bin/"):
            return str(PurePosixPath("app") / "ui" / f"{p.stem}.py")

    # Generic extension mapping as a fallback.
    ext_map = {
        ("perl", "python"): ".py",
        ("perl", "perl"): ".pl",
        ("python", "perl"): ".pl",
    }

    new_suffix = ext_map.get((src, tgt))
    if new_suffix is None:
        # Fallback: keep original suffix
        return str(p)
    return str(p.with_suffix(new_suffix))


async def convert_full_project(
    db: Session,
    *,
    source_repo_url: str,
    source_revision: str,
    source_language: str,
    target_language: str,
    target_repo_name: Optional[str] = None,
) -> dict:
    """
    Convert *all* behaviors in a given source repo from source_language to target_language.

    Strategy:
      - Find all BehaviorImplementation rows for this repo + language + revision.
      - Group by behavior_id.
      - For each behavior_id, call convert_behavior_stub() (single-behavior conversion).
      - Return a mapping of source impl -> target impl (with file paths, behavior_id, etc.).
    """
    if source_language == target_language:
        raise ConversionError("Source and target language must be different")

    # Find all implementations in this repo / language / revision.
    impls: List[BehaviorImplementation] = (
        db.query(BehaviorImplementation)
        .filter(
            BehaviorImplementation.repo_url == source_repo_url,
            BehaviorImplementation.language == source_language,
            BehaviorImplementation.revision == source_revision,
        )
        .order_by(BehaviorImplementation.behavior_id.asc(), BehaviorImplementation.id.asc())
        .all()
    )

    if not impls:
        raise ConversionError(
            f"No implementations found in repo={source_repo_url!r} "
            f"revision={source_revision!r} language={source_language!r}"
        )

    # Deduplicate by behavior_id: for each behavior, pick the newest/first impl.
    by_behavior: Dict[int, BehaviorImplementation] = {}
    for impl in impls:
        if impl.behavior_id not in by_behavior:
            by_behavior[impl.behavior_id] = impl

    conversions: List[Dict[str, Any]] = []
    target_repo_url: Optional[str] = None

    # We re-use the same target_repo_name for all conversions so they land
    # in a single GitHub repo.
    for idx, (behavior_id, src_impl) in enumerate(by_behavior.items(), start=1):
        # For the first behavior, use the user-provided target_repo_name (or default).
        # For subsequent behaviors we reuse the *actual* repo name from the
        # first created target implementation via its repo_url.
        this_target_repo_name = target_repo_name
        if idx > 1 and target_repo_url:
            # Extract repo name from clone URL "https://github.com/owner/name.git"
            # so the rest of the conversions reuse that same repo.
            clean = target_repo_url.rstrip("/")
            if clean.endswith(".git"):
                clean = clean[:-4]
            parts = clean.split("/")
            if len(parts) >= 2:
                this_target_repo_name = parts[-1]

        target_impl = await convert_behavior_stub(
            db=db,
            behavior_id=behavior_id,
            source_language=source_language,
            target_language=target_language,
            contract_id=None,
            target_repo_name=this_target_repo_name,
        )

        # Track the target repo URL from the first conversion
        if target_repo_url is None:
            target_repo_url = target_impl.repo_url

        conversions.append(
            {
                "behavior_id": behavior_id,
                "source_implementation_id": src_impl.id,
                "source_file_path": src_impl.file_path,
                "target_implementation_id": target_impl.id,
                "target_file_path": target_impl.file_path,
                "target_language": target_impl.language,
                "target_repo_url": target_impl.repo_url,
            }
        )

    if target_repo_url is None and conversions:
        # Fall back: use the repo URL from the first conversion
        target_repo_url = conversions[0]["target_repo_url"]

    return {
        "target_repo_url": target_repo_url,
        "source_repo_url": source_repo_url,
        "source_revision": source_revision,
        "source_language": source_language,
        "target_language": target_language,
        "conversions": conversions,
    }

async def convert_behavior_stub(
    db: Session,
    behavior_id: int,
    source_language: str,
    target_language: str,
    contract_id: Optional[int] = None,
    target_repo_name: Optional[str] = None,
) -> BehaviorImplementation:
    """
    Conversion with GitHub integration + AI codegen for a *single* behaviour.

    It:
      - Ensures the behavior exists
      - Ensures we have a source implementation with repo_url/file_path/revision
      - Ensures we have adapters for both languages
      - Optionally looks up a contract
      - Ensures a GitHub repo exists (or is created) for converted code
      - Uses AI to generate target-language code from the source file
      - Falls back to a placeholder stub if AI fails
      - Writes the file to the target repo
      - Creates a new BehaviorImplementation row with status='candidate'
        and repo_url pointing to the Git clone URL (good for Podman).

    To convert an entire legacy project (e.g. the whole Perl repo) you can
    call this once per behaviour / source implementation, *reusing* the same
    target_repo_name so all converted files land in the same Python repo.
    """
    if source_language == target_language:
        raise ConversionError("Source and target language must be different")

    behavior = _get_behavior(db, behavior_id)
    source_impl = _get_source_implementation(db, behavior_id, source_language)
    contract = _get_contract(db, behavior_id, contract_id)

    try:
        source_adapter = get_adapter(source_language)
    except KeyError:
        raise ConversionError(f"No LanguageAdapter registered for '{source_language}'")

    try:
        target_adapter = get_adapter(target_language)
    except KeyError:
        raise ConversionError(f"No LanguageAdapter registered for '{target_language}'")

    # ---- GitHub repo + file creation ----
    gh = get_github_client()

    # If the caller provides an explicit target_repo_name (like "perl-plot-project-python-port"),
    # reuse that so multiple behaviours from the same legacy repo end up in a single Python repo.
    repo_name = target_repo_name or _derive_target_repo_name(behavior, target_language)
    try:
        repo = await gh.ensure_repo(repo_name=repo_name, private=True)
    except GitHubError as exc:
        raise ConversionError(f"GitHub repo creation failed: {exc}")

    target_path = _map_file_path_for_target_language(
        source_path=source_impl.file_path or "src/unknown.pl",
        source_language=source_language,
        target_language=target_language,
    )

    # Placeholder code in case AI fails
    placeholder_code = (
        f"# Placeholder {target_language} implementation for behavior '{behavior.name}'.\n"
        f"# Source language: {source_language} (adapter: {source_adapter.name})\n"
        f"# Target language: {target_language} (adapter: {target_adapter.name})\n"
        f"# Source file: {source_impl.file_path or '<unknown>'}\n"
        f"# Behavior ID: {behavior.id}\n"
    )
    if contract:
        placeholder_code += f"# Contract ID: {contract.id}, version: {contract.version}\n"

    placeholder_code += "\n\n"
    placeholder_code += "# TODO: Replace this placeholder with AI-generated implementation.\n"

    # ---- AI conversion ----
    try:
        generated_code = generate_target_code_from_ai(
            repo_url=source_impl.repo_url,
            revision=source_impl.revision,
            file_path=source_impl.file_path,
            behavior=behavior,
            contract=contract,
            source_language=source_language,
            target_language=target_language,
        )
        code_to_write = generated_code
        ai_note = "AI conversion succeeded"
        ai_error_message: Optional[str] = None
    except AIConversionError as exc:
        # Fall back to placeholder but annotate what failed
        ai_note = "AI conversion FAILED, placeholder stub written instead"
        ai_error_message = str(exc)
        code_to_write = placeholder_code + f"\n# AI conversion error: {exc}\n"

    # Write main target file
    try:
        file_url = await gh.create_or_update_file(
            repo=repo,
            path=target_path,
            content=code_to_write,
            commit_message=(
                f"Converted {source_language} -> {target_language} for behavior {behavior.id}"
            ),
        )
    except GitHubError as exc:
        raise ConversionError(f"GitHub file write failed: {exc}")

    # ---- Optionally ensure requirements.txt for Python targets ----
    # This gives the runtime a stable way to install deps via the PythonAdapter.build_command.
    notes_requirements: Optional[str] = None
    if target_language.lower() == "python":
        requirements_lines = [
            "# Auto-generated by MultiLang Behavior Engine",
            "# You can edit this file as needed.",
            "",
            # We always include pytest so the runtime can run tests for the converted implementation.
            "pytest",
        ]
        requirements_content = "\n".join(requirements_lines) + "\n"

        try:
            await gh.create_or_update_file(
                repo=repo,
                path="requirements.txt",
                content=requirements_content,
                commit_message=(
                    f"Add auto-generated requirements.txt for behavior {behavior.id}"
                ),
            )
            notes_requirements = "requirements.txt auto-generated with `pytest`."
        except GitHubError as exc:
            # Don't fail conversion if this write fails; just record a warning in notes.
            notes_requirements = f"WARNING: failed to write requirements.txt: {exc}"

    # ---- DB record for target implementation ----
    now = datetime.utcnow()
    notes_lines = [
        "# Conversion result",
        "",
        f"- Behavior: {behavior.id} ({behavior.name})",
        f"- Source language: {source_adapter.name}",
        f"- Target language: {target_adapter.name}",
        f"- Source implementation id: {source_impl.id}",
        f"- Target repo (HTML): {repo.html_url}",
        f"- Target repo (clone): {repo.clone_url}",
        f"- Target path: {target_path}",
        f"- File URL: {file_url}",
        f"- AI note: {ai_note}",
    ]
    if ai_error_message:
        notes_lines.append(f"- AI error: {ai_error_message}")
    if contract:
        notes_lines.append(f"- Contract id: {contract.id} (version {contract.version})")
    else:
        notes_lines.append("- Contract: none (conversion without explicit contract)")

    if notes_requirements:
        notes_lines.append(f"- {notes_requirements}")

    notes_md = "\n".join(notes_lines)

    # IMPORTANT: repo_url is the clone URL, good for Podman/CI.
    target_impl = BehaviorImplementation(
        behavior_id=behavior.id,
        language=target_language,
        repo_url=repo.clone_url,
        revision=repo.default_branch,  # rough stand-in; later track commits
        file_path=target_path,
        status="candidate",
        notes=notes_md,
        created_at=now,
        updated_at=now,
    )

    db.add(target_impl)
    db.commit()
    db.refresh(target_impl)

    return target_impl
