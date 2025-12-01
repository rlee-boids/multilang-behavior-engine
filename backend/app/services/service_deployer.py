from __future__ import annotations

import textwrap
from dataclasses import dataclass
from pathlib import Path

from typing import Optional

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.behavior_implementation import BehaviorImplementation
from app.services.podman_runner import (
    clone_or_update_repo,
    run_podman,
    PodmanRuntimeError,
)


class ServiceDeploymentError(Exception):
    """Raised when deploying a behavior UI service fails."""


@dataclass
class ServiceDeploymentResult:
    implementation_id: int
    image: str
    container_name: str
    internal_port: int
    host_port: int
    url: str
    build_stdout: str
    build_stderr: str
    run_stdout: str
    run_stderr: str


def _get_service_workspace_root() -> Path:
    """
    Root directory where we stage service Docker builds.

    Uses settings.service_workspace_root if present, else ./services_workspace.
    """
    root = getattr(settings, "service_workspace_root", None)
    if not root:
        root = "./services_workspace"
    p = Path(root)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _render_perl_psgi_app(cgi_path: str) -> str:
    """
    Generate a minimal app.psgi that wraps a CGI script via CGI::Emulate::PSGI.
    """
    return textwrap.dedent(
        f"""\
        use strict;
        use warnings;

        use CGI::Emulate::PSGI;
        use CGI::Compile;

        # Wrap the legacy CGI script as a PSGI app.
        my $app = CGI::Emulate::PSGI->handler(
            CGI::Compile->compile('{cgi_path}')
        );

        $app;
        """
    )


def _render_perl_ui_dockerfile() -> str:
    """
    Dockerfile for running a Perl CGI/PSGI UI via plackup.
    """
    return textwrap.dedent(
        """\
        FROM perl:5.38

        WORKDIR /app

        # Copy entire repo as build context
        COPY . /app

        # Install system libs + CPAN modules needed by Plot::Generator + PSGI
        RUN apt-get update \
            && apt-get install -y --no-install-recommends \
                cpanminus \
                libgd-dev \
            && cpanm --notest \
                GD::Graph \
                JSON \
                File::Slurp \
                Plack \
                CGI::Emulate::PSGI \
                CGI::Compile \
            && apt-get clean \
            && rm -rf /var/lib/apt/lists/*

        EXPOSE 5000

        CMD ["plackup", "-Ilib", "-p", "5000", "app.psgi"]
        """
    )


async def deploy_behavior_service(
    db: Session,
    implementation_id: int,
    host_port: Optional[int] = None,
) -> ServiceDeploymentResult:
    """
    Deploy a UI/service for a given BehaviorImplementation.

    For now, only Perl CGI/PSGI UIs are supported:
      - impl.language == 'perl'
      - impl.file_path points to a CGI script (e.g. 'cgi-bin/plot_ui.cgi')

    Flow:
      1. Clone or update the legacy UI repo into the service workspace.
      2. Write app.psgi at the repo root that wraps the CGI script.
      3. Write a Dockerfile for a plackup-based Perl PSGI service.
      4. Build image:  mlbe-svc-<language>-impl-<id>
      5. Run container: mlbe-svc-<id>, port host_port:5000
    """
    impl: Optional[BehaviorImplementation] = (
        db.query(BehaviorImplementation)
        .filter(BehaviorImplementation.id == implementation_id)
        .one_or_none()
    )
    if impl is None:
        raise ServiceDeploymentError(f"BehaviorImplementation id={implementation_id} not found")

    if not impl.repo_url:
        raise ServiceDeploymentError(f"Implementation {implementation_id} has no repo_url set")

    language = (impl.language or "").lower()
    file_path = impl.file_path or ""

    if language != "perl":
        raise ServiceDeploymentError(
            f"Only perl UI deployments are supported for now (got language={impl.language!r})"
        )

    if not file_path.endswith(".cgi"):
        raise ServiceDeploymentError(
            f"Perl UI deployment expects a CGI script (.cgi), got file_path={file_path!r}"
        )

    # --- 1. Clone/update repo into the service workspace ---
    workspace_root = _get_service_workspace_root()
    repo_root = await clone_or_update_repo(
        repo_url=impl.repo_url,
        base_dir=workspace_root,
        revision=impl.revision or "main",
    )

    # --- 2. Write app.psgi that wraps the CGI script ---
    app_psgi_path = repo_root / "app.psgi"
    app_psgi_code = _render_perl_psgi_app(file_path)
    app_psgi_path.write_text(app_psgi_code)

    # --- 3. Write Dockerfile for PSGI service ---
    dockerfile_path = repo_root / "Dockerfile"
    dockerfile_code = _render_perl_ui_dockerfile()
    dockerfile_path.write_text(dockerfile_code)

    # --- 4. Build image ---
    image_name = f"mlbe-svc-{language}-impl-{implementation_id}"
    build_args = ["build", "-t", image_name, "."]

    try:
        build_res = await run_podman(build_args, cwd=repo_root)
    except PodmanRuntimeError as exc:
        raise ServiceDeploymentError(
            f"Image build failed (exit {exc.exit_code})\nstdout:\n{exc.stdout}\n\nstderr:\n{exc.stderr}\n"
        ) from exc

    # --- 5. Run container ---
    container_name = f"mlbe-svc-{implementation_id}"
    internal_port = 5000
    if host_port is None:
        host_port = 18000 + implementation_id

    # Stop/remove existing container if present (ignore failure)
    try:
        await run_podman(["rm", "-f", container_name])
    except PodmanRuntimeError:
        # It's fine if it wasn't running
        pass

    run_args = [
        "run",
        "-d",
        "--rm",
        "-p",
        f"{host_port}:{internal_port}",
        "--name",
        container_name,
        image_name,
    ]

    try:
        run_res = await run_podman(run_args)
    except PodmanRuntimeError as exc:
        raise ServiceDeploymentError(
            f"Container run failed (exit {exc.exit_code})\nstdout:\n{exc.stdout}\n\nstderr:\n{exc.stderr}\n"
        ) from exc

    url = f"http://localhost:{host_port}"

    return ServiceDeploymentResult(
        implementation_id=implementation_id,
        image=image_name,
        container_name=container_name,
        internal_port=internal_port,
        host_port=host_port,
        url=url,
        build_stdout=build_res.stdout,
        build_stderr=build_res.stderr,
        run_stdout=run_res.stdout,
        run_stderr=run_res.stderr,
    )
