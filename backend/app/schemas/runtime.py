from __future__ import annotations

from typing import Optional, Literal

from pydantic import BaseModel, Field


class TestImplementationRequest(BaseModel):
    implementation_id: int = Field(
        ...,
        description="ID of BehaviorImplementation to test in a Podman container.",
    )


class TestImplementationResponse(BaseModel):
    implementation_id: int
    exit_code: int
    stdout: str
    stderr: str
    container_image: str
    elapsed_seconds: float


class DeployRequest(BaseModel):
    # Option A: use an existing BehaviorImplementation id
    implementation_id: Optional[int] = Field(
        default=None,
        description=(
            "If provided, repo_url / revision / language are inferred from this "
            "BehaviorImplementation."
        ),
    )

    # Option B: raw GitHub info
    repo_url: Optional[str] = Field(
        default=None,
        description="Git clone URL for the repo (if not using implementation_id).",
    )
    revision: Optional[str] = Field(
        default="main",
        description="Git branch or revision to deploy.",
    )
    language: Optional[str] = Field(
        default=None,
        description="Language for selecting the adapter (if not using implementation_id).",
    )

    command_override: Optional[str] = Field(
        default=None,
        description="Optional shell command to run as the service, overrides adapter default.",
    )
    host_port: Optional[int] = Field(
        default=None,
        description="Host port to expose (optional).",
    )
    container_port: Optional[int] = Field(
        default=None,
        description="Container service port (optional).",
    )


class DeployResponse(BaseModel):
    container_id: str
    container_image: str
    repo_url: str
    revision: str
    language: str
    elapsed_seconds: float
