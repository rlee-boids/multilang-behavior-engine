from __future__ import annotations

import os
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

# Kept for backwards compatibility if referenced elsewhere; not used directly below.
SERVICES_WORKSPACE_ROOT = Path(
    os.path.abspath(getattr(settings, "SERVICES_WORKSPACE_ROOT", "services_workspace"))
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
    # Make this absolute so it doesn’t depend on uvicorn’s CWD.
    p = Path(root).resolve()
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


def _render_python_ui_dockerfile(entrypoint: str) -> str:
    """
    Dockerfile for running a Python-based UI.

    `entrypoint` is taken from BehaviorImplementation.file_path, e.g.:
      - "app/ui/plot_ui.py"
      - "cgi-bin/plot_ui.py"
    We simply run `python <entrypoint>`.
    """
    # Normalize to POSIX-style path just in case
    entrypoint = entrypoint.replace("\\\\", "/") or "app/ui/plot_ui.py"

    return textwrap.dedent(
        f"""\
        FROM python:3.12-slim
        WORKDIR /app
        COPY . /app

        # Install dependencies if requirements.txt present
        RUN if [ -f requirements.txt ]; then \\
                pip install --no-cache-dir -r requirements.txt; \\
            fi

        EXPOSE 8000

        # Run the converted UI entrypoint
        CMD ["python", "{entrypoint}"]
        """
    )


def _render_perl_ui_dockerfile() -> str:
    """
    Dockerfile for running a Perl CGI/PSGI UI via plackup.

    We tweak APT sources to use HTTPS when possible, to avoid transparent
    HTTP proxies / captive portals (e.g. Meraki) that block plain HTTP
    access to deb.debian.org.
    """
    return textwrap.dedent(
        """\
        FROM perl:5.38

        WORKDIR /app

        # Copy entire repo as build context
        COPY . /app

        # Install required deps via apt + cpanm.
        # On newer Debian images, /etc/apt/sources.list may not exist and
        # APT uses /etc/apt/sources.list.d/debian.sources instead. We
        # conditionally rewrite both, if present, to prefer HTTPS URIs.
        RUN if [ -f /etc/apt/sources.list ]; then \\
                sed -i 's#http://deb.debian.org#https://deb.debian.org#g' /etc/apt/sources.list; \\
            fi \\
            && if [ -f /etc/apt/sources.list.d/debian.sources ]; then \\
                sed -i 's#http://deb.debian.org/debian#https://deb.debian.org/debian#g' /etc/apt/sources.list.d/debian.sources; \\
            fi \\
            && apt-get update \\
            && apt-get install -y --no-install-recommends \\
                cpanminus \\
                libgd-dev \\
            && cpanm --notest \\
                GD::Graph \\
                JSON \\
                File::Slurp \\
                Plack \\
                CGI::Emulate::PSGI \\
                CGI::Compile \\
            && apt-get clean \\
            && rm -rf /var/lib/apt/lists/*

        EXPOSE 5000

        # Run via plackup, adding lib/ to include path
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

    Supported for now:
      - Perl CGI/PSGI UIs:
          impl.language == 'perl'
          impl.file_path points to a CGI script (e.g. 'cgi-bin/plot_ui.cgi')
      - Python UIs:
          impl.language == 'python'
          impl.file_path points to the converted UI entrypoint
          (e.g. 'app/ui/plot_ui.py' or 'cgi-bin/plot_ui.py')

    Flow:
      1. Clone or update the UI repo into the service workspace.
      2. For Perl:
           - Write app.psgi that wraps the CGI script.
           - Write a Dockerfile for a plackup-based Perl PSGI service.
         For Python:
           - Write a Dockerfile that runs the converted Python UI entrypoint.
      3. Build image:  mlbe-svc-<language>-impl-<id>
      4. Run container: mlbe-svc-<id>,
         mapping host_port to the appropriate internal port.
    """
    impl: Optional[BehaviorImplementation] = (
        db.query(BehaviorImplementation)
        .filter(BehaviorImplementation.id == implementation_id)
        .one_or_none()
    )
    if impl is None:
        raise ServiceDeploymentError(
            f"BehaviorImplementation id={implementation_id} not found"
        )

    if not impl.repo_url:
        raise ServiceDeploymentError(
            f"Implementation {implementation_id} has no repo_url set"
        )

    language = (impl.language or "").lower()
    file_path = impl.file_path or ""

    # Only perl and python are supported for UI deployment right now.
    if language not in ("perl", "python"):
        raise ServiceDeploymentError(
            f"UI deployment not supported for language={impl.language!r}"
        )

    # --- 1. Clone/update repo into the service workspace ---
    workspace_root = _get_service_workspace_root()
    repo_root = await clone_or_update_repo(
        repo_url=impl.repo_url,
        base_dir=workspace_root,
        revision=impl.revision or "main",
    )

    # --- 2. Language-specific setup (app.psgi, internal port, Dockerfile content) ---
    if language == "perl":
        # For Perl UIs we expect a CGI script.
        if not file_path.endswith(".cgi"):
            raise ServiceDeploymentError(
                f"Perl UI deployment expects a CGI script (.cgi), got file_path={file_path!r}"
            )

        # Write app.psgi that wraps the CGI script.
        app_psgi_path = repo_root / "app.psgi"
        app_psgi_code = _render_perl_psgi_app(file_path)
        app_psgi_path.write_text(app_psgi_code)

        dockerfile_code = _render_perl_ui_dockerfile()
        internal_port = 5000

    elif language == "python":
        # Python UI: we expect the AI to have generated a WSGI-style entry script
        # at impl.file_path and a requirements.txt at the repo root.
        #
        # NO assumptions about the path (it may be app/ui/plot_ui.py, main.py, etc.),
        # as long as running `python <file_path>` starts an HTTP server on port 8000.
        dockerfile_code = _render_python_ui_dockerfile(file_path)
        internal_port = 8000

    else:
        raise ServiceDeploymentError(
            f"Only perl and python UI deployments are supported for now (got language={impl.language!r})"
        )

    # --- 3. Write Dockerfile ---
    dockerfile_path = repo_root / "Dockerfile"
    dockerfile_path.write_text(dockerfile_code)

    # --- 4. Build image ---
    image_name = f"mlbe-svc-{language}-impl-{implementation_id}"
    build_args = ["build", "-t", image_name, "."]

    try:
        build_res = await run_podman(build_args, cwd=repo_root)
    except PodmanRuntimeError as exc:
        raise ServiceDeploymentError(
            f"Image build failed (exit {exc.exit_code})\n"
            f"stdout:\n{exc.stdout}\n\nstderr:\n{exc.stderr}\n"
        ) from exc

    # --- 5. Run container ---
    container_name = f"mlbe-svc-{implementation_id}"

    if host_port is None:
        # Use a simple, deterministic mapping; you already used 18000+id for Perl.
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
        #"--rm",
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
            f"Container run failed (exit {exc.exit_code})\n"
            f"stdout:\n{exc.stdout}\n\nstderr:\n{exc.stderr}\n"
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
