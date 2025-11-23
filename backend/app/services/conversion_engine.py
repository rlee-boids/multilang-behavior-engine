from __future__ import annotations

from datetime import datetime
from pathlib import PurePosixPath
from typing import Optional

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
    # e.g. multilang-converted-generate_plot-python
    base = behavior.name.replace(" ", "_").lower()
    return f"{settings.GITHUB_REPO_PREFIX}{base}-{target_language.lower()}"


def _map_file_path_for_target_language(
    source_path: str,
    source_language: str,
    target_language: str,
) -> str:
    """
    Keep source-relative path but adjust extension for the target language
    where it's obvious (e.g. .pl -> .py).
    """
    p = PurePosixPath(source_path)

    # Simple extension mapping; we'll expand later as we add adapters.
    ext_map = {
        ("perl", "python"): ".py",
        ("perl", "perl"): ".pl",
        ("python", "perl"): ".pl",
    }

    new_suffix = ext_map.get((source_language, target_language))
    if new_suffix is None:
        # Fallback: keep original suffix
        return str(p)
    return str(p.with_suffix(new_suffix))


async def convert_behavior_stub(
    db: Session,
    behavior_id: int,
    source_language: str,
    target_language: str,
    contract_id: Optional[int] = None,
    target_repo_name: Optional[str] = None,
) -> BehaviorImplementation:
    """
    Conversion with GitHub integration + AI codegen.

    It:
      - Ensures the behavior exists
      - Ensures we have a source implementation with repo_url/file_path/revision
      - Ensures we have adapters for both languages
      - Optionally looks up a contract
      - Ensures a GitHub repo exists for converted code
      - Uses AI to generate target-language code from the source file
      - Falls back to a placeholder stub if AI fails
      - Writes the file to the target repo
      - Creates a new BehaviorImplementation row with status='candidate'
        and repo_url pointing to the Git clone URL (good for Podman).
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
        ai_error_message = None
    except AIConversionError as exc:
        # Fall back to placeholder but annotate what failed
        ai_note = "AI conversion FAILED, placeholder stub written instead"
        ai_error_message = str(exc)
        code_to_write = placeholder_code + f"\n# AI conversion error: {exc}\n"

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
