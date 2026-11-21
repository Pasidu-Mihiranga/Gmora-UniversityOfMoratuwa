"""Composition root (ARC-3, ARC-10): the only place that imports module packages and wires them."""

from __future__ import annotations

import importlib

MODULES: tuple[str, ...] = (
    "identity",
    "consent",
    "record",
    "audit",
    "safety",
    "navigation",
    "booking",
    "care",
    "imaging",
    "reports",
    "knowledge",
    "private",
    "memory",
    "notifications",
    "cases",
    "verifier",
    "agents",
    "ops",
)

_MODULES_WITH_TABLES: tuple[str, ...] = (
    "identity",
    "consent",
    "record",
    "audit",
    "booking",
    "care",
    "imaging",
    "reports",
    "knowledge",
    "private",
    "memory",
    "notifications",
    "cases",
    "verifier",
    "agents",
    "ops",
)


def import_all_models() -> None:
    """Register every table on Base.metadata (Alembic and tests)."""
    importlib.import_module("suwapath.platform.models")
    for name in _MODULES_WITH_TABLES:
        importlib.import_module(f"suwapath.modules.{name}.adapters.models")
