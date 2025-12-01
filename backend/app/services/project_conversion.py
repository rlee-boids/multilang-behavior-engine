from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import List, Optional

from sqlalchemy.orm import Session

from app.models.behavior_implementation import BehaviorImplementation
from app.services.conversion_engine import (
    convert_behavior_stub,
    ConversionError,
)
from app.services.converted_tests_builder import (
    build_converted_tests_for_implementation,
    ConvertedTestsError,
)


class ProjectConversionError(Exception):
    pass


@dataclass
class ProjectConversionResult:
    source_repo_url: str
    source_language: str
    target_language: str
    target_repo_name: str
    target_repo_url: str
    implementations: List[BehaviorImplementation]


def _derive_default_target_repo_name(source_repo_url: str, target_language: str) -> str:
    """
    Given a source repo URL like:
      https://github.com/rlee-boids/perl-plot-project.git

    Derive something like:
      perl-plot-project-python

    NOTE: We *do not* add the GITHUB_REPO_PREFIX here; that is only used when
    the per-behavior conversion logic is deriving its own name. For project
    conversion we pass the final name through as-is.
    """
    # Grab last path segment
    path = PurePosixPath(source_repo_url)
    base = path.name  # e.g. "perl-plot-project.git" or "perl-plot-project"
    if base.endswith(".git"):
        base = base[:-4]
    if not base:
        base = "converted-project"
    return f"{base}-{target_language.lower()}"


def _find_source_behavior_ids_for_repo(
    db: Session,
    source_repo_url: str,
    source_language: str,
) -> List[int]:
    """
    Find all behavior_ids for implementations that:

      - live in the given source_repo_url
      - are in the given source_language
      - have a 'source-like' status (source/validated/converted)

    We deduplicate by behavior_id and keep the most recent implementation
    per behavior where there are multiples.
    """
    q = (
        db.query(BehaviorImplementation)
        .filter(
            BehaviorImplementation.repo_url == source_repo_url,
            BehaviorImplementation.language == source_language,
            BehaviorImplementation.status.in_(["source", "validated", "converted"]),
        )
        .order_by(
            BehaviorImplementation.behavior_id.asc(),
            BehaviorImplementation.created_at.desc(),
        )
    )

    behavior_ids: List[int] = []
    seen: set[int] = set()
    for impl in q:
        if impl.behavior_id in seen:
            continue
        seen.add(impl.behavior_id)
        behavior_ids.append(impl.behavior_id)

    return behavior_ids


async def convert_project(
    db: Session,
    source_repo_url: str,
    source_language: str,
    target_language: str,
    contract_id: Optional[int] = None,
    target_repo_name: Optional[str] = None,
) -> ProjectConversionResult:
    """
    Whole-project conversion:

    1. Discover all behaviors that have a source implementation in the given
       repo + language.
    2. For each behavior_id:
         - call convert_behavior_stub (which writes to GitHub)
         - immediately call build_converted_tests_for_implementation so that
           unit tests are generated in the same repo for the new code.
    3. Return a single target repo (shared across all behaviors) and a list
       of the final BehaviorImplementation rows (post-test-generation).

    NOTE:
      - We deliberately reuse the single-behavior conversion + converted-tests
        builder to keep logic consistent.
      - In the future, if you plug in AI codegen, both code and tests can be
        generated from a *single* AI call inside convert_behavior_stub or a
        sibling function, but this orchestration layer stays the same.
    """
    if source_language == target_language:
        raise ProjectConversionError("Source and target language must be different")

    behavior_ids = _find_source_behavior_ids_for_repo(
        db=db,
        source_repo_url=source_repo_url,
        source_language=source_language,
    )
    if not behavior_ids:
        raise ProjectConversionError(
            f"No source implementations found for repo '{source_repo_url}' "
            f"in language '{source_language}'"
        )

    # Derive a default target repo name if not explicitly provided.
    effective_target_repo_name = target_repo_name or _derive_default_target_repo_name(
        source_repo_url=source_repo_url,
        target_language=target_language,
    )

    converted_impls: List[BehaviorImplementation] = []
    target_repo_url: Optional[str] = None

    for behavior_id in behavior_ids:
        # --- Step 1: per-behavior conversion ---
        try:
            impl = await convert_behavior_stub(
                db=db,
                behavior_id=behavior_id,
                source_language=source_language,
                target_language=target_language,
                contract_id=contract_id,
                target_repo_name=effective_target_repo_name,
            )
        except ConversionError as exc:
            raise ProjectConversionError(
                f"Conversion failed for behavior {behavior_id}: {exc}"
            ) from exc

        # Capture repo_url from the first converted implementation
        if target_repo_url is None and impl.repo_url:
            target_repo_url = impl.repo_url

        # --- Step 2: generate tests in the *same* repo ---
        try:
            impl_with_tests = await build_converted_tests_for_implementation(
                db=db,
                implementation_id=impl.id,
                contract_id=contract_id,
            )
        except ConvertedTestsError as exc:
            # If test generation fails, we still surface the error and abort.
            raise ProjectConversionError(
                f"Converted tests generation failed for behavior {behavior_id}, "
                f"implementation {impl.id}: {exc}"
            ) from exc

        converted_impls.append(impl_with_tests)

    if target_repo_url is None:
        # This should not happen if at least one impl has repo_url set.
        raise ProjectConversionError(
            "Project conversion completed but no target_repo_url was recorded."
        )

    return ProjectConversionResult(
        source_repo_url=source_repo_url,
        source_language=source_language,
        target_language=target_language,
        target_repo_name=effective_target_repo_name,
        target_repo_url=target_repo_url,
        implementations=converted_impls,
    )
