from __future__ import annotations

from typing import Dict, List

from app.adapters.base import LanguageAdapter
from app.adapters.python_adapter import python_adapter

# Central registry of language adapters
_ADAPTERS: Dict[str, LanguageAdapter] = {}


def register_adapter(adapter: LanguageAdapter) -> None:
    """
    Register a LanguageAdapter instance under its .name (lowercased).
    """
    key = adapter.name.lower()
    _ADAPTERS[key] = adapter


# --- Always-available adapters ---

register_adapter(python_adapter)

# --- Optional adapters (Perl, Java, etc.) ---

# We try importing them, but do NOT fail the app if they're missing or broken.
try:
    from app.adapters.perl_adapter import perl_adapter  # type: ignore

    register_adapter(perl_adapter)
except Exception:
    # Perl adapter is optional for now; ignore if not present.
    pass

# You can add more optional adapters like this later:
# try:
#     from app.adapters.java_adapter import java_adapter
#     register_adapter(java_adapter)
# except Exception:
#     pass


def get_adapter(language: str) -> LanguageAdapter:
    """
    Retrieve a registered adapter by language name (case-insensitive).
    Raises KeyError if not found.
    """
    key = language.lower()
    if key not in _ADAPTERS:
        raise KeyError(f"No adapter registered for language='{language}'")
    return _ADAPTERS[key]


def list_adapters() -> List[dict]:
    """
    Return a list of adapter metadata for diagnostics / config endpoint.
    """
    items: List[dict] = []
    for name, adapter in _ADAPTERS.items():
        items.append(
            {
                "name": adapter.name,
                "file_extensions": getattr(adapter, "file_extensions", []),
                "docker_image": getattr(adapter, "docker_image", None),
            }
        )
    return items
