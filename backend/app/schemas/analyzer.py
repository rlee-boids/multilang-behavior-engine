from typing import List, Optional

from pydantic import BaseModel, HttpUrl


class AnalyzeRepoRequest(BaseModel):
    repo_url: HttpUrl | str
    language: str
    revision: Optional[str] = None
    max_files: int = 50
    behavior_domain: Optional[str] = None


class AnalyzeRepoFileResult(BaseModel):
    file_path: str
    code_knowledge_id: int
    behavior_id: int
    implementation_id: int


class AnalyzeRepoResponse(BaseModel):
    repo_url: str
    language: str
    revision: Optional[str]
    analyzed_files: List[AnalyzeRepoFileResult]
