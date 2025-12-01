from typing import Optional, List

from pydantic import BaseModel

from app.schemas.implementation import BehaviorImplementationRead


class ConversionRequest(BaseModel):
    """
    Single-behavior conversion (existing).
    """
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


class ProjectConversionRequest(BaseModel):
    """
    Request to convert an entire legacy repo into a target language.

    This is repo-centric rather than behavior-centric.

    Typical use:
      - source_repo_url: "https://github.com/rlee-boids/perl-plot-project.git"
      - source_language: "perl"
      - target_language: "python"
      - target_repo_name: (optional; if omitted, backend derives something
        like "perl-plot-project-python")
    """
    source_repo_url: str
    source_language: str
    target_language: str
    contract_id: Optional[int] = None

    # Optional: explicit name for the target repo.
    # If omitted, backend derives from source_repo_url + target_language.
    target_repo_name: Optional[str] = None


class ProjectConversionResponse(BaseModel):
    """
    Response after converting a whole project.

    - All relevant behaviors for the source repo/language are converted.
    - Each converted implementation has tests generated in the *same* repo.
    """
    source_repo_url: str
    source_language: str
    target_language: str
    target_repo_name: str
    target_repo_url: str
    implementations: List[BehaviorImplementationRead]
