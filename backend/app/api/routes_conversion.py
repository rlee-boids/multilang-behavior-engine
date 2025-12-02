from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.conversion import (
    ConversionRequest,
    ConversionResponse,
    ProjectConversionRequest,
    ProjectConversionResponse,
)
from app.schemas.implementation import BehaviorImplementationRead
from app.services.conversion_engine import (
    convert_behavior_stub,
    convert_full_project,
    ConversionError,
)
from app.services.converted_tests_builder import (
    build_converted_tests_for_implementation,
    ConvertedTestsError,
)
from app.services.project_conversion import (
    convert_project,
    ProjectConversionError,
)

router = APIRouter(prefix="/conversion", tags=["conversion"])


# ---------- Single-behavior conversion (existing) ----------


@router.post("/convert", response_model=ConversionResponse)
async def convert_behavior(request: ConversionRequest, db: Session = Depends(get_db)):
    """
    Conversion API stub with GitHub publishing.

    Called by the UI when the user selects a target language and clicks 'Convert'
    for a *single* behavior.
    """
    try:
        impl = await convert_behavior_stub(
            db=db,
            behavior_id=request.behavior_id,
            source_language=request.source_language,
            target_language=request.target_language,
            contract_id=request.contract_id,
            target_repo_name=request.target_repo_name,
        )
    except ConversionError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Conversion failed: {exc}")

    return ConversionResponse(
        behavior_id=request.behavior_id,
        source_language=request.source_language,
        target_language=request.target_language,
        contract_id=request.contract_id,
        target_repo_name=impl.repo_url.split("/")[-1] if impl.repo_url else "",
        target_repo_url=impl.repo_url or "",
        implementation=BehaviorImplementationRead.model_validate(impl),
    )


# ---------- Build converted tests for a single implementation (existing) ----------


@router.post("/build-converted-tests", response_model=BehaviorImplementationRead)
async def build_converted_tests_endpoint(
    implementation_id: int,
    contract_id: int | None = None,
    db: Session = Depends(get_db),
):
    """
    Generate contract-driven tests directly in the converted implementation repo
    for a *single* implementation.

    NOTE:
      - For whole-project conversions, you should prefer /convert-project,
        which does conversion + test generation in a single flow.
    """
    try:
        impl = await build_converted_tests_for_implementation(
            db=db,
            implementation_id=implementation_id,
            contract_id=contract_id,
        )
    except ConvertedTestsError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Build converted tests failed: {exc}")

    return BehaviorImplementationRead.model_validate(impl)


# ---------- NEW: Whole-project conversion (convert + tests in one flow) ----------



@router.post("/convert-project", response_model=ProjectConversionResponse)
async def convert_project_route(
    request: ProjectConversionRequest,
    db: Session = Depends(get_db),
):
    """
    Convert an entire legacy repo into a target language, and generate tests
    for each converted implementation *within the same process*.

    Flow:
      1. Discover all behaviors whose source implementations live in
         `request.source_repo_url` and `request.source_language`.
      2. For each behavior_id:
           - call convert_behavior_stub()
           - call build_converted_tests_for_implementation()
      3. Return a single target repo + list of converted implementations.

    This is the "Option 2" repo-centric conversion you asked for.
    """
    try:
        result = await convert_project(
            db=db,
            source_repo_url=request.source_repo_url,
            source_language=request.source_language,
            target_language=request.target_language,
            contract_id=request.contract_id,
            target_repo_name=request.target_repo_name,
        )
    except ProjectConversionError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Project conversion failed: {exc}")

    impl_reads = [
        BehaviorImplementationRead.model_validate(impl)
        for impl in result.implementations
    ]

    return ProjectConversionResponse(
        source_repo_url=result.source_repo_url,
        source_language=result.source_language,
        target_language=result.target_language,
        target_repo_name=result.target_repo_name,
        target_repo_url=result.target_repo_url,
        implementations=impl_reads,
    )
