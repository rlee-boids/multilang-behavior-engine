from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.conversion import ConversionRequest, ConversionResponse
from app.schemas.implementation import BehaviorImplementationRead
from app.services.conversion_engine import convert_behavior_stub, ConversionError

router = APIRouter(prefix="/conversion", tags=["conversion"])


@router.post("/convert", response_model=ConversionResponse)
async def convert_behavior(request: ConversionRequest, db: Session = Depends(get_db)):
    """
    Conversion API stub with GitHub publishing.

    Called by the UI when the user selects a target language and clicks 'Convert'.
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

    # impl.repo_url is now the clone URL (e.g. https://github.com/owner/repo.git)
    raw_name = ""
    if impl.repo_url:
        last = impl.repo_url.rstrip("/").split("/")[-1]
        raw_name = last[:-4] if last.endswith(".git") else last

    return ConversionResponse(
        behavior_id=request.behavior_id,
        source_language=request.source_language,
        target_language=request.target_language,
        contract_id=request.contract_id,
        target_repo_name=raw_name,
        target_repo_url=impl.repo_url or "",
        implementation=BehaviorImplementationRead.model_validate(impl),
    )
