"""Baseline coordinate checks for the declared AstrBot support window.

These tests answer one question: are the four workspace resolvers still where
the patch expects them, with the signatures the wrappers mimic, and do the
known consumers still bind them through top level 'from ... import'?

The tested baseline is AstrBot 4.29.4; the support window declared in
metadata.yaml is wider (>=4.27.0,<5) because the resolvers are unchanged
across it. The version gate below reads that same metadata so the tests and
the market gate can never drift apart.
"""

from __future__ import annotations

import ast
import inspect
import os
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from packaging.specifiers import SpecifierSet
from packaging.version import InvalidVersion, Version

BASELINE = {"astrbot": "4.29.4", "commit": "831abae85"}

EXPECTED_SIGNATURES: dict[str, dict[str, Any]] = {
    "resolve_workspace_root_for_umo": {
        "params": ["umo", "db"],
        "kwonly": [],
        "async": True,
    },
    "resolve_project_workspace_root": {
        "params": ["project", "fallback_umo"],
        "kwonly": ["fallback_umo"],
        "async": False,
    },
    "default_workspace_root": {"params": ["umo"], "kwonly": [], "async": False},
    "project_workspace_root": {"params": ["project_id"], "kwonly": [], "async": False},
}

EXPECTED_CONSUMERS: dict[str, tuple[str, ...]] = {
    "astrbot/core/astr_main_agent.py": ("resolve_workspace_root_for_umo",),
    "astrbot/core/tools/computer_tools/util.py": ("resolve_workspace_root_for_umo",),
    "astrbot/dashboard/services/chatui_project_service.py": (
        "resolve_project_workspace_root",
    ),
}


def _astrbot_source_root() -> Path | None:
    """Locate the AstrBot source tree used for the coordinate checks."""
    env = Path(__file__).resolve().parent.parent
    candidates: list[Path] = []
    override = os.environ.get("ASTRBOT_SOURCE")
    if override:
        candidates.append(Path(override))
    try:
        import astrbot

        candidates.append(Path(astrbot.__file__).resolve().parent.parent)
    except Exception:
        pass
    candidates.extend(
        [
            env.parent / "AstrBot-code",
            env.parent.parent / "AstrBot-code",
        ]
    )
    for candidate in candidates:
        if (candidate / "astrbot" / "core" / "workspace.py").is_file():
            return candidate
    return None


def detect_drift(module: ModuleType) -> list[str]:
    """Return the list of drifted symbols for a workspace-like module."""
    problems: list[str] = []
    for name, expected in EXPECTED_SIGNATURES.items():
        target = getattr(module, name, None)
        if target is None or not callable(target):
            problems.append(f"missing: {name}")
            continue
        signature = inspect.signature(target)
        params = [
            param.name
            for param in signature.parameters.values()
            if param.kind
            in (param.POSITIONAL_ONLY, param.POSITIONAL_OR_KEYWORD, param.KEYWORD_ONLY)
        ]
        if sorted(params) != sorted(expected["params"]):
            problems.append(f"signature: {name}({', '.join(params)})")
        for keyword_only in expected["kwonly"]:
            param = signature.parameters.get(keyword_only)
            if param is None or param.kind is not param.KEYWORD_ONLY:
                problems.append(f"keyword-only: {name}.{keyword_only}")
        if expected["async"] and not inspect.iscoroutinefunction(target):
            problems.append(f"not async: {name}")
        if not expected["async"] and inspect.iscoroutinefunction(target):
            problems.append(f"unexpectedly async: {name}")
    return problems


def test_workspace_functions_match_baseline():
    pytest.importorskip("astrbot.core.workspace")
    import astrbot.core.workspace as workspace

    assert astrbot_version_is_supported()
    assert detect_drift(workspace) == []


def _declared_spec() -> SpecifierSet:
    """Read astrbot_version from metadata.yaml (single source of truth)."""
    metadata = Path(__file__).resolve().parent.parent / "metadata.yaml"
    for line in metadata.read_text(encoding="utf-8").splitlines():
        if line.startswith("astrbot_version:"):
            return SpecifierSet(line.split(":", 1)[1].strip().strip('"'))
    raise AssertionError("metadata.yaml has no astrbot_version")


def astrbot_version_is_supported() -> bool:
    """True when the running AstrBot sits inside the declared support window."""
    import astrbot

    version = str(getattr(astrbot, "__version__", ""))
    try:
        return Version(version) in _declared_spec()
    except InvalidVersion:
        return False


def test_drift_detection_flags_renamed_symbol():
    """A stub module with one renamed resolver must be reported (AC-13.1)."""
    import astrbot.core.workspace as workspace

    stub = ModuleType("stub_workspace")
    for name in EXPECTED_SIGNATURES:
        setattr(stub, name, getattr(workspace, name))
    delattr(stub, "resolve_project_workspace_root")

    problems = detect_drift(stub)

    assert any("resolve_project_workspace_root" in item for item in problems), problems


def test_consumers_still_use_top_level_imports():
    root = _astrbot_source_root()
    if root is None:
        pytest.skip("AstrBot source tree not available; run on a checked-out baseline")
    problems: list[str] = []
    for relative, names in EXPECTED_CONSUMERS.items():
        path = root / relative
        if not path.is_file():
            problems.append(f"missing file: {relative}")
            continue
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        imported: set[str] = set()
        deferred: set[str] = set()
        nested_nodes = {
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda)
        }
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and node.module == "astrbot.core.workspace":
                imported.update(alias.name for alias in node.names)
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.module != "astrbot.core.workspace":
                continue
            if node not in tree.body:
                deferred.update(alias.name for alias in node.names)
        for name in names:
            if name in deferred and name not in imported:
                problems.append(f"deferred import: {relative}:{name}")
            elif name not in imported:
                problems.append(f"missing import: {relative}:{name}")
        del nested_nodes
    assert problems == [], problems
