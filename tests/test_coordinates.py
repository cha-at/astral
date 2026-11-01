"""Baseline coordinate checks for AstrBot 4.29.4.

These tests answer one question: are the four workspace resolvers still where
the patch expects them, with the signatures the wrappers mimic, and do the
known consumers still bind them through top level 'from ... import'?
"""

from __future__ import annotations

import ast
import inspect
import os
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

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


def astrbot_version_is_supported() -> bool:
    import astrbot

    version = str(getattr(astrbot, "__version__", ""))
    major = version.split(".")[0]
    return major == BASELINE["astrbot"].split(".")[0]


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
