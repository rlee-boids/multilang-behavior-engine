from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.ai_client import get_ai_client

router = APIRouter(prefix="/ai", tags=["ai"])


class AnalyzeCodeRequest(BaseModel):
    language: str = Field(..., description="Programming language of the code (e.g. perl, python).")
    code: str = Field(..., description="Source code to analyze.")
    mode: Literal["summary", "contract"] = Field(
        default="summary",
        description="Whether to generate a summary or a behavior contract suggestion.",
    )


class AnalyzeCodeResponse(BaseModel):
    provider: str
    mode: str
    language: str
    result: str


@router.post("/analyze", response_model=AnalyzeCodeResponse)
async def analyze_code(request: AnalyzeCodeRequest) -> AnalyzeCodeResponse:
    try:
        client = get_ai_client()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    if request.mode == "summary":
        text = await client.summarize_code(code=request.code, language=request.language)
    else:
        text = await client.suggest_contract(code=request.code, language=request.language)

    return AnalyzeCodeResponse(
        provider=str(type(client).__name__),
        mode=request.mode,
        language=request.language,
        result=text,
    )
