"""Registry of available evals. Add new evals here."""
from __future__ import annotations

from typing import Callable

from .base import Eval

_REGISTRY: dict[str, Callable[[], Eval]] = {}


def register(eval_id: str, factory: Callable[[], Eval]) -> None:
    _REGISTRY[eval_id] = factory


def get(eval_id: str) -> Eval:
    if eval_id not in _REGISTRY:
        raise KeyError(f"unknown eval '{eval_id}'. known: {list(_REGISTRY)}")
    return _REGISTRY[eval_id]()


def all_ids() -> list[str]:
    return sorted(_REGISTRY)


def _autoload() -> None:
    # imports register evals as a side-effect
    for mod_name in ("tutorbench", "matscibench", "androidbench"):
        try:
            __import__(f"grok_evals.evals.{mod_name}")
        except Exception as e:  # surface but don't crash registry import
            print(f"[registry] {mod_name} load failed: {e}")


_autoload()
