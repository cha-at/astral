"""Tests for the shared-workspace patch core (ws_shared.py).

Covers AC-3.1, AC-3.2, AC-3.4, AC-3.5, AC-3.6, AC-4.1, AC-4.3, AC-5.2,
AC-5.3 and AC-6.1 from specs/001-shared-workspace/spec.md.

These tests patch the real astrbot.core.workspace module, so every test runs
through fixtures that restore the baseline in teardown. Consumer modules are
in-memory stand-ins for the three real AstrBot consumers documented in
001 plan section 8.2.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import sys
import types
from pathlib import Path

import pytest

import astrbot.core.workspace as ws

import ws_shared

AGENT_MODULE = "astrbot.core.astr_main_agent"
TOOLS_MODULE = "astrbot.core.tools.computer_tools.util"
DASH_MODULE = "astrbot.dashboard.services.chatui_project_service"
LATE_MODULE = "astrbot.core.tools.late_workspace_consumer"

UMO = "telegram:GroupMessage:123456789"

_MISSING = object()


def _run(coro):
    """Run a coroutine in a fresh event loop."""
    return asyncio.run(coro)


@pytest.fixture()
def patch_env(monkeypatch):
    """Clean patch baseline plus consumer/root factories."""
    ws_shared.uninstall()

    baseline = {name: getattr(ws, name) for name in ws_shared.ALL_FUNCS}
    saved = {name: ws.__dict__.get(name, _MISSING) for name in ws_shared.ALL_FUNCS}
    saved_state = ws.__dict__.get(ws_shared.STATE_ATTR, _MISSING)
    consumers = {}

    def add_consumer(module_name, *func_names, use_module_attr=False):
        """Register a fake consumer module holding the given functions.

        use_module_attr mimics an import that happens after install: the
        attribute is taken from the already patched source module.
        """
        module = types.ModuleType(module_name)
        for func_name in func_names:
            value = getattr(ws, func_name) if use_module_attr else baseline[func_name]
            setattr(module, func_name, value)
        monkeypatch.setitem(sys.modules, module_name, module)
        consumers[module_name] = module
        return module

    def shared_root(tmp_path, name="shared"):
        root = tmp_path / name
        root.mkdir(parents=True, exist_ok=True)
        return root

    env = types.SimpleNamespace(
        baseline=baseline,
        consumers=consumers,
        add_consumer=add_consumer,
        shared_root=shared_root,
    )
    try:
        yield env
    finally:
        try:
            ws_shared.uninstall()
        finally:
            for name, value in saved.items():
                if value is _MISSING:
                    ws.__dict__.pop(name, None)
                else:
                    setattr(ws, name, value)
            if saved_state is _MISSING:
                ws.__dict__.pop(ws_shared.STATE_ATTR, None)
            else:
                setattr(ws, ws_shared.STATE_ATTR, saved_state)


@pytest.fixture()
def stub_workspace(monkeypatch):
    """Swap astrbot.core.workspace for a stub module (AC-6.1)."""
    real_symbols = {name: getattr(ws, name) for name in ws_shared.ALL_FUNCS}

    @contextlib.contextmanager
    def build(drop=None):
        stub = types.ModuleType(ws_shared.TARGET_MODULE)
        for name, value in real_symbols.items():
            if name != drop:
                setattr(stub, name, value)
        with monkeypatch.context() as ctx:
            ctx.setitem(sys.modules, ws_shared.TARGET_MODULE, stub)
            yield stub

    return build


def test_module_constants():
    assert ws_shared.TARGET_MODULE == "astrbot.core.workspace"
    assert ws_shared.STATE_ATTR == "__astral_patch_state__"
    assert ws_shared.BASELINE["astrbot"] == "4.29.4"
    assert ws_shared.BASELINE["commit"] == "831abae85"
    assert set(ws_shared.ALL_FUNCS) == {
        "resolve_workspace_root_for_umo",
        "resolve_project_workspace_root",
        "default_workspace_root",
        "project_workspace_root",
    }
    assert set(ws_shared.PROJECT_FUNCS) == {
        "resolve_project_workspace_root",
        "project_workspace_root",
    }


def test_install_writes_both_source_module_and_consumers(patch_env, tmp_path):
    """AC-3.1 / AC-3.2 / AC-3.5: the double write rewrites both sides."""
    agent = patch_env.add_consumer(AGENT_MODULE, "resolve_workspace_root_for_umo")
    tools = patch_env.add_consumer(TOOLS_MODULE, "resolve_workspace_root_for_umo")
    dashboard = patch_env.add_consumer(DASH_MODULE, "resolve_project_workspace_root")
    root = patch_env.shared_root(tmp_path)

    report = ws_shared.install(root)

    assert report["installed"] is True
    assert Path(report["root"]) == root.resolve()
    assert set(report["patched"]) == set(ws_shared.ALL_FUNCS)
    assert isinstance(report["skipped"], list)
    assert report["reason"] is None
    assert any(AGENT_MODULE in str(entry) for entry in report["rebound"])
    assert any(TOOLS_MODULE in str(entry) for entry in report["rebound"])

    # Side (1): the source module holds the wrappers.
    for name in ws_shared.ALL_FUNCS:
        assert getattr(getattr(ws, name), "__astral_name__", None) == name

    # Side (2): modules that imported the originals before install are rebound.
    assert agent.resolve_workspace_root_for_umo is ws.resolve_workspace_root_for_umo
    assert tools.resolve_workspace_root_for_umo is ws.resolve_workspace_root_for_umo
    assert (
        dashboard.resolve_project_workspace_root
        is ws.resolve_project_workspace_root
    )


def test_status_lists_consumers_sorted_and_deduped(patch_env, tmp_path):
    """AC-3.1 / AC-3.2: status reports the consumers per function."""
    patch_env.add_consumer(AGENT_MODULE, "resolve_workspace_root_for_umo")
    patch_env.add_consumer(TOOLS_MODULE, "resolve_workspace_root_for_umo")
    patch_env.add_consumer(DASH_MODULE, "resolve_project_workspace_root")
    root = patch_env.shared_root(tmp_path)

    ws_shared.install(root)
    state = ws_shared.status()

    assert state["installed"] is True
    assert Path(state["root"]) == root.resolve()
    assert state["baseline"] == ws_shared.BASELINE
    assert isinstance(state["installed_at"], str) and state["installed_at"]
    assert state["patch_project"] is True
    assert state["reason"] is None

    consumers = state["consumers"]
    assert set(consumers) == set(ws_shared.ALL_FUNCS)
    for name, modules in consumers.items():
        assert modules == sorted(set(modules)), name
        # The definition module itself always consumes the symbol.
        assert ws_shared.TARGET_MODULE in modules, name

    umo_consumers = consumers["resolve_workspace_root_for_umo"]
    assert AGENT_MODULE in umo_consumers
    assert TOOLS_MODULE in umo_consumers
    assert DASH_MODULE in consumers["resolve_project_workspace_root"]


def test_positive_control_waits_for_await_and_returns_root(patch_env, tmp_path):
    """AC-3.5 / AC-3.6: the async wrapper is a coroutine returning the root."""
    agent = patch_env.add_consumer(
        AGENT_MODULE,
        "resolve_workspace_root_for_umo",
        "default_workspace_root",
    )
    root = patch_env.shared_root(tmp_path)
    ws_shared.install(root)
    expected = root.resolve()

    assert inspect.iscoroutinefunction(agent.resolve_workspace_root_for_umo)
    assert _run(agent.resolve_workspace_root_for_umo(UMO)) == expected
    assert _run(agent.resolve_workspace_root_for_umo(UMO, None)) == expected
    assert agent.default_workspace_root(UMO) == expected


def test_all_patched_functions_return_shared_root(patch_env, tmp_path):
    """FR-3: all four symbols are replaced by constant-returning wrappers."""
    root = patch_env.shared_root(tmp_path)
    ws_shared.install(root)
    expected = root.resolve()
    project = types.SimpleNamespace(
        workspace_type="project",
        project_id="p-1",
        creator="admin",
        workspace_path=None,
    )

    assert _run(ws.resolve_workspace_root_for_umo(UMO)) == expected
    assert ws.resolve_project_workspace_root(project, fallback_umo=UMO) == expected
    assert ws.default_workspace_root(UMO) == expected
    assert ws.project_workspace_root("p-1") == expected


def test_consumer_imported_after_install_gets_shared_root(patch_env, tmp_path):
    """AC-3.5: a consumer imported after the patch still gets the root."""
    root = patch_env.shared_root(tmp_path)
    ws_shared.install(root)
    late = patch_env.add_consumer(
        LATE_MODULE,
        "resolve_workspace_root_for_umo",
        use_module_attr=True,
    )

    assert _run(late.resolve_workspace_root_for_umo(UMO)) == root.resolve()


def test_negative_control_without_module_scan(patch_env, tmp_path):
    """AC-3.4: source-only patch leaves stale consumers on native paths."""
    agent = patch_env.add_consumer(AGENT_MODULE, "resolve_workspace_root_for_umo")
    tools = patch_env.add_consumer(TOOLS_MODULE, "default_workspace_root")
    native_default = ws.default_workspace_root(UMO)  # captured before patching
    original_resolve = ws.resolve_workspace_root_for_umo
    root = patch_env.shared_root(tmp_path)

    report = ws_shared.install(root, scan_modules=False)

    assert report["installed"] is True
    # Side (1) is applied...
    assert ws.default_workspace_root(UMO) == root.resolve()
    # ...but the already-imported consumer still resolves the native path.
    assert tools.default_workspace_root(UMO) == native_default
    assert tools.default_workspace_root(UMO) != root.resolve()
    assert agent.resolve_workspace_root_for_umo is original_resolve
    assert getattr(agent.resolve_workspace_root_for_umo, "__astral_name__", None) is None


def test_install_is_idempotent_and_keeps_originals(patch_env, tmp_path):
    """AC-4.1 / AC-4.3: repeat installs never stack wrappers."""
    agent = patch_env.add_consumer(AGENT_MODULE, "resolve_workspace_root_for_umo")
    root_a = patch_env.shared_root(tmp_path, "root-a")
    root_b = patch_env.shared_root(tmp_path, "root-b")

    ws_shared.install(root_a)
    for _ in range(2):
        report = ws_shared.install(root_a)
        assert report["installed"] is True

    originals = ws_shared.originals()
    assert set(originals) == set(ws_shared.ALL_FUNCS)
    for name in ws_shared.ALL_FUNCS:
        assert originals[name] is patch_env.baseline[name]
    assert (
        getattr(ws.resolve_workspace_root_for_umo, "__astral_name__", None)
        == "resolve_workspace_root_for_umo"
    )

    # Re-install without uninstall (AC-4.3): root follows, originals stay put.
    ws_shared.install(root_b)
    assert _run(ws.resolve_workspace_root_for_umo(UMO)) == root_b.resolve()
    assert _run(agent.resolve_workspace_root_for_umo(UMO)) == root_b.resolve()
    assert (
        ws_shared.originals()["resolve_workspace_root_for_umo"]
        is patch_env.baseline["resolve_workspace_root_for_umo"]
    )
    assert Path(ws_shared.status()["root"]) == root_b.resolve()


def test_originals_is_empty_before_install(patch_env):
    ws_shared.uninstall()
    assert ws_shared.originals() == {}


def test_uninstall_restores_every_reference(patch_env, tmp_path):
    """AC-5.2: uninstall restores the originals everywhere and drops state."""
    agent = patch_env.add_consumer(AGENT_MODULE, "resolve_workspace_root_for_umo")
    tools = patch_env.add_consumer(
        TOOLS_MODULE, "resolve_workspace_root_for_umo", "default_workspace_root"
    )
    dashboard = patch_env.add_consumer(DASH_MODULE, "resolve_project_workspace_root")
    root = patch_env.shared_root(tmp_path)
    native = ws.default_workspace_root(UMO)

    ws_shared.install(root)
    report = ws_shared.uninstall()

    assert report["uninstalled"] is True
    for name in ws_shared.ALL_FUNCS:
        assert getattr(ws, name) is patch_env.baseline[name]
    assert agent.resolve_workspace_root_for_umo is patch_env.baseline[
        "resolve_workspace_root_for_umo"
    ]
    assert tools.default_workspace_root is patch_env.baseline["default_workspace_root"]
    assert dashboard.resolve_project_workspace_root is patch_env.baseline[
        "resolve_project_workspace_root"
    ]
    assert not hasattr(ws, ws_shared.STATE_ATTR)
    assert _run(ws.resolve_workspace_root_for_umo(UMO)) == native


def test_uninstall_is_safe_to_repeat(patch_env):
    """AC-5.3: repeated uninstall returns uninstalled=False and never raises."""
    assert ws_shared.uninstall()["uninstalled"] is False
    assert ws_shared.uninstall()["uninstalled"] is False


def test_status_without_install_reports_reason(patch_env):
    ws_shared.uninstall()
    state = ws_shared.status()

    assert state["installed"] is False
    assert state["root"] is None
    assert state["installed_at"] is None
    assert isinstance(state["reason"], str) and state["reason"]
    assert state["baseline"] == ws_shared.BASELINE
    assert all(not modules for modules in state["consumers"].values())


def test_wrapper_signatures_match_baseline(patch_env, tmp_path):
    """AC-3.6: wrapper signatures stay compatible with the baseline."""
    ws_shared.install(patch_env.shared_root(tmp_path))

    assert inspect.iscoroutinefunction(ws.resolve_workspace_root_for_umo)
    params = list(inspect.signature(ws.resolve_workspace_root_for_umo).parameters.values())
    assert [param.name for param in params] == ["umo", "db"]
    assert params[1].default is None

    params = list(inspect.signature(ws.resolve_project_workspace_root).parameters.values())
    assert [param.name for param in params] == ["project", "fallback_umo"]
    assert params[1].kind is inspect.Parameter.KEYWORD_ONLY

    params = list(inspect.signature(ws.default_workspace_root).parameters.values())
    assert [param.name for param in params] == ["umo"]

    params = list(inspect.signature(ws.project_workspace_root).parameters.values())
    assert [param.name for param in params] == ["project_id"]


def test_patch_project_false_keeps_project_resolver(patch_env, tmp_path):
    """AC-9.3 (testable part): only the UMO resolver is redirected."""
    root = patch_env.shared_root(tmp_path)

    report = ws_shared.install(root, patch_project=False)

    assert report["installed"] is True
    assert "resolve_project_workspace_root" not in report["patched"]
    assert ws.resolve_project_workspace_root is patch_env.baseline[
        "resolve_project_workspace_root"
    ]
    assert getattr(ws.resolve_project_workspace_root, "__astral_name__", None) is None
    assert _run(ws.resolve_workspace_root_for_umo(UMO)) == root.resolve()
    assert ws_shared.status()["patch_project"] is False


def test_install_fails_when_a_function_is_missing(patch_env, stub_workspace, tmp_path):
    """AC-6.1: a missing symbol aborts the whole install."""
    root = patch_env.shared_root(tmp_path)
    with stub_workspace(drop="resolve_project_workspace_root") as stub:
        report = ws_shared.install(root)

        assert report["installed"] is False
        assert "resolve_project_workspace_root" in (report["reason"] or "")
        assert report["patched"] == []
        assert report["rebound"] == []
        assert not hasattr(stub, ws_shared.STATE_ATTR)
        for name in ws_shared.ALL_FUNCS:
            if name == "resolve_project_workspace_root":
                continue
            assert getattr(stub, name) is patch_env.baseline[name]

        state = ws_shared.status()

    assert state["installed"] is False
    assert "resolve_project_workspace_root" in (state["reason"] or "")


def test_install_fails_when_the_module_cannot_be_imported(patch_env, tmp_path, monkeypatch):
    """FR-6: an import failure is reported, never raised."""
    with monkeypatch.context() as ctx:
        ctx.setitem(sys.modules, ws_shared.TARGET_MODULE, None)
        report = ws_shared.install(patch_env.shared_root(tmp_path))

    assert report["installed"] is False
    assert isinstance(report["reason"], str) and report["reason"]
    assert report["patched"] == []
    assert report["rebound"] == []
