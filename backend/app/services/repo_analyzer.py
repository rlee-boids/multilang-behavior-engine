from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Set

from sqlalchemy.orm import Session

from app.adapters import get_adapter
from app.core.config import settings
from app.models.behavior import Behavior
from app.models.code_knowledge import CodeKnowledge
from app.models.behavior_implementation import BehaviorImplementation
from app.services.ai_client import get_ai_client


class RepoAnalysisError(Exception):
    pass


@dataclass
class AnalyzedFileResult:
    file_path: str
    code_knowledge_id: int
    behavior_id: int
    implementation_id: int


def _ensure_workspace_root() -> str:
    root = os.path.abspath(settings.ANALYZER_WORKSPACE_ROOT)
    os.makedirs(root, exist_ok=True)
    return root


def _clone_repo_to_temp(repo_url: str, revision: Optional[str] = None) -> str:
    """
    Clone repo into a fresh temp directory under ANALYZER_WORKSPACE_ROOT.
    For now we do a shallow clone (--depth=1).
    """
    workspace = _ensure_workspace_root()
    temp_dir = tempfile.mkdtemp(prefix="repo_", dir=workspace)

    cmd = ["git", "clone", "--depth", "1", repo_url, temp_dir]
    if revision:
        cmd = ["git", "clone", "--depth", "1", "--branch", revision, repo_url, temp_dir]

    try:
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as exc:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise RepoAnalysisError(
            f"Failed to clone repo {repo_url} at {revision or 'default'}: "
            f"{exc.stderr.decode('utf-8', errors='ignore')}"
        )

    return temp_dir


def _iter_language_files(root: str, language: str) -> List[str]:
    """
    Walk the repo and yield all files that match the adapter's file_extensions.
    """
    adapter = get_adapter(language)
    exts = set(adapter.file_extensions)

    results: List[str] = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for fn in filenames:
            p = Path(dirpath, fn)
            if p.suffix in exts:
                # Store repo-relative paths
                rel = os.path.relpath(p, root)
                results.append(rel)
    return sorted(results)


def _upsert_behavior(
    db: Session,
    behavior_name: str,
    behavior_domain: Optional[str],
    language: str,
    rel_path: str,
) -> Behavior:
    """
    Ensure a Behavior row exists for this unit (file).

    Identity: Behavior.name (e.g. "perl:lib/Plot/Generator.pm")
    """
    behavior = (
        db.query(Behavior)
        .filter(Behavior.name == behavior_name)
        .one_or_none()
    )
    if not behavior:
        behavior = Behavior(
            name=behavior_name,
            description=f"Behavior extracted from {rel_path}",
            domain=behavior_domain,
            tags=[language, "analyzed"],
        )
        db.add(behavior)
        db.flush()
        return behavior

    # Existing behavior: update domain / tags if we have more info
    if behavior_domain and behavior.domain != behavior_domain:
        behavior.domain = behavior_domain
    if language not in (behavior.tags or []):
        behavior.tags = list(set((behavior.tags or []) + [language, "analyzed"]))
    return behavior


def _upsert_code_knowledge(
    db: Session,
    *,
    repo_url: str,
    revision: Optional[str],
    rel_path: str,
    language: str,
    behavior: Behavior,
    summary: str,
    contract_text: str,
    behavior_domain: Optional[str],
) -> CodeKnowledge:
    """
    Upsert CodeKnowledge by (repo_url, language, file_path).

    If an entry already exists for this triple, we update it.
    Otherwise we create a new one.
    """
    ck = (
        db.query(CodeKnowledge)
        .filter(
            CodeKnowledge.repo_url == repo_url,
            CodeKnowledge.language == language,
            CodeKnowledge.file_path == rel_path,
            CodeKnowledge.is_archived == False,  # noqa: E712
        )
        .order_by(CodeKnowledge.created_at.desc())
        .first()
    )

    if ck:
        ck.revision = revision
        ck.behavior_id = behavior.id
        ck.symbol_name = rel_path
        ck.symbol_kind = "file"
        ck.summary = summary
        ck.raw_excerpt = ck.raw_excerpt or ""
        ck.io_description = {"raw_text": contract_text}
        ck.details_md = contract_text
        ck.short_summary = summary[:200]
        ck.domain = behavior_domain
        # tags: ensure language/analyzed are present
        tags = set(ck.tags or [])
        tags.update([language, "analyzed"])
        ck.tags = list(tags)
        return ck

    ck = CodeKnowledge(
        repo_url=repo_url,
        revision=revision,
        file_path=rel_path,
        language=language,
        behavior_id=behavior.id,
        symbol_name=rel_path,
        symbol_kind="file",
        summary=summary,
        raw_excerpt=summary[:2000],
        io_description={"raw_text": contract_text},
        dependencies=None,
        title=rel_path,
        short_summary=summary[:200],
        details_md=contract_text,
        tags=[language, "analyzed"],
        audience="internal",
        domain=behavior_domain,
        analyzer_name="multilang-analyzer",
        analyzer_version="0.1.0",
        model_name=str(settings.AI_PROVIDER),
        created_by="system",
        last_edited_by="system",
        is_human_reviewed=False,
        is_archived=False,
    )
    db.add(ck)
    db.flush()
    return ck


def _upsert_behavior_implementation(
    db: Session,
    *,
    behavior: Behavior,
    language: str,
    repo_url: str,
    revision: Optional[str],
    rel_path: str,
) -> BehaviorImplementation:
    """
    Upsert BehaviorImplementation for the *source* implementation.

    Identity: (behavior_id, language, repo_url, file_path)
    """
    impl = (
        db.query(BehaviorImplementation)
        .filter(
            BehaviorImplementation.behavior_id == behavior.id,
            BehaviorImplementation.language == language,
            BehaviorImplementation.repo_url == repo_url,
            BehaviorImplementation.file_path == rel_path,
        )
        .order_by(BehaviorImplementation.created_at.desc())
        .first()
    )

    if impl:
        impl.revision = revision
        impl.status = impl.status or "source"
        if not impl.notes:
            impl.notes = "Source implementation discovered during repo analysis."
        return impl

    impl = BehaviorImplementation(
        behavior_id=behavior.id,
        language=language,
        repo_url=repo_url,
        revision=revision,
        file_path=rel_path,
        status="source",
        notes=f"Source implementation discovered during repo analysis for {repo_url}",
    )
    db.add(impl)
    db.flush()
    return impl


def _archive_missing_for_repo(
    db: Session,
    *,
    repo_url: str,
    language: str,
    current_files: Set[str],
) -> None:
    """
    Archive CodeKnowledge + BehaviorImplementation entries for files that used to
    exist for this repo+language but are no longer present in the current scan.

    - CodeKnowledge.is_archived = True
    - BehaviorImplementation.status = "archived"
    """
    # All non-archived CodeKnowledge for this repo+language
    existing_cks = (
        db.query(CodeKnowledge)
        .filter(
            CodeKnowledge.repo_url == repo_url,
            CodeKnowledge.language == language,
            CodeKnowledge.is_archived == False,  # noqa: E712
        )
        .all()
    )

    existing_paths: Set[str] = {ck.file_path for ck in existing_cks if ck.file_path}

    missing_paths = existing_paths - current_files
    if not missing_paths:
        return

    # Archive CodeKnowledge rows
    for ck in existing_cks:
        if ck.file_path in missing_paths:
            ck.is_archived = True

    # Archive BehaviorImplementations for those paths
    impls = (
        db.query(BehaviorImplementation)
        .filter(
            BehaviorImplementation.repo_url == repo_url,
            BehaviorImplementation.language == language,
            BehaviorImplementation.file_path.in_(list(missing_paths)),
        )
        .all()
    )
    for impl in impls:
        impl.status = "archived"

    db.commit()


async def analyze_repository(
    db: Session,
    repo_url: str,
    language: str,
    revision: Optional[str] = None,
    max_files: int = 50,
    behavior_domain: Optional[str] = None,
) -> List[AnalyzedFileResult]:
    """
    High-level pipeline:
      1. Clone repo
      2. Find language-specific source files via LanguageAdapter
      3. For each file (up to max_files):
         - Read file (bounded by MAX_ANALYZER_FILE_BYTES)
         - Ask AI for summary + contract-like description
         - Upsert Behavior
         - Upsert CodeKnowledge
         - Upsert BehaviorImplementation (status='source')
      4. Archive any previously-known files for this repo+language that are
         no longer present (mark CodeKnowledge.is_archived, Implementation.status='archived').
    """
    local_root = _clone_repo_to_temp(repo_url, revision)
    try:
        adapter = get_adapter(language)
    except KeyError:
        shutil.rmtree(local_root, ignore_errors=True)
        raise RepoAnalysisError(f"No LanguageAdapter registered for '{language}'")

    files = _iter_language_files(local_root, language)
    if not files:
        shutil.rmtree(local_root, ignore_errors=True)
        raise RepoAnalysisError(
            f"No {language} files found in repository {repo_url} at {revision or 'default'}"
        )

    ai = get_ai_client()
    results: List[AnalyzedFileResult] = []

    current_files: Set[str] = set()

    for rel_path in files[: max_files]:
        abs_path = os.path.join(local_root, rel_path)
        try:
            data = Path(abs_path).read_bytes()
        except OSError:
            # Skip unreadable files
            continue

        if len(data) > settings.MAX_ANALYZER_FILE_BYTES:
            # Skip extremely large files in v1
            continue

        code_text = data.decode("utf-8", errors="ignore")
        current_files.add(rel_path)

        # --- AI calls ---
        summary = await ai.summarize_code(code_text, language=language)
        contract_text = await ai.suggest_contract(code_text, language=language)

        # --- DB upserts ---
        behavior_name = f"{language}:{rel_path}"
        behavior = _upsert_behavior(
            db=db,
            behavior_name=behavior_name,
            behavior_domain=behavior_domain,
            language=language,
            rel_path=rel_path,
        )

        ck = _upsert_code_knowledge(
            db=db,
            repo_url=repo_url,
            revision=revision,
            rel_path=rel_path,
            language=language,
            behavior=behavior,
            summary=summary,
            contract_text=contract_text,
            behavior_domain=behavior_domain,
        )

        impl = _upsert_behavior_implementation(
            db=db,
            behavior=behavior,
            language=language,
            repo_url=repo_url,
            revision=revision,
            rel_path=rel_path,
        )

        db.commit()
        db.refresh(ck)
        db.refresh(behavior)
        db.refresh(impl)

        results.append(
            AnalyzedFileResult(
                file_path=rel_path,
                code_knowledge_id=ck.id,
                behavior_id=behavior.id,
                implementation_id=impl.id,
            )
        )

    # Archive any previously-known files that are now missing
    _archive_missing_for_repo(
        db=db,
        repo_url=repo_url,
        language=language,
        current_files=current_files,
    )

    shutil.rmtree(local_root, ignore_errors=True)
    return results
