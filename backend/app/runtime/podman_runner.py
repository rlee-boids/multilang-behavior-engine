from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from app.core.config import settings


@dataclass
class ContainerRunResult:
    exit_code: int
    stdout: str
    stderr: str


@dataclass
class VolumeMount:
    host_path: Path
    container_path: Path
    read_only: bool = False


@dataclass
class PodmanRunner:
    """
    Thin wrapper around the `podman` CLI.

    This is intentionally simple and avoids any heavy SDKs.
    """

    binary: str = field(default_factory=lambda: settings.CONTAINER_RUNTIME)
    network: str = field(default_factory=lambda: settings.CONTAINER_NETWORK)

    def run(
        self,
        image: str,
        command: List[str],
        volumes: Optional[List[VolumeMount]] = None,
        env: Optional[Dict[str, str]] = None,
        workdir: Optional[str] = None,
        remove: bool = True,
    ) -> ContainerRunResult:
        volumes = volumes or []
        env = env or {}

        cmd: List[str] = [self.binary, "run"]

        if remove:
            cmd.append("--rm")

        if self.network:
            cmd += ["--network", self.network]

        # Environment variables
        for key, value in env.items():
            cmd += ["-e", f"{key}={value}"]

        # Volume mounts
        for vol in volumes:
            mount_spec = f"{vol.host_path}:{vol.container_path}"
            if vol.read_only:
                mount_spec += ":ro"
            cmd += ["-v", mount_spec]

        if workdir:
            cmd += ["-w", workdir]

        # Image + command
        cmd.append(image)
        cmd += command

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        stdout, stderr = proc.communicate()
        return ContainerRunResult(exit_code=proc.returncode, stdout=stdout, stderr=stderr)
