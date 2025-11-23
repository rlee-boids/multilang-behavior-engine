from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.analyzer import (
    AnalyzeRepoRequest,
    AnalyzeRepoResponse,
    AnalyzeRepoFileResult,
)
from app.services.repo_analyzer import analyze_repository, RepoAnalysisError

router = APIRouter(prefix="/analyzer", tags=["analyzer"])


@router.post("/analyze-repo", response_model=AnalyzeRepoResponse)
async def analyze_repo_endpoint(
    payload: AnalyzeRepoRequest,
    db: Session = Depends(get_db),
):
    """
    Scan a legacy GitHub repo and feed each source file to the AI analyzer.

    - Clones repo_url (optionally at revision)
    - Walks files using LanguageAdapter for `language`
    - For each file:
      - summarize_code
      - suggest_contract
      - writes CodeKnowledge + Behavior + BehaviorImplementation (status='source')
    """
    try:
        results = await analyze_repository(
            db=db,
            repo_url=str(payload.repo_url),
            language=payload.language,
            revision=payload.revision,
            max_files=payload.max_files,
            behavior_domain=payload.behavior_domain,
        )
    except RepoAnalysisError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Analysis failed: {exc}")

    return AnalyzeRepoResponse(
        repo_url=str(payload.repo_url),
        language=payload.language,
        revision=payload.revision,
        analyzed_files=[
            AnalyzeRepoFileResult(
                file_path=r.file_path,
                code_knowledge_id=r.code_knowledge_id,
                behavior_id=r.behavior_id,
                implementation_id=r.implementation_id,
            )
            for r in results
        ],
    )
