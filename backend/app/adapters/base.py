from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, Type


class LanguageAdapter(ABC):
    """
    Per-language interface used by the conversion engine and test harness.
    """

    name: str
    file_extensions: tuple[str, ...]
    docker_image: str

    def __init__(self) -> None:
        if not getattr(self, "name", None):
            raise ValueError("Adapter must define name")
        if not getattr(self, "file_extensions", None):
            raise ValueError("Adapter must define file_extensions")
        if not getattr(self, "docker_image", None):
            raise ValueError("Adapter must define docker_image")

    def detect(self, path: Path) -> bool:
        return path.suffix in self.file_extensions

    @abstractmethod
    def build_command(self, project_root: Path) -> list[str]:
        ...

    @abstractmethod
    def test_command(self, project_root: Path) -> list[str]:
        ...

    @abstractmethod
    def run_contract_test_command(self, behavior_id: int, contract_id: int) -> list[str]:
        """
        Command to run contract-based tests inside container.
        """
        ...

    @abstractmethod
    def generate_test_code_from_contract(self, contract: dict, output_path: Path) -> None:
        """
        Write language-specific test code to output_path.
        """
        ...

    @abstractmethod
    def generate_skeleton_from_behavior(self, behavior: dict, contract: dict, output_path: Path) -> None:
        """
        Generate a skeleton implementation for the target language.
        """
        ...


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
