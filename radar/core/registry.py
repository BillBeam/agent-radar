"""Adapter registry — `@register(kind, name)` self-registration + lookup.

Adapters declare themselves; config references them by name. Adding a new
source type / channel / quality rule = drop a file with a `@register` decorator,
no core edit. `load_adapters()` imports the adapter packages so their decorators
run before lookup.
"""
from __future__ import annotations

import importlib
import pkgutil
from collections import defaultdict
from typing import Any, Callable, TypeVar

_REGISTRY: dict[str, dict[str, type]] = defaultdict(dict)

# adapter packages to import so their @register decorators execute
_ADAPTER_PACKAGES = [
    "radar.sources",
    "radar.channels",
    "radar.quality",
    "radar.stages",
    "radar.llm",
    "radar.memory",
]

T = TypeVar("T")


def register(kind: str, name: str) -> Callable[[type[T]], type[T]]:
    def deco(cls: type[T]) -> type[T]:
        if name in _REGISTRY[kind]:
            raise ValueError(f"duplicate {kind} adapter registered: {name!r}")
        _REGISTRY[kind][name] = cls
        cls._registry_kind = kind  # type: ignore[attr-defined]
        cls._registry_name = name  # type: ignore[attr-defined]
        return cls

    return deco


def get(kind: str, name: str) -> type:
    try:
        return _REGISTRY[kind][name]
    except KeyError:
        known = ", ".join(sorted(_REGISTRY[kind])) or "(none)"
        raise KeyError(f"no {kind} adapter named {name!r}; known: {known}") from None


def create(kind: str, name: str, **kwargs: Any) -> Any:
    return get(kind, name)(**kwargs)


def all_of(kind: str) -> dict[str, type]:
    return dict(_REGISTRY[kind])


_loaded = False


def load_adapters() -> None:
    """Import every adapter submodule once so registrations happen."""
    global _loaded
    if _loaded:
        return
    for pkg_name in _ADAPTER_PACKAGES:
        try:
            pkg = importlib.import_module(pkg_name)
        except ModuleNotFoundError:
            continue
        for mod in pkgutil.iter_modules(pkg.__path__):
            if mod.name.startswith("_"):
                continue
            importlib.import_module(f"{pkg_name}.{mod.name}")
    _loaded = True
