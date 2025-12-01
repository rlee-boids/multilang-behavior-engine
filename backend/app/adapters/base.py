from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Type, List


@dataclass
class ServiceHarnessInfo:
    """
    Metadata returned by LanguageAdapter.generate_service_harness.

    context_dir:
        Directory used as Docker build context (usually the cloned repo root).
    dockerfile_path:
        Path to the Dockerfile within context_dir.
    internal_port:
        Port the service will listen on *inside* the container.
    """
    context_dir: Path
    dockerfile_path: Path
    internal_port: int

class LanguageAdapter(ABC):
    """
    Per-language interface used by the conversion engine, test harness,
    and (optionally) runtime service deployment.
    """

    # Must be set by subclasses
    name: str
    file_extensions: List[str]
    docker_image: str  # base image used for test runs; may also be used for services

    def __init__(self) -> None:
        if not getattr(self, "name", None):
            raise ValueError("Adapter must define name")
        if not getattr(self, "file_extensions", None):
            raise ValueError("Adapter must define file_extensions")
        if not getattr(self, "docker_image", None):
            raise ValueError("Adapter must define docker_image")

    def detect(self, path: Path | str) -> bool:
        """
        Default detection: treat `path` as a file path and check extension.
        Subclasses may override with richer heuristics.
        """
        p = Path(path)
        return p.suffix in self.file_extensions

    # ------------------------------------------------------------------
    # Build / test (single-repo)
    # ------------------------------------------------------------------

    @abstractmethod
    def build_command(self, project_root: str | Path) -> list[str] | str | None:
        """
        Return a shell command (string or argv list) to build the project.

        - May return None to indicate "no build step".
        - `project_root` will be the directory where the repo was checked out.
        """
        ...

    @abstractmethod
    def test_command(self, project_root: str | Path) -> list[str] | str:
        """
        Return a shell command (string or argv list) to run tests in a single repo.
        """
        ...

    # ------------------------------------------------------------------
    # Contract-specific harness tests (paired legacy + harness)
    # ------------------------------------------------------------------

    @abstractmethod
    def run_contract_test_command(
        self,
        behavior_id: int,
        contract_id: int | None,
        project_root: str | Path = "/tests",
    ) -> list[str] | str:
        """
        Command to run contract-based tests inside a paired container.

        Layout is typically:
          /code   -> legacy or converted code repo
          /tests  -> harness repo (working dir)

        Implementations can assume:
          - `project_root` is mounted and used as cwd for the command.
          - Language-specific env vars (e.g., PYTHONPATH, PERL5LIB) may need
            to be set here.
        """
        ...

    # ------------------------------------------------------------------
    # Code generation hooks
    # ------------------------------------------------------------------

    @abstractmethod
    def generate_test_code_from_contract(self, contract: object | None, output_path: str | Path) -> None:
        """
        Write language-specific test code for a given BehaviorContract.

        - `contract` will typically be a BehaviorContract ORM object or None.
        - `output_path` is a directory where tests should be written.
        """
        ...

    @abstractmethod
    def generate_skeleton_from_behavior(
        self,
        behavior: object,
        contract: object | None,
        output_path: str | Path,
    ) -> None:
        """
        Generate a skeleton implementation for the target language based on
        a Behavior and optionally its contract.
        """
        ...

    # ---------- NEW: service harness generation ----------

    @abstractmethod
    def generate_service_harness(
        self,
        behavior,
        implementation,
        contract,
        repo_root: Path,
    ) -> ServiceHarnessInfo:
        """
        Generate a minimal web service harness for this implementation
        inside the given repo_root.

        Typical responsibilities:
          - Write service entrypoint (e.g. app/main.py or app.psgi).
          - Write a Dockerfile that:
              - uses self.docker_image as base OR a suitable base
              - installs minimal runtime deps (FastAPI/Plack/etc.)
              - exposes a known internal port
              - CMD runs the service entrypoint.
          - Return ServiceHarnessInfo with:
              - context_dir = repo_root
              - dockerfile_path = repo_root / "Dockerfile"
              - internal_port = (e.g.) 8000 or 5000
        """
        ...

    # ------------------------------------------------------------------
    # Service deployment hooks (runtime UI / HTTP services)
    #
    # These are *not* abstract to keep backwards compatibility.
    # Adapters can override as needed. service_deployer can rely on them.
    # ------------------------------------------------------------------

    def service_image(self) -> str:
        """
        Base image to use for service containers.
        Default: same as docker_image used for tests.
        """
        return self.docker_image

    def service_internal_port(self) -> int:
        """
        Port that the app will listen on inside the container.
        Default: 8000.
        """
        return 8000

    def prepare_service_workspace(self, code_root: str | Path) -> None:
        """
        Called after the repo has been cloned into `code_root` and before
        building the service image.

        Typical uses:
          - Generate a Dockerfile specific to this language.
          - Generate an app entry point (e.g., app.psgi, app.py).
          - Inject any config needed for runtime.

        Default implementation is a no-op.
        """
        return None

    def service_command(self, code_root: str | Path) -> list[str]:
        """
        Command (argv) used as the container entrypoint to run the service.

        The container's WORKDIR will usually be set to `code_root` (e.g., /app).

        Default implementation raises; adapters that support service deployment
        should override this.
        """
        raise NotImplementedError(
            f"service_command is not implemented for language adapter '{self.name}'"
        )


_ADAPTERS: Dict[str, LanguageAdapter] = {}


def register_adapter(adapter_cls: Type[LanguageAdapter]) -> None:
    instance = adapter_cls()
    _ADAPTERS[instance.name.lower()] = instance


def get_adapter(language: str) -> LanguageAdapter:
    language_norm = language.lower()
    if language_norm not in _ADAPTERS:
        raise ValueError(f"No adapter registered for language '{language}'")
    return _ADAPTERS[language_norm]


def list_adapters() -> dict[str, str]:
    """
    Returns mapping language -> docker_image.
    """
    return {name: adapter.docker_image for name, adapter in _ADAPTERS.items()}
