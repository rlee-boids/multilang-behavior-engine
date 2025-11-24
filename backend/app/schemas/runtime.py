from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from app.schemas.implementation import BehaviorImplementationRead


# -------- Test a single implementation in Podman --------


class TestImplementationRequest(BaseModel):
    implementation_id: int


class TestImplementationResponse(BaseModel):
    implementation_id: int
    exit_code: int
    stdout: str
    stderr: str
    container_image: str
    elapsed_seconds: float


# -------- Build legacy harness repo (separate tests repo) --------


class BuildLegacyHarnessRequest(BaseModel):
    behavior_id: int
    language: str
    contract_id: Optional[int] = None
    target_repo_name: Optional[str] = None


class BuildLegacyHarnessResponse(BaseModel):
    harness: BehaviorImplementationRead


# -------- Run legacy code + harness together in Podman --------


class RunLegacyWithHarnessRequest(BaseModel):
    legacy_implementation_id: int
    harness_implementation_id: int
    # Optional override; normally inferred from implementations
    behavior_id: Optional[int] = None
    contract_id: Optional[int] = None


class RunLegacyWithHarnessResponse(BaseModel):
    legacy_implementation_id: int
    harness_implementation_id: int
    exit_code: int
    stdout: str
    stderr: str
    container_image: str
    elapsed_seconds: float


# -------- NEW: Build converted tests in the converted repo --------


class BuildConvertedTestsRequest(BaseModel):
    implementation_id: int
    contract_id: Optional[int] = None


class BuildConvertedTestsResponse(BaseModel):
    implementation: BehaviorImplementationRead
