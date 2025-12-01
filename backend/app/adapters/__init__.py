# backend/app/adapters/__init__.py
from __future__ import annotations

from typing import Dict

from app.adapters.base import LanguageAdapter
from app.adapters.perl_adapter import perl_adapter
from app.adapters.python_adapter import python_adapter

_ADAPTERS: Dict[str, LanguageAdapter] = {
    "perl": perl_adapter,
    "python": python_adapter,
    # later: "java": java_adapter, etc.
}


def get_adapter(language: str) -> LanguageAdapter:
    key = language.lower()
    if key not in _ADAPTERS:
        raise KeyError(f"No adapter registered for language '{language}'")
    return _ADAPTERS[key]


def list_adapters() -> dict[str, dict]:
    """
    For config/diagnostic endpoint.
    """
    return {
        name: {
            "name": adapter.name,
            "file_extensions": list(adapter.file_extensions),
            "runtime_image": adapter.runtime_image,
            "service_internal_port": adapter.service_internal_port,
        }
        for name, adapter in _ADAPTERS.items()
    }
