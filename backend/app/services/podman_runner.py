from __future__ import annotations

import os
import asyncio
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, List

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.behavior_implementation import BehaviorImplementation
from app.adapters import get_adapter
DEBUG_PODMAN = os.getenv("MLBE_DEBUG_PODMAN", "0") == "1"

class PodmanRuntimeError(RuntimeError):
    """
    Raised when a Podman or git command fails.

    Carries stdout/stderr/exit_code so callers (and HTTP error responses)
    can surface useful debugging info.
    """

    def __init__(self, message: str, stdout: str = "", stderr: str = "", exit_code: int | None = None) -> None:
        super().__init__(message)
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code

    def __str__(self) -> str:
        base = super().__str__()
        parts: list[str] = []
        if self.exit_code is not None:
            parts.append(f"exit_code={self.exit_code}")
        if self.stdout:
            parts.append("stdout:\n" + self.stdout)
        if self.stderr:
            parts.append("stderr:\n" + self.stderr)
        if parts:
            return base + "\n" + "\n".join(parts)
        return base


@dataclass
class PodmanExecResult:
    stdout: str
    stderr: str
    exit_code: int


@dataclass
class PodmanResult:
    """
    Higher-level result used by runtime endpoints.
    """

    exit_code: int
    stdout: str
    stderr: str
    container_image: str
    elapsed_seconds: float


# ---------- Container runtime configuration ----------

# Allow overriding the container runtime (podman or docker) via env var.
# Default is "podman", per your current design.
PODMAN_BIN = os.getenv("MLBE_CONTAINER_BIN", "podman")

GIT_BIN = os.getenv("MLBE_GIT_BIN", "git")
# ---------- Low-level podman wrapper ----------


async def run_podman(args: List[str], cwd: Optional[Path] = None) -> PodmanExecResult:
    """
    Run the configured container runtime (PODMAN_BIN) with the given args.
    """
    if DEBUG_PODMAN:
        print(f"[MLBE_DEBUG] run_podman: PODMAN_BIN={PODMAN_BIN!r}, args={args}, cwd={cwd}")

    try:
        proc = await asyncio.create_subprocess_exec(
            PODMAN_BIN,
            *args,
            cwd=str(cwd) if cwd is not None else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        # If we hit this, you’ll see a PodmanRuntimeError, not raw [Errno 2]
        raise PodmanRuntimeError(
            (
                f"Container runtime '{PODMAN_BIN}' not found. "
                "Install Podman (or Docker) and ensure it is on PATH for the backend "
                "process, or set MLBE_CONTAINER_BIN to the full path of the binary "
                "(e.g. /opt/podman/bin/podman) and restart the backend."
            )
        ) from exc

    stdout_b, stderr_b = await proc.communicate()
    stdout = stdout_b.decode()
    stderr = stderr_b.decode()

    if proc.returncode != 0:
        raise PodmanRuntimeError(
            f"{PODMAN_BIN} {' '.join(args)} failed with exit {proc.returncode}",
            stdout=stdout,
            stderr=stderr,
            exit_code=proc.returncode,
        )

    return PodmanExecResult(stdout=stdout, stderr=stderr, exit_code=proc.returncode)

# ---------- Local git helpers (self-contained) ----------


async def _run_git(args: list[str], cwd: Path) -> None:
    """
    Run a git command (GIT_BIN) and raise PodmanRuntimeError on failure.

    This version explicitly checks both:
      - that GIT_BIN exists and is executable
      - that cwd exists
    so we don't mis-diagnose FileNotFoundError.
    """
    if DEBUG_PODMAN:
        print(f"[MLBE_DEBUG] _run_git: GIT_BIN={GIT_BIN!r}, args={args}, cwd={cwd}")

    # Pre-check binary and cwd before spawning
    import os as _os

    git_exists = _os.path.exists(GIT_BIN)
    git_x_ok = _os.access(GIT_BIN, _os.X_OK)
    cwd_exists = cwd.exists()

    if DEBUG_PODMAN:
        print(
            f"[MLBE_DEBUG] _run_git precheck: "
            f"git_exists={git_exists}, git_x_ok={git_x_ok}, cwd_exists={cwd_exists}"
        )

    if not cwd_exists:
        raise PodmanRuntimeError(
            f"Git cwd '{cwd}' does not exist. "
            f"(GIT_BIN='{GIT_BIN}')"
        )

    if not git_exists or not git_x_ok:
        raise PodmanRuntimeError(
            f"Git binary '{GIT_BIN}' is not usable: "
            f"exists={git_exists}, executable={git_x_ok}. "
            "Install git and ensure it is executable for the backend process, "
            "or set MLBE_GIT_BIN to a valid git binary (e.g. /opt/homebrew/bin/git)."
        )

    try:
        proc = await asyncio.create_subprocess_exec(
            GIT_BIN,
            *args,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        # If we get here, the OS raised ENOENT anyway (rare if prechecks passed);
        # just dump all context.
        raise PodmanRuntimeError(
            "Git invocation failed with FileNotFoundError. "
            f"GIT_BIN='{GIT_BIN}', cwd='{cwd}'. "
            "Double-check that both the git binary and cwd exist and are accessible."
        ) from exc

    stdout_b, stderr_b = await proc.communicate()
    stdout = stdout_b.decode()
    stderr = stderr_b.decode()

    if proc.returncode != 0:
        raise PodmanRuntimeError(
            f"{GIT_BIN} {' '.join(args)} failed with exit {proc.returncode}",
            stdout=stdout,
            stderr=stderr,
            exit_code=proc.returncode,
        )


def _with_github_token(repo_url: str) -> str:
    """
    Inject a GitHub token into HTTPS URL if available.

    Supports private repos like:
      https://github.com/rlee-boids/private-repo(.git)?
    """
    token = (
        os.getenv("GITHUB_TOKEN")
        or os.getenv("GH_TOKEN")
        or os.getenv("GITHUB_PAT")
    )
    if not token:
        return repo_url

    prefix = "https://github.com/"
    if repo_url.startswith(prefix):
        # https://github.com/OWNER/REPO(.git)? -> https://TOKEN@github.com/OWNER/REPO(.git)?
        rest = repo_url[len("https://"):]  # "github.com/OWNER/REPO..."
        return f"https://{token}@" + rest

    # If it's already an https://token@github.com/... style URL, just return it.
    return repo_url


async def clone_or_update_repo(
    repo_url: str,
    base_dir: Path,
    revision: str,
) -> Path:
    """
    Simple, self-contained clone/update helper for runtime use.

    - Accepts either HTML or .git-style URLs.
    - Injects GitHub token into URL if present (for private repos).
    - If repo directory exists:
        git fetch; git checkout <revision>; git pull --ff-only origin <revision>
    - Else:
        git clone <repo_url> <dir>; git checkout <revision>
    """
    base_dir = base_dir.resolve()
    base_dir.mkdir(parents=True, exist_ok=True)

    # Normalize to .git URL
    raw_url = repo_url.rstrip("/")
    if not raw_url.endswith(".git"):
        raw_url = raw_url + ".git"

    # Add token if available
    clone_url = _with_github_token(raw_url)

    # Derive repo dir name from the *path* portion (strip .git)
    repo_name = raw_url.rsplit("/", 1)[-1]
    if repo_name.endswith(".git"):
        repo_name = repo_name[:-4]

    repo_path = base_dir / repo_name

    if repo_path.exists():
        # Update existing clone
        await _run_git(["fetch", "origin"], cwd=repo_path)
        await _run_git(["checkout", revision], cwd=repo_path)
        await _run_git(["pull", "--ff-only", "origin", revision], cwd=repo_path)
    else:
        # New clone
        proc = await asyncio.create_subprocess_exec(
            "git",
            "clone",
            clone_url,
            str(repo_path),
            cwd=str(base_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_b, stderr_b = await proc.communicate()
        stdout = stdout_b.decode()
        stderr = stderr_b.decode()
        if proc.returncode != 0:
            raise PodmanRuntimeError(
                f"git clone failed with exit {proc.returncode}",
                stdout=stdout,
                stderr=stderr,
                exit_code=proc.returncode,
            )

        # Checkout requested revision (branch/tag/commit)
        await _run_git(["checkout", revision], cwd=repo_path)

    return repo_path


# ---------- High-level helpers used by FastAPI routes ----------


async def run_tests_for_implementation(
    db: Session,
    implementation_id: int,
) -> PodmanResult:
    """
    Run tests for a single BehaviorImplementation in an ephemeral container.

    Uses the LanguageAdapter's docker_image + build_command + test_command.
    """
    impl: Optional[BehaviorImplementation] = (
        db.query(BehaviorImplementation)
        .filter(BehaviorImplementation.id == implementation_id)
        .one_or_none()
    )
    if impl is None:
        raise PodmanRuntimeError(f"BehaviorImplementation id={implementation_id} not found")

    if not impl.repo_url:
        raise PodmanRuntimeError(f"Implementation {implementation_id} has no repo_url set")

    if not impl.language:
        raise PodmanRuntimeError(f"Implementation {implementation_id} has no language set")

    adapter = get_adapter(impl.language)

    # Workspace where we clone the repo
    workspace_root = Path(os.path.abspath(settings.ANALYZER_WORKSPACE_ROOT))
    workspace_root.mkdir(parents=True, exist_ok=True)

    repo_root = await clone_or_update_repo(
        repo_url=impl.repo_url,
        base_dir=workspace_root,
        revision=impl.revision or "main",
    )

    image = adapter.docker_image

    # Build + test command inside the container
    build_cmd = adapter.build_command("/code")
    test_cmd = adapter.test_command("/code")

    parts: list[str] = []
    if build_cmd:
        parts.append(str(build_cmd))
    parts.append(str(test_cmd))
    joined_cmd = " && ".join(parts)

    full_args = [
        "run",
        "--rm",
        "-v",
        f"{repo_root}:/code",
        "-w",
        "/code",
        image,
        "/bin/sh",
        "-lc",
        joined_cmd,
    ]

    t0 = time.monotonic()
    try:
        exec_res = await run_podman(full_args, cwd=None)
    except PodmanRuntimeError as exc:
        elapsed = time.monotonic() - t0
        # Re-raise with same info but include context about the implementation
        raise PodmanRuntimeError(
            f"Runtime test failed for implementation {implementation_id}: {exc}",
            stdout=exc.stdout,
            stderr=exc.stderr,
            exit_code=exc.exit_code,
        ) from exc

    elapsed = time.monotonic() - t0
    return PodmanResult(
        exit_code=exec_res.exit_code,
        stdout=exec_res.stdout,
        stderr=exec_res.stderr,
        container_image=image,
        elapsed_seconds=elapsed,
    )


async def run_legacy_with_harness(
    db: Session,
    legacy_implementation_id: int,
    harness_implementation_id: int,
    behavior_id: int,
    contract_id: int | None,
) -> PodmanResult:
    """
    Run legacy code + harness tests inside a paired container setup.

    Layout inside the container:
      /code  -> legacy repo
      /tests -> harness repo (working dir)

    Uses the LanguageAdapter.run_contract_test_command to construct the test command.
    """
    legacy_impl: Optional[BehaviorImplementation] = (
        db.query(BehaviorImplementation)
        .filter(BehaviorImplementation.id == legacy_implementation_id)
        .one_or_none()
    )
    if legacy_impl is None:
        raise PodmanRuntimeError(f"Legacy BehaviorImplementation id={legacy_implementation_id} not found")

    harness_impl: Optional[BehaviorImplementation] = (
        db.query(BehaviorImplementation)
        .filter(BehaviorImplementation.id == harness_implementation_id)
        .one_or_none()
    )
    if harness_impl is None:
        raise PodmanRuntimeError(f"Harness BehaviorImplementation id={harness_implementation_id} not found")

    if legacy_impl.language != harness_impl.language:
        raise PodmanRuntimeError(
            f"Language mismatch: legacy={legacy_impl.language}, harness={harness_impl.language}"
        )

    if not legacy_impl.repo_url:
        raise PodmanRuntimeError(f"Legacy implementation {legacy_implementation_id} has no repo_url set")
    if not harness_impl.repo_url:
        raise PodmanRuntimeError(f"Harness implementation {harness_implementation_id} has no repo_url set")

    language = legacy_impl.language
    adapter = get_adapter(language)

    workspace_root = Path(os.path.abspath(settings.ANALYZER_WORKSPACE_ROOT))
    workspace_root.mkdir(parents=True, exist_ok=True)

    legacy_root = await clone_or_update_repo(
        repo_url=legacy_impl.repo_url,
        base_dir=workspace_root,
        revision=legacy_impl.revision or "main",
    )
    harness_root = await clone_or_update_repo(
        repo_url=harness_impl.repo_url,
        base_dir=workspace_root,
        revision=harness_impl.revision or "main",
    )

    image = adapter.docker_image
    test_cmd = adapter.run_contract_test_command(
        behavior_id=behavior_id,
        contract_id=contract_id,
        project_root="/tests",
    )

    full_args = [
        "run",
        "--rm",
        "-v",
        f"{legacy_root}:/code",
        "-v",
        f"{harness_root}:/tests",
        "-w",
        "/tests",
        image,
        "/bin/sh",
        "-lc",
        str(test_cmd),
    ]

    t0 = time.monotonic()
    try:
        exec_res = await run_podman(full_args, cwd=None)
    except PodmanRuntimeError as exc:
        elapsed = time.monotonic() - t0
        raise PodmanRuntimeError(
            f"Legacy+harness test failed: {exc}",
            stdout=exc.stdout,
            stderr=exc.stderr,
            exit_code=exc.exit_code,
        ) from exc

    elapsed = time.monotonic() - t0
    return PodmanResult(
        exit_code=exec_res.exit_code,
        stdout=exec_res.stdout,
        stderr=exec_res.stderr,
        container_image=image,
        elapsed_seconds=elapsed,
    )
