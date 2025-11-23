from typing import Optional

from pydantic import BaseModel

from app.schemas.implementation import BehaviorImplementationRead


class ConversionRequest(BaseModel):
    behavior_id: int
    source_language: str
    target_language: str
    contract_id: Optional[int] = None

    # Optional: name of the GitHub repo for converted code.
    # If omitted, backend will derive one from behavior name + target language.
    target_repo_name: Optional[str] = None


class ConversionResponse(BaseModel):
    behavior_id: int
    source_language: str
    target_language: str
    contract_id: Optional[int] = None
    target_repo_name: str
    target_repo_url: str
    implementation: BehaviorImplementationRead
