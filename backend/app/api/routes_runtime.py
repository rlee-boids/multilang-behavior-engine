from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models.behavior_implementation import BehaviorImplementation
from app.schemas.runtime import (
    TestImplementationRequest,
    TestImplementationResponse,
    DeployRequest,
    DeployResponse,
)
from app.services.podman_runtime import (
    run_tests_for_implementation,
    deploy_service_from_repo,
    PodmanRuntimeError,
)


router = APIRouter(prefix="/runtime", tags=["runtime"])


@router.post("/test-implementation", response_model=TestImplementationResponse)
def test_implementation(
    payload: TestImplementationRequest,
    db: Session = Depends(get_db),
):
    """
    Run build + unit tests for a given BehaviorImplementation inside a Podman container.

    This is a one-shot ephemeral run:
      - clone repo at implementation.revision
      - mount into container
      - run adapter.build_command && adapter.test_command
      - remove container when done
    """
    try:
        result = run_tests_for_implementation(db=db, implementation_id=payload.implementation_id)
    except PodmanRuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Runtime test failed: {exc}")

    return TestImplementationResponse(
        implementation_id=payload.implementation_id,
        exit_code=result.exit_code,
        stdout=result.stdout,
        stderr=result.stderr,
        container_image=result.container_image,
        elapsed_seconds=result.elapsed_seconds,
    )


@router.post("/deploy", response_model=DeployResponse)
def deploy_service(
    payload: DeployRequest,
    db: Session = Depends(get_db),
):
    """
    Deploy a service container using Podman.

    You can either:
      - Provide implementation_id, and we infer repo_url, revision, language
      - OR provide repo_url + revision + language directly
    """
    repo_url: str
    revision: str
    language: str

    if payload.implementation_id is not None:
        impl = db.get(BehaviorImplementation, payload.implementation_id)
        if impl is None:
            raise HTTPException(
                status_code=404,
                detail=f"BehaviorImplementation {payload.implementation_id} not found",
            )
        if not impl.repo_url or not impl.revision:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"BehaviorImplementation {payload.implementation_id} "
                    f"is missing repo_url or revision"
                ),
            )
        repo_url = impl.repo_url
        revision = impl.revision
        language = impl.language
    else:
        # Raw mode
        if not payload.repo_url or not payload.language:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Either implementation_id or (repo_url + language) must be provided."
                ),
            )
        repo_url = payload.repo_url
        revision = payload.revision or "main"
        language = payload.language

    try:
        result = deploy_service_from_repo(
            repo_url=repo_url,
            revision=revision,
            language=language,
            command_override=payload.command_override,
            host_port=payload.host_port,
            container_port=payload.container_port,
        )
    except PodmanRuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Deploy failed: {exc}")

    if not result.container_id:
        raise HTTPException(
            status_code=500,
            detail="Deploy returned no container_id (unexpected)",
        )

    return DeployResponse(
        container_id=result.container_id,
        container_image=result.container_image,
        repo_url=repo_url,
        revision=revision,
        language=language,
        elapsed_seconds=result.elapsed_seconds,
    )
