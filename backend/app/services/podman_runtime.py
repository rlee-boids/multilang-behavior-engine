from __future__ import annotations

import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Literal

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


def _clone_repo_to_workspace(
    repo_url: str,
    revision: str,
    workspace_root: Path,
    run_label: str,
) -> Path:
    """
    Clone a Git repo into a unique workspace directory.

    For now we do a fresh clone per run:
      workspace_root / run_label
    """
    if not repo_url:
        raise PodmanRuntimeError("BehaviorImplementation.repo_url is empty")

    runtime_dir = workspace_root / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)

    run_dir = runtime_dir / run_label

    # If it already exists from a previous run, blow it away for now.
    if run_dir.exists():
        shutil.rmtree(run_dir)

    run_dir.mkdir(parents=True, exist_ok=True)

    # Simple clone of the given revision/branch
    cmd = [
        "git",
        "clone",
        "--branch",
        revision,
        "--single-branch",
        repo_url,
        str(run_dir),
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

    return run_dir


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
    High-level helper:

    - Load BehaviorImplementation from DB
    - Get appropriate LanguageAdapter
    - Clone repo into a temporary workspace
    - Run build+test inside a Podman (or Docker) container
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
        f"{repo_dir}:/workspace",
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
        f"{repo_dir}:/workspace",
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
