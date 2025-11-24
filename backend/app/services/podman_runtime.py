from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from app.adapters import get_adapter
from app.core.config import settings
from app.models.behavior_implementation import BehaviorImplementation


class PodmanRuntimeError(Exception):
    pass


@dataclass
class PodmanRunResult:
    exit_code: int
    stdout: str
    stderr: str
    container_image: str
    elapsed_seconds: float
    container_id: Optional[str] = None  # for detached runs


def _ensure_runtime_binary() -> str:
    """
    Ensure the configured container runtime (podman by default) is available.
    """
    runtime = settings.CONTAINER_RUNTIME or "podman"
    if not shutil.which(runtime):
        raise PodmanRuntimeError(
            f"Container runtime '{runtime}' not found on PATH. "
            f"Please install it or adjust CONTAINER_RUNTIME."
        )
    return runtime


def _git_clone_repo(repo_url: str, revision: str, dest_dir: Path) -> None:
    """
    Clone a Git repo to a specific directory.
    """
    if not repo_url:
        raise PodmanRuntimeError("Repo URL is empty")

    if dest_dir.exists():
        shutil.rmtree(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        "git",
        "clone",
        "--branch",
        revision,
        "--single-branch",
        repo_url,
        str(dest_dir),
    ]
    try:
        subprocess.run(
            cmd,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        raise PodmanRuntimeError(
            f"Git clone failed for {repo_url}@{revision}: {exc.stderr}"
        ) from exc


def _clone_repo_to_workspace(
    repo_url: str,
    revision: str,
    workspace_root: Path,
    run_label: str,
) -> Path:
    """
    Clone a Git repo into workspace_root/runtime/<run_label>.
    """
    runtime_dir = workspace_root / "runtime"
    target_dir = runtime_dir / run_label
    _git_clone_repo(repo_url=repo_url, revision=revision, dest_dir=target_dir)
    return target_dir


def _build_test_shell_command(
    adapter,
    project_root: str,
) -> str:
    """
    Build a shell command string that runs 'build' then 'test' using the adapter.

    We keep it simple: `build && test`. If adapter has no separate build, we can
    just run test.
    """
    build_cmd = adapter.build_command(project_root)
    test_cmd = adapter.test_command(project_root)

    def _cmd_to_str(cmd) -> str:
        if isinstance(cmd, str):
            return cmd
        return " ".join(cmd)

    build_str = _cmd_to_str(build_cmd) if build_cmd else ""
    test_str = _cmd_to_str(test_cmd)

    if build_str:
        return f"{build_str} && {test_str}"
    return test_str


def run_tests_for_implementation(
    db: Session,
    implementation_id: int,
) -> PodmanRunResult:
    """
    Single-repo test runner:

    - Load BehaviorImplementation from DB
    - Get appropriate LanguageAdapter
    - Clone repo into a temporary workspace
    - Run build+test inside a Podman container
    - Return stdout/stderr/exit_code
    """
    runtime_bin = _ensure_runtime_binary()

    impl: BehaviorImplementation | None = db.get(BehaviorImplementation, implementation_id)
    if impl is None:
        raise PodmanRuntimeError(f"BehaviorImplementation {implementation_id} not found")

    if not impl.repo_url or not impl.revision:
        raise PodmanRuntimeError(
            f"BehaviorImplementation {implementation_id} missing repo_url or revision"
        )

    try:
        adapter = get_adapter(impl.language)
    except KeyError:
        raise PodmanRuntimeError(f"No LanguageAdapter registered for language='{impl.language}'")

    image = getattr(adapter, "docker_image", None)
    if not image:
        raise PodmanRuntimeError(
            f"LanguageAdapter for '{impl.language}' has no docker_image configured"
        )

    # Prepare workspace
    root = Path(settings.ANALYZER_WORKSPACE_ROOT or "./workspace")
    run_label = f"impl_{implementation_id}"
    repo_dir = _clone_repo_to_workspace(
        repo_url=impl.repo_url,
        revision=impl.revision,
        workspace_root=root,
        run_label=run_label,
    )

    # Podman needs an ABSOLUTE PATH for bind mounts
    host_path = str(repo_dir.resolve())

    # Build shell command to run inside container
    inner_cmd = _build_test_shell_command(adapter, project_root="/workspace")

    # Compose container run command
    cmd = [
        runtime_bin,
        "run",
        "--rm",
        "--network",
        settings.CONTAINER_NETWORK or "bridge",
        "-v",
        f"{host_path}:/workspace",
        "-w",
        "/workspace",
        image,
        "sh",
        "-lc",
        inner_cmd,
    ]

    start = time.time()
    try:
        proc = subprocess.run(
            cmd,
            check=False,  # Do not raise, we want exit_code + stdout/stderr either way
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except Exception as exc:
        raise PodmanRuntimeError(f"Error running container: {exc}") from exc
    finally:
        elapsed = time.time() - start

    return PodmanRunResult(
        exit_code=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
        container_image=image,
        elapsed_seconds=elapsed,
        container_id=None,
    )


def run_paired_legacy_and_harness(
    db: Session,
    legacy_implementation_id: int,
    harness_implementation_id: int,
) -> PodmanRunResult:
    """
    Paired runner:

    - legacy implementation: code repo
    - harness implementation: tests repo (status='test-harness')

    Layout inside container:
      /code  -> legacy repo
      /tests -> harness repo (working directory)

    The adapter's run_contract_test_command() is used to define what to run.
    """
    runtime_bin = _ensure_runtime_binary()

    legacy: BehaviorImplementation | None = db.get(
        BehaviorImplementation, legacy_implementation_id
    )
    if legacy is None:
        raise PodmanRuntimeError(
            f"Legacy BehaviorImplementation {legacy_implementation_id} not found"
        )

    harness: BehaviorImplementation | None = db.get(
        BehaviorImplementation, harness_implementation_id
    )
    if harness is None:
        raise PodmanRuntimeError(
            f"Harness BehaviorImplementation {harness_implementation_id} not found"
        )

    if legacy.language != harness.language:
        raise PodmanRuntimeError(
            f"Language mismatch: legacy={legacy.language}, harness={harness.language}"
        )

    if not legacy.repo_url or not legacy.revision:
        raise PodmanRuntimeError(
            f"Legacy implementation {legacy_implementation_id} missing repo_url or revision"
        )
    if not harness.repo_url or not harness.revision:
        raise PodmanRuntimeError(
            f"Harness implementation {harness_implementation_id} missing repo_url or revision"
        )

    try:
        adapter = get_adapter(legacy.language)
    except KeyError:
        raise PodmanRuntimeError(
            f"No LanguageAdapter registered for language='{legacy.language}'"
        )

    image = getattr(adapter, "docker_image", None)
    if not image:
        raise PodmanRuntimeError(
            f"LanguageAdapter for '{legacy.language}' has no docker_image configured"
        )

    # Workspace layout: <root>/runtime_paired/legacy_<id>_harness_<id>/{code,tests}
    root = Path(settings.ANALYZER_WORKSPACE_ROOT or "./workspace")
    pair_root = root / "runtime_paired" / f"legacy_{legacy_implementation_id}_h_{harness_implementation_id}"
    if pair_root.exists():
        shutil.rmtree(pair_root)
    pair_root.mkdir(parents=True, exist_ok=True)

    code_dir = pair_root / "code"
    tests_dir = pair_root / "tests"

    _git_clone_repo(legacy.repo_url, legacy.revision, code_dir)
    _git_clone_repo(harness.repo_url, harness.revision, tests_dir)

    host_code = str(code_dir.resolve())
    host_tests = str(tests_dir.resolve())

    # Build command to run inside /tests; adapter can later be made aware of /code
    inner_cmd = adapter.run_contract_test_command(
        behavior_id=legacy.behavior_id,
        contract_id=None,
        project_root="/tests",
    )

    if isinstance(inner_cmd, list):
        inner_cmd = " ".join(inner_cmd)

    cmd = [
        runtime_bin,
        "run",
        "--rm",
        "--network",
        settings.CONTAINER_NETWORK or "bridge",
        "-v",
        f"{host_code}:/code",
        "-v",
        f"{host_tests}:/tests",
        "-w",
        "/tests",
        image,
        "sh",
        "-lc",
        inner_cmd,
    ]

    start = time.time()
    try:
        proc = subprocess.run(
            cmd,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except Exception as exc:
        raise PodmanRuntimeError(f"Error running paired container: {exc}") from exc
    finally:
        elapsed = time.time() - start

    return PodmanRunResult(
        exit_code=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
        container_image=image,
        elapsed_seconds=elapsed,
        container_id=None,
    )


def deploy_service_from_repo(
    *,
    repo_url: str,
    revision: str,
    language: str,
    workspace_root: Optional[Path] = None,
    command_override: Optional[str] = None,
    host_port: Optional[int] = None,
    container_port: Optional[int] = None,
) -> PodmanRunResult:
    """
    Very simple 'deploy' helper:

    - Clone the repo at revision into a workspace
    - Run a container in detached mode (-d) using adapter's default 'service' command,
      or a provided command_override.
    - Optionally map a host port to a container port.

    This is intentionally generic and does not assume any particular framework.
    """
    runtime_bin = _ensure_runtime_binary()

    try:
        adapter = get_adapter(language)
    except KeyError:
        raise PodmanRuntimeError(f"No LanguageAdapter registered for language='{language}'")

    image = getattr(adapter, "docker_image", None)
    if not image:
        raise PodmanRuntimeError(
            f"LanguageAdapter for '{language}' has no docker_image configured"
        )

    # Workspace
    root = workspace_root or Path(settings.ANALYZER_WORKSPACE_ROOT or "./workspace")
    run_label = f"deploy_{language}"
    repo_dir = _clone_repo_to_workspace(
        repo_url=repo_url,
        revision=revision,
        workspace_root=root,
        run_label=run_label,
    )

    # Absolute host path for bind mount
    host_path = str(repo_dir.resolve())

    # Determine service command
    if command_override:
        inner_cmd = command_override
    else:
        # Try to get service_command from adapter if present, else fall back to test_command
        service_cmd = getattr(adapter, "service_command", None)
        if callable(service_cmd):
            inner_cmd = service_cmd("/workspace")
        else:
            inner_cmd = adapter.test_command("/workspace")

        if isinstance(inner_cmd, list):
            inner_cmd = " ".join(inner_cmd)

    cmd = [
        runtime_bin,
        "run",
        "-d",  # detached service
        "--network",
        settings.CONTAINER_NETWORK or "bridge",
        "-v",
        f"{host_path}:/workspace",
        "-w",
        "/workspace",
    ]

    if host_port is not None and container_port is not None:
        cmd.extend(["-p", f"{host_port}:{container_port}"])

    cmd.append(image)
    cmd.extend(["sh", "-lc", inner_cmd])

    start = time.time()
    try:
        proc = subprocess.run(
            cmd,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        elapsed = time.time() - start
        raise PodmanRuntimeError(
            f"Service deploy failed: {exc.stderr}"
        ) from exc

    elapsed = time.time() - start

    # In detached mode, stdout should be the container id
    container_id = proc.stdout.strip()

    return PodmanRunResult(
        exit_code=0,
        stdout=proc.stdout,
        stderr=proc.stderr,
        container_image=image,
        elapsed_seconds=elapsed,
        container_id=container_id,
    )
