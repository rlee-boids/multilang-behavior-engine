# app/schemas/runtime.py
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from app.schemas.implementation import BehaviorImplementationRead


# ---------- Test a single implementation in Podman ----------


class TestImplementationRequest(BaseModel):
    implementation_id: int


class TestImplementationResponse(BaseModel):
    implementation_id: int
    exit_code: int
    stdout: str
    stderr: str
    container_image: str
    elapsed_seconds: float


# ---------- Build legacy harness repo (separate tests repo) ----------


class BuildLegacyHarnessRequest(BaseModel):
    behavior_id: int
    language: str
    contract_id: Optional[int] = None

    # Optional: explicit GitHub repo name for the harness.
    # If omitted, backend derives one from behavior + language.
    target_repo_name: Optional[str] = None


class BuildLegacyHarnessResponse(BaseModel):
    harness: BehaviorImplementationRead


# ---------- Run legacy + harness together in Podman ----------


class RunLegacyWithHarnessRequest(BaseModel):
    legacy_implementation_id: int
    harness_implementation_id: int
    behavior_id: int
    contract_id: Optional[int] = None


class RunLegacyWithHarnessResponse(BaseModel):
    legacy_implementation_id: int
    harness_implementation_id: int
    exit_code: int
    stdout: str
    stderr: str
    container_image: str
    elapsed_seconds: float


# ---------- Build converted tests inside converted repo ----------


class BuildConvertedTestsRequest(BaseModel):
    implementation_id: int
    contract_id: Optional[int] = None


class BuildConvertedTestsResponse(BaseModel):
    implementation: BehaviorImplementationRead


# ---------- Deploy a service container for an implementation ----------


class DeployServiceRequest(BaseModel):
    implementation_id: int


class DeployServiceResponse(BaseModel):
    implementation_id: int

    # Image + container details
    image: str
    container_name: str

    # Internal port inside container (e.g. 5000) and host-mapped port
    internal_port: int
    host_port: int

    # Convenience URL like "http://localhost:18015"
    url: str

    # Build + run logs from Podman
    build_stdout: str
    build_stderr: str
    run_stdout: str
    run_stderr: str
