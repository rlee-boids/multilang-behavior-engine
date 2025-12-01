from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.implementation import BehaviorImplementationRead
from app.schemas.runtime import (
    TestImplementationRequest,
    TestImplementationResponse,
    BuildLegacyHarnessRequest,
    BuildLegacyHarnessResponse,
    RunLegacyWithHarnessRequest,
    RunLegacyWithHarnessResponse,
    BuildConvertedTestsRequest,
    BuildConvertedTestsResponse,
    DeployServiceRequest,
    DeployServiceResponse,
)
from app.services.podman_runner import (
    run_tests_for_implementation,
    run_legacy_with_harness,
    PodmanRuntimeError,
)
from app.services.test_harness_builder import (
    build_legacy_test_harness,
    TestHarnessError,
)
from app.services.converted_tests_builder import (
    build_converted_tests_for_implementation,
    ConvertedTestsError,
)
from app.services.service_deployer import (
    deploy_behavior_service,
    ServiceDeploymentError,
)

router = APIRouter(prefix="/runtime", tags=["runtime"])


# ---------- Test a single implementation in Podman ----------


@router.post("/test-implementation", response_model=TestImplementationResponse)
async def test_implementation(
    req: TestImplementationRequest,
    db: Session = Depends(get_db),
):
    """
    Run tests for a single BehaviorImplementation in an ephemeral Podman container.

    Uses the language adapter's container image + build_command + test_command.
    """
    try:
        result = await run_tests_for_implementation(db=db, implementation_id=req.implementation_id)
    except PodmanRuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Runtime test failed: {exc}")

    return TestImplementationResponse(
        implementation_id=req.implementation_id,
        exit_code=result.exit_code,
        stdout=result.stdout,
        stderr=result.stderr,
        container_image=result.container_image,
        elapsed_seconds=result.elapsed_seconds,
    )


# ---------- Build legacy harness repo (separate tests repo) ----------


@router.post("/build-legacy-harness", response_model=BuildLegacyHarnessResponse)
async def build_legacy_harness(
    req: BuildLegacyHarnessRequest,
    db: Session = Depends(get_db),
):
    """
    Build a separate GitHub repo containing test harness code for a legacy implementation.
    """
    try:
        harness_impl = await build_legacy_test_harness(
            db=db,
            behavior_id=req.behavior_id,
            language=req.language,
            contract_id=req.contract_id,
            target_repo_name=req.target_repo_name,
        )
    except TestHarnessError as exc:
        raise HTTPException(status_code=500, detail=f"Harness build failed: {exc}")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Harness build failed: {exc}")

    return BuildLegacyHarnessResponse(
        harness=BehaviorImplementationRead.model_validate(harness_impl)
    )


# ---------- Run legacy + harness together in Podman ----------


@router.post("/run-legacy-with-harness", response_model=RunLegacyWithHarnessResponse)
async def run_legacy_with_harness_route(
    req: RunLegacyWithHarnessRequest,
    db: Session = Depends(get_db),
):
    """
    Run legacy code + harness tests inside a paired container setup.

    Layout inside the container:
      /code  -> legacy repo
      /tests -> harness repo (working dir)
    """
    try:
        result = await run_legacy_with_harness(
            db=db,
            legacy_implementation_id=req.legacy_implementation_id,
            harness_implementation_id=req.harness_implementation_id,
            behavior_id=req.behavior_id,
            contract_id=req.contract_id,
        )
    except PodmanRuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Runtime test failed: {exc}")

    return RunLegacyWithHarnessResponse(
        legacy_implementation_id=req.legacy_implementation_id,
        harness_implementation_id=req.harness_implementation_id,
        exit_code=result.exit_code,
        stdout=result.stdout,
        stderr=result.stderr,
        container_image=result.container_image,
        elapsed_seconds=result.elapsed_seconds,
    )


# ---------- Build converted tests inside converted repo ----------


@router.post("/build-converted-tests", response_model=BuildConvertedTestsResponse)
async def build_converted_tests(
    req: BuildConvertedTestsRequest,
    db: Session = Depends(get_db),
):
    """
    Generate contract-driven tests directly in the converted implementation repo.

    Typical flow:
      1. User selects a converted implementation and a contract in the UI.
      2. UI calls this endpoint with implementation_id (+ optional contract_id).
      3. Backend:
         - generates pytest tests via LanguageAdapter.generate_test_code_from_contract()
         - pushes them to the same GitHub repo as the converted code
         - updates the BehaviorImplementation.notes
      4. UI can then call /runtime/test-implementation to run these tests in Podman.
    """
    try:
        impl = await build_converted_tests_for_implementation(
            db=db,
            implementation_id=req.implementation_id,
            contract_id=req.contract_id,
        )
    except ConvertedTestsError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Build converted tests failed: {exc}")

    return BuildConvertedTestsResponse(
        implementation=BehaviorImplementationRead.model_validate(impl)
    )


# ---------- Deploy a behavior implementation as a service ----------


@router.post("/deploy-service", response_model=DeployServiceResponse)
async def deploy_service(
    req: DeployServiceRequest,
    db: Session = Depends(get_db),
):
    """
    Build & run a containerized service for a UI-style BehaviorImplementation.

    For now this supports:
      - Perl CGI UI (cgi-bin/plot_ui.cgi) wrapped as PSGI via Plack.
    """
    try:
        result = await deploy_behavior_service(
            db=db,
            implementation_id=req.implementation_id,
            # host_port is optional; let the service choose default (18000 + impl_id)
            # If you later extend DeployServiceRequest with host_port, you can pass it here.
        )
    except ServiceDeploymentError as exc:
        raise HTTPException(status_code=500, detail=f"Service deployment failed: {exc}")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Service deployment failed: {exc}")

    return DeployServiceResponse(
        implementation_id=result.implementation_id,
        image=result.image,
        container_name=result.container_name,
        internal_port=result.internal_port,
        host_port=result.host_port,
        url=result.url,
        build_stdout=result.build_stdout,
        build_stderr=result.build_stderr,
        run_stdout=result.run_stdout,
        run_stderr=result.run_stderr,
    )