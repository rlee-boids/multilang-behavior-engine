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
    """Errors raised when running code in Podman containers."""


@dataclass
class PodmanRuntimeResult:
    exit_code: int
    stdout: str
    stderr: str
    container_image: str
    elapsed_seconds: float


# Base directory on the host where we clone repos for runtime testing
RUNTIME_ROOT = Path("workspace/runtime").resolve()


def _ensure_runtime_root() -> Path:
    RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
    return RUNTIME_ROOT


def _get_implementation(db: Session, implementation_id: int) -> BehaviorImplementation:
    impl = db.get(BehaviorImplementation, implementation_id)
    if not impl:
        raise PodmanRuntimeError(
            f"BehaviorImplementation {implementation_id} not found"
        )
    if not impl.repo_url:
        raise PodmanRuntimeError(
            f"BehaviorImplementation {implementation_id} has no repo_url"
        )
    return impl


def _clone_or_update_repo(repo_url: str, revision: Optional[str], dest: Path) -> None:
    """
    Clone or update a git repo into dest.

    - If dest does not exist: git clone --depth=1 --branch <rev or main> <url> dest
    - If dest exists and is a git repo: git fetch && git checkout <rev or main>
    """
    dest_parent = dest.parent
    dest_parent.mkdir(parents=True, exist_ok=True)

    branch = revision or "main"

    if not dest.exists():
        # Fresh clone
        cmd = [
            "git",
            "clone",
            "--depth",
            "1",
            "--branch",
            branch,
            repo_url,
            str(dest),
        ]
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if proc.returncode != 0:
            raise PodmanRuntimeError(
                f"git clone failed for {repo_url}@{branch}: {proc.stderr}"
            )
        return

    # Existing dir -> try to update if it's a git repo
    if not (dest / ".git").exists():
        # Not a git repo; blow it away and reclone
        shutil.rmtree(dest)
        _clone_or_update_repo(repo_url, revision, dest)
        return

    # git fetch + checkout
    cmd_fetch = ["git", "-C", str(dest), "fetch", "--all", "--prune"]
    proc_fetch = subprocess.run(
        cmd_fetch,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if proc_fetch.returncode != 0:
        raise PodmanRuntimeError(
            f"git fetch failed for {repo_url}: {proc_fetch.stderr}"
        )

    cmd_checkout = ["git", "-C", str(dest), "checkout", branch]
    proc_checkout = subprocess.run(
        cmd_checkout,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if proc_checkout.returncode != 0:
        raise PodmanRuntimeError(
            f"git checkout {branch} failed for {repo_url}: {proc_checkout.stderr}"
        )

    cmd_reset = ["git", "-C", str(dest), "reset", "--hard", f"origin/{branch}"]
    proc_reset = subprocess.run(
        cmd_reset,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if proc_reset.returncode != 0:
        raise PodmanRuntimeError(
            f"git reset failed for {repo_url}: {proc_reset.stderr}"
        )


def _run_podman(
    image: str,
    workdir_in_container: str,
    volumes: list[tuple[Path, str]],
    inner_cmd: str,
) -> PodmanRuntimeResult:
    """
    Run a single Podman container with:

    - image: container image name
    - workdir_in_container: working directory inside container
    - volumes: list of (host_path, container_path) tuples
    - inner_cmd: shell command to run via `sh -lc`
    """
    runtime_bin = settings.CONTAINER_RUNTIME or "podman"

    # Build volume args
    volume_args: list[str] = []
    for host, container in volumes:
        volume_args.extend(
            [
                "-v",
                f"{host.resolve()}:{container}",
            ]
        )

    cmd = [
        runtime_bin,
        "run",
        "--rm",
        "-w",
        workdir_in_container,
        *volume_args,
        image,
        "sh",
        "-lc",
        inner_cmd,
    ]

    start = time.time()
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    end = time.time()

    return PodmanRuntimeResult(
        exit_code=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
        container_image=image,
        elapsed_seconds=end - start,
    )


# ---------- Public API: single implementation ----------


async def run_tests_for_implementation(
    db: Session,
    implementation_id: int,
) -> PodmanRuntimeResult:
    """
    Run tests for a single BehaviorImplementation:

    - Clone/update its repo into workspace/runtime/impl_<id>
    - Use the adapter's build_command + test_command
    - Run in Podman with the adapter's docker_image
    """
    impl = _get_implementation(db, implementation_id)
    adapter = get_adapter(impl.language)

    _ensure_runtime_root()
    work_dir = RUNTIME_ROOT / f"impl_{impl.id}"
    _clone_or_update_repo(impl.repo_url, impl.revision, work_dir)

    image = adapter.docker_image

    # Build inner command using adapter hooks; we expect these to be shell snippets
    build_cmd = adapter.build_command("/workspace")
    test_cmd = adapter.test_command("/workspace")

    if build_cmd:
        inner_cmd = f"{build_cmd} && {test_cmd}"
    else:
        inner_cmd = test_cmd

    result = _run_podman(
        image=image,
        workdir_in_container="/workspace",
        volumes=[(work_dir, "/workspace")],
        inner_cmd=inner_cmd,
    )

    return result


# ---------- Public API: legacy + harness together ----------


async def run_legacy_with_harness(
    db: Session,
    legacy_implementation_id: int,
    harness_implementation_id: int,
    behavior_id: Optional[int] = None,
    contract_id: Optional[int] = None,
) -> PodmanRuntimeResult:
    """
    Run legacy implementation + separate harness repo together in Podman.

    Layout inside the container:
      /code   -> legacy repo
      /tests  -> harness repo (working dir)

    The language adapter provides a `run_contract_test_command` which is
    executed with project_root="/tests".
    """
    legacy_impl = _get_implementation(db, legacy_implementation_id)
    harness_impl = _get_implementation(db, harness_implementation_id)

    if legacy_impl.behavior_id != harness_impl.behavior_id:
        # Allow caller to override, but warn by failing here if they mismatch.
        raise PodmanRuntimeError(
            f"Legacy implementation behavior_id={legacy_impl.behavior_id} "
            f"differs from harness behavior_id={harness_impl.behavior_id}"
        )

    behavior_id = behavior_id or legacy_impl.behavior_id

    adapter = get_adapter(legacy_impl.language)
    image = adapter.docker_image

    _ensure_runtime_root()

    legacy_dir = RUNTIME_ROOT / f"legacy_{legacy_impl.id}"
    harness_dir = RUNTIME_ROOT / f"harness_{harness_impl.id}"

    _clone_or_update_repo(legacy_impl.repo_url, legacy_impl.revision, legacy_dir)
    _clone_or_update_repo(harness_impl.repo_url, harness_impl.revision, harness_dir)

    # Let the adapter define the exact test command using the contract semantics
    inner_cmd = adapter.run_contract_test_command(
        behavior_id=behavior_id,
        contract_id=contract_id,
        project_root="/tests",
    )

    result = _run_podman(
        image=image,
        workdir_in_container="/tests",
        volumes=[
            (legacy_dir, "/code"),
            (harness_dir, "/tests"),
        ],
        inner_cmd=inner_cmd,
    )

    return result
