"""Plugin assembly tests (001 FR-3/FR-7/FR-8/FR-10, 002 FR-2/FR-3/FR-6).

These tests instantiate the real plugin class against the real
astrbot.core.workspace module with a throwaway shared root, then exercise the
state payload, the seeding entry point, the snapshot hook and the read-only
files API.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.datastructures import Headers, QueryParams

import commands
import config
import snapshot
import state as state_mod
import versioning
import ws_shared


class FakeContext:
    """Minimal Context stand-in for the plugin lifecycle."""

    def __init__(self, runtime: str = "local") -> None:
        self.runtime = runtime
        self.registered: list[tuple] = []
        self.conversation_manager = None

    def get_config(self, umo=None):  # noqa: ANN001, ANN201
        return {"provider_settings": {"computer_use_runtime": self.runtime}}

    def register_web_api(self, route, handler, methods, description) -> None:  # noqa: ANN001
        self.registered.append((route, handler, methods, description))


def _make_consumer(module_name: str, attr: str, value) -> types.ModuleType:  # noqa: ANN001
    module = types.ModuleType(module_name)
    setattr(module, attr, value)
    sys.modules[module_name] = module
    return module


@pytest.fixture()
def plugin_factory(tmp_path):
    import astrbot.core.workspace as workspace

    created: list = []

    def build(**overrides):
        shared = tmp_path / "ws"
        raw = {
            "shared_root": str(shared),
            "patch_webchat_projects": True,
            "forbidden_roots": [],
            "quota_gb": 0,
            "memory": {
                "enabled": True,
                "soul_budget_chars": 4000,
                "memory_budget_chars": 8000,
                "soft_limit_chars": 20000,
            },
            "version_control": {"enabled": True, "git_path": ""},
        }
        raw.update(overrides)
        context = FakeContext(runtime=overrides.pop("runtime", "local"))
        import main as main_mod

        plugin = main_mod.AstralPlugin(context, raw)
        created.append(plugin)
        return plugin, shared

    def cleanup() -> None:
        for name in ("astrbot.core.astr_main_agent", "astrbot.core.tools.computer_tools.util"):
            sys.modules.pop(name, None)
        for plugin in created:
            try:
                asyncio.run(plugin.terminate())
            except Exception:
                pass

    _make_consumer(
        "astrbot.core.astr_main_agent",
        "resolve_workspace_root_for_umo",
        workspace.resolve_workspace_root_for_umo,
    )
    _make_consumer(
        "astrbot.core.tools.computer_tools.util",
        "resolve_workspace_root_for_umo",
        workspace.resolve_workspace_root_for_umo,
    )
    try:
        yield build
    finally:
        cleanup()


def _run(coro):
    return asyncio.run(coro)


def test_install_rebinds_consumers_and_reports_state(plugin_factory):
    import astrbot.core.workspace as workspace

    plugin, shared = plugin_factory()
    assert plugin.is_installed() is True

    payload = plugin.state_payload()
    assert payload["installed"] is True
    assert payload["shared_root"] == str(shared.resolve())
    assert payload["baseline"] == ws_shared.BASELINE
    consumers = payload["consumers"]
    assert "astrbot.core.astr_main_agent" in consumers["resolve_workspace_root_for_umo"]
    assert (
        "astrbot.core.tools.computer_tools.util"
        in consumers["resolve_workspace_root_for_umo"]
    )
    assert "astrbot.core.workspace" in consumers["resolve_project_workspace_root"]

    consumer = sys.modules["astrbot.core.astr_main_agent"]
    resolved = _run(consumer.resolve_workspace_root_for_umo("telegram:GroupMessage:1"))
    assert resolved == shared.resolve()
    assert workspace.default_workspace_root("telegram:GroupMessage:1") == shared.resolve()

    for relative in ("skills", "shared", "memory/archive", "archive", ".astral"):
        assert (shared / relative).is_dir()

    state = json.loads((shared / ".astral" / "state.json").read_text(encoding="utf-8"))
    assert state["installed"] is True
    assert state["shared_root"] == str(shared.resolve())
    text = json.dumps(state, ensure_ascii=False).lower()
    for forbidden in ("token", "secret", "password", "api_key"):
        assert forbidden not in text

    audits = [
        json.loads(line)
        for line in (shared / ".astral" / "audit.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    assert audits and all({"ts", "action", "detail"} <= set(item) for item in audits)


def test_initialize_seeds_and_never_overwrites(plugin_factory):
    plugin, shared = plugin_factory()
    _run(plugin.initialize())

    for name in ("SOUL.md", "MEMORY.md", "AGENTS.md", "EXTRA_PROMPT.md"):
        path = shared / name
        assert path.is_file(), name
        assert path.read_text(encoding="utf-8").startswith("<!-- astral:seeded:v1 -->")

    memory = shared / "MEMORY.md"
    original = memory.read_text(encoding="utf-8")
    assert "全局共享" in original
    extra = (shared / "EXTRA_PROMPT.md").read_text(encoding="utf-8")
    assert "astral:managed:begin" in extra and "astral:managed:end" in extra

    memory.write_text(original + "- [2026-11-01] [测试] 不该被覆盖\n", encoding="utf-8")
    before = memory.read_text(encoding="utf-8")
    _run(plugin.initialize())
    assert memory.read_text(encoding="utf-8") == before
    assert "不该被覆盖" in memory.read_text(encoding="utf-8")


def test_snapshot_hook_appends_and_freezes(plugin_factory):
    plugin, shared = plugin_factory()
    _run(plugin.initialize())

    def call(req):
        event = SimpleNamespace(
            unified_msg_origin="telegram:GroupMessage:1",
            is_private_chat=lambda: False,
        )
        _run(plugin.inject_memory_snapshot(event, req))
        return req.system_prompt

    first = call(SimpleNamespace(system_prompt="", conversation=None))
    assert first.startswith("\n" + snapshot.HEADER)
    assert snapshot.SOUL_TITLE in first and snapshot.MEMORY_TITLE in first
    assert "conv=" not in first and "frozen_at" not in first

    memory = shared / "MEMORY.md"
    memory.write_text(
        memory.read_text(encoding="utf-8")
        + "- [2026-11-01] [测试] 冻结验证条目ZZZ\n",
        encoding="utf-8",
    )
    second = call(SimpleNamespace(system_prompt="base", conversation=None))
    assert second.startswith("base\n" + snapshot.HEADER)
    # same conversation: the frozen block is byte-identical
    assert second == "base" + first
    assert "冻结验证条目ZZZ" not in second

    third = call(
        SimpleNamespace(system_prompt="", conversation=SimpleNamespace(cid="conv-123456")),
    )
    assert "冻结验证条目ZZZ" in third

    plugin.options["memory"]["enabled"] = False
    disabled = SimpleNamespace(system_prompt="keep-me")
    event = SimpleNamespace(
        unified_msg_origin="telegram:GroupMessage:1",
        is_private_chat=lambda: False,
    )
    _run(plugin.inject_memory_snapshot(event, disabled))
    assert disabled.system_prompt == "keep-me"


class _FakeRequest:
    method = "GET"
    headers = Headers({})
    cookies: dict = {}
    client = None

    def __init__(self, query: str = "") -> None:
        self.query_params = QueryParams(query)
        self.url = SimpleNamespace(path="/api/v1/plugins/extensions/astrbot_plugin_astral/files")

    async def json(self, default=None):  # noqa: ANN001, ANN201
        return default


def _call_files(plugin, query: str = ""):
    from astrbot.api.web import PluginRequest, bind_request_context

    import webapi

    bound = PluginRequest(_FakeRequest(query), plugin_name="astrbot_plugin_astral")
    with bind_request_context(bound):
        response = _run(webapi.files_handler(plugin))
    return response.status_code, json.loads(response.body)


def test_files_api_contract(plugin_factory):
    plugin, shared = plugin_factory()
    _run(plugin.initialize())

    status, payload = _call_files(plugin)
    assert status == 200
    assert payload["shared_root"] == str(shared.resolve())
    assert payload["memory_soft_limit"] == 20000
    assert {item["name"] for item in payload["files"]} == {
        "SOUL",
        "MEMORY",
        "AGENTS",
        "EXTRA",
    }
    assert all("content" not in item for item in payload["files"])

    status, payload = _call_files(plugin, "name=SOUL")
    assert status == 200
    assert payload["content"] == (shared / "SOUL.md").read_text(encoding="utf-8")
    assert payload["truncated"] is False
    for key in ("name", "path", "abs_path", "size", "mtime", "content"):
        assert key in payload

    status, payload = _call_files(plugin, "name=..%2F..%2Fetc%2Fpasswd")
    assert status == 400
    assert payload["error"] == "unknown_name"

    (shared / "SOUL.md").unlink()
    status, payload = _call_files(plugin, "name=SOUL")
    assert status == 404
    assert payload["error"] == "not_found"


def test_state_endpoint_200_and_503(plugin_factory):
    from astrbot.api.web import bind_request_context, PluginRequest

    import webapi

    plugin, _shared = plugin_factory()
    bound = PluginRequest(_FakeRequest(), plugin_name="astrbot_plugin_astral")
    with bind_request_context(bound):
        response = _run(webapi.state_handler(plugin))
    assert response.status_code == 200
    payload = json.loads(response.body)
    assert payload["installed"] is True
    assert payload["shared_root"]
    assert payload["consumers"]

    ws_shared.uninstall()
    with bind_request_context(bound):
        response = _run(webapi.state_handler(plugin))
    assert response.status_code == 503
    payload = json.loads(response.body)
    assert payload["installed"] is False
    assert payload["reason"]
    assert payload["message"] == payload["reason"]


def test_default_config_installs_into_plugin_data(tmp_path, monkeypatch):
    """An empty config must land in <data>/plugin_data/<plugin>."""
    monkeypatch.setenv("ASTRBOT_ROOT", str(tmp_path / "root"))
    data = tmp_path / "root" / "data"
    import main as main_mod

    plugin = main_mod.AstralPlugin(FakeContext(), {})
    try:
        expected = (data / "plugin_data" / config.PLUGIN_NAME).resolve()
        assert plugin.is_installed() is True
        assert plugin.shared_root() == expected
        assert (expected / ".astral" / "state.json").is_file()
    finally:
        asyncio.run(plugin.terminate())


def test_invalid_config_fails_loud_without_partial_patch(tmp_path, monkeypatch):
    monkeypatch.setenv("ASTRBOT_ROOT", str(tmp_path / "root"))
    data = tmp_path / "root" / "data"
    data.mkdir(parents=True)
    import main as main_mod

    plugin = main_mod.AstralPlugin(
        FakeContext(),
        {"shared_root": str(data / "config" / "nested"), "forbidden_roots": []},
    )
    try:
        assert plugin.is_installed() is False
        payload = plugin.state_payload()
        assert payload["installed"] is False
        assert "forbidden" in (payload["reason"] or "")
        assert payload["consumers"] == {}
    finally:
        ws_shared.uninstall()


def test_patch_project_switch_keeps_umo_shared(tmp_path):
    import main as main_mod

    base = {
        "shared_root": str(tmp_path / "ws"),
        "forbidden_roots": [],
        "quota_gb": 0,
        "memory": config.MEMORY_DEFAULTS,
    }
    plugin = main_mod.AstralPlugin(FakeContext(), dict(base, patch_webchat_projects=False))
    try:
        import astrbot.core.workspace as workspace

        project = SimpleNamespace(workspace_type="session", creator="user", project_id="p1")
        assert asyncio.run(
            workspace.resolve_workspace_root_for_umo("telegram:GroupMessage:9"),
        ) == (tmp_path / "ws").resolve()
        native = workspace.resolve_project_workspace_root(project, fallback_umo="umo")
        assert native == workspace.default_workspace_root("umo") or native != (
            tmp_path / "ws"
        ).resolve()
    finally:
        asyncio.run(plugin.terminate())


def test_status_text_and_warnings(plugin_factory):
    """AC-7.3 / AC-7.4: the chat answer carries the root, state and warnings."""
    plugin, shared = plugin_factory()
    _run(plugin.initialize())

    import state as state_mod

    payload = plugin.state_payload()
    text = state_mod.status_text(payload)
    assert str(shared.resolve()) in text
    assert "已生效" in text
    assert "记忆概况" in text
    assert "/astral memory show" in text
    assert "全局共享" in text
    assert "记忆版本" in text
    assert "/astral commit" in text
    # patch internals stay out of chat output
    assert "consumers" not in text
    assert "831abae85" not in text
    # The version warning is environment-dependent (git may be absent).
    assert [item for item in payload["warnings"] if "git" not in item.lower()] == []

    sandbox_plugin, _ = plugin_factory(runtime="sandbox")
    sandbox_payload = sandbox_plugin.state_payload()
    assert sandbox_payload["runtime"] == "sandbox"
    assert any("沙箱" in warning for warning in sandbox_payload["warnings"])

    # the shipped default runtime is "none": no file tools, memory is read-only
    none_plugin, _ = plugin_factory(runtime="none")
    none_payload = none_plugin.state_payload()
    assert any("本机环境" in warning for warning in none_payload["warnings"])
    assert "写不进去" in state_mod.status_text(none_payload)


def test_memory_show_private_vs_group(plugin_factory):
    """AC-6.2 / AC-7.1: private chat returns content, group only metadata."""
    plugin, shared = plugin_factory()
    _run(plugin.initialize())

    import state as state_mod

    private_event = SimpleNamespace(is_private_chat=lambda: True)
    group_event = SimpleNamespace(is_private_chat=lambda: False)

    private_text = state_mod.memory_show_text(plugin, private_event, "memory")
    assert "MEMORY" in private_text
    assert "群聊里只显示" not in private_text

    group_text = state_mod.memory_show_text(plugin, group_event, "memory")
    assert "群聊里只显示" in group_text
    assert "全局共享" in group_text
    assert "私聊" in group_text

    assert "未知文件" in state_mod.memory_show_text(plugin, private_event, "nope")

    long_text = "x" * 4000
    (shared / "MEMORY.md").write_text(long_text, encoding="utf-8")
    truncated = state_mod.memory_show_text(plugin, private_event, "memory")
    assert len(truncated) < 4000
    assert str(shared / "MEMORY.md") in truncated


def test_all_endpoints_503_after_terminate(plugin_factory):
    """AC-10.1 / FR-10: every endpoint self-checks the patch state."""
    from astrbot.api.web import PluginRequest, bind_request_context

    import webapi

    plugin, _shared = plugin_factory()
    _run(plugin.terminate())

    bound = PluginRequest(_FakeRequest(), plugin_name="astrbot_plugin_astral")
    with bind_request_context(bound):
        state_response = _run(webapi.state_handler(plugin))
        files_response = _run(webapi.files_handler(plugin))
        clean_response = _run(webapi.clean_handler(plugin))

    for response in (state_response, files_response, clean_response):
        assert response.status_code == 503
        body = json.loads(response.body)
        assert body["installed"] is False
        assert body["message"]


def _git_available() -> bool:
    return shutil.which("git") is not None


def test_versioning_repo_initialized_on_seed(plugin_factory):
    if not _git_available():
        pytest.skip("git is not installed")
    plugin, shared = plugin_factory()
    _run(plugin.initialize())

    assert (shared / ".git").is_dir()
    summary = versioning.summary(shared)
    assert summary["enabled"] is True
    assert summary["available"] is True
    assert summary["initialized"] is True
    assert summary["head"]
    assert summary["count"] == 1

    tracked = subprocess.run(
        ["git", "-C", str(shared), "ls-files"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    assert {line for line in tracked if line.strip()} == set(versioning.TRACKED_FILES)


def test_versioning_commit_and_recover_commands(plugin_factory):
    if not _git_available():
        pytest.skip("git is not installed")
    plugin, shared = plugin_factory()
    _run(plugin.initialize())

    memory = shared / "MEMORY.md"
    memory.write_text("v1 记忆\n", encoding="utf-8")
    commit_text = _run(commands.save_memory(plugin))
    assert "已保存记忆" in commit_text
    head = versioning.summary(shared)["head"]
    assert head and head in commit_text

    event = SimpleNamespace(
        unified_msg_origin="telegram:GroupMessage:1",
        is_private_chat=lambda: False,
    )
    req = SimpleNamespace(system_prompt="", conversation=None)
    _run(plugin.inject_memory_snapshot(event, req))
    assert len(plugin._cache) == 1
    assert "v1 记忆" in req.system_prompt

    memory.write_text("v2 记忆\n", encoding="utf-8")
    recover_text = _run(commands.recover_memory(plugin, head))
    assert "已回退到" in recover_text
    assert memory.read_text(encoding="utf-8") == "v1 记忆\n"
    assert len(plugin._cache) == 0

    list_text = _run(commands.recover_memory(plugin, ""))
    assert "最近的记忆版本" in list_text
    assert head in list_text

    payload = plugin.state_payload()
    assert payload["versioning"]["initialized"] is True
    assert payload["versioning"]["head"] != head
    status = state_mod.status_text(payload)
    assert "记忆版本" in status
    assert "/astral commit" in status


def test_version_control_disabled(plugin_factory):
    plugin, shared = plugin_factory(version_control={"enabled": False})
    _run(plugin.initialize())

    assert not (shared / ".git").exists()
    text = _run(commands.save_memory(plugin))
    assert "已关闭" in text
    payload = plugin.state_payload()
    assert payload["versioning"]["enabled"] is False
    assert "已关闭" in state_mod.status_text(payload)


def test_version_control_git_missing(plugin_factory, monkeypatch):
    monkeypatch.setattr(versioning, "find_git", lambda configured="": None)
    plugin, shared = plugin_factory()
    _run(plugin.initialize())

    assert not (shared / ".git").exists()
    payload = plugin.state_payload()
    assert payload["versioning"]["available"] is False
    assert any("git" in warning.lower() for warning in payload["warnings"])
    text = _run(commands.save_memory(plugin))
    assert "没找到 git" in text or "不可用" in text


def test_recover_without_target_lists_versions(plugin_factory):
    if not _git_available():
        pytest.skip("git is not installed")
    plugin, shared = plugin_factory()
    _run(plugin.initialize())

    (shared / "MEMORY.md").write_text("checkpoint\n", encoding="utf-8")
    _run(commands.save_memory(plugin))
    text = _run(commands.recover_memory(plugin, ""))
    assert "最近的记忆版本" in text
    assert "回退：/astral recover <短哈希>" in text
