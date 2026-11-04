"""Static contract tests for the Astral read-only page and user documents.

These tests use the standard library only: they read files shipped in this
repository and never import astrbot. They are the executable form of
docs/dev-notes.md (read-only page).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
VIEW_DIR = REPO_ROOT / "views" / "astral"
INDEX_HTML = VIEW_DIR / "index.html"
I18N_ZH = REPO_ROOT / ".astrbot-plugin" / "i18n" / "zh-CN.json"
I18N_EN = REPO_ROOT / ".astrbot-plugin" / "i18n" / "en-US.json"
README = REPO_ROOT / "README.md"
CHANGELOG = REPO_ROOT / "CHANGELOG.md"

BRIDGE_SDK_URL = "/api/plugin/page/bridge-sdk.js"

CONFIG_KEYS = (
    "shared_root",
    "patch_webchat_projects",
    "forbidden_roots",
    "quota_gb",
    "memory.enabled",
    "memory.soul_budget_chars",
    "memory.memory_budget_chars",
    "memory.soft_limit_chars",
    "version_control.enabled",
    "version_control.git_path",
)


def _read(path: Path) -> str:
    assert path.is_file(), f"missing file: {path}"
    return path.read_text(encoding="utf-8")


def _html() -> str:
    return _read(INDEX_HTML)


def test_page_has_no_editable_controls() -> None:
    html = _html().lower()
    for marker in ("<textarea", "contenteditable", "<input", "<select", "<form", "<button"):
        assert marker not in html, f"read-only page must not contain {marker!r}"


def test_page_has_no_write_actions() -> None:
    html = _html().lower()
    for marker in ("apipost", "api:post", "files:upload", "download", "upload"):
        assert marker not in html, f"read-only page must not expose {marker!r}"


def test_page_renders_dynamic_text_without_html_injection() -> None:
    html = _html()
    lowered = html.lower()
    for marker in ("innerhtml", "outerhtml", "insertadjacenthtml", "document.write"):
        assert marker not in lowered, f"page must not use {marker}"
    assert html.count("textContent") >= 10


def test_page_banner_discloses_global_sharing() -> None:
    html = _html()
    assert "全局共享" in html
    assert "所有平台" in html
    assert "所有群" in html


def test_page_loads_bridge_sdk_from_astrbot_route() -> None:
    html = _html()
    assert 'src="' + BRIDGE_SDK_URL + '"' in html
    script_sources = re.findall(r'<script[^>]*\bsrc="([^"]+)"', html, re.IGNORECASE)
    assert script_sources == [BRIDGE_SDK_URL]


def test_page_calls_only_state_and_files_get() -> None:
    html = _html()
    assert re.search(r"bridge\.ready\(\s*\)", html)
    assert re.search(r'apiGet\(\s*"state"\s*\)', html)
    assert re.search(r'apiGet\(\s*"files"\s*\)', html)
    assert re.search(r'apiGet\(\s*"files"\s*,\s*\{\s*name', html)
    endpoints = set(re.findall(r'apiGet\(\s*["\']([^"\']+)["\']', html))
    assert endpoints == {"state", "files"}


def test_page_preview_uses_preformatted_block() -> None:
    html = _html()
    blocks = re.findall(r"<pre\b[^>]*>(.*?)</pre>", html, re.IGNORECASE | re.DOTALL)
    assert blocks, "page must render the preview inside a pre element"
    assert any("加载中" in block for block in blocks)


def test_page_handles_missing_bridge_without_alert() -> None:
    html = _html()
    assert "请在 AstrBot WebUI 的插件页中打开" in html
    assert "alert(" not in html
    assert 'role="alert"' in html
    assert 'id="error"' in html


def test_page_supports_dark_mode_and_narrow_viewport() -> None:
    html = _html()
    assert "prefers-color-scheme: dark" in html
    assert 'name="viewport"' in html
    assert re.search(r"@media\s*\(max-width:\s*380px\)", html)
    assert "max-width: 100%" in html
    assert "min-width: 0" in html


def test_page_has_no_external_dependencies() -> None:
    html = _html()
    source = "https://github.com/cha-at/astral"
    assert source in html, "page should link to its source (AGPL section 13)"
    without_source = html.replace(source, "")
    assert "http://" not in without_source
    assert "https://" not in without_source
    assert "@import" not in html
    assert "<link" not in html.lower()


def test_page_i18n_metadata() -> None:
    for path in (I18N_ZH, I18N_EN):
        payload = json.loads(_read(path))
        view = payload["views"]["astral"]
        assert view["title"] == "Astral"
        assert view["description"].strip()


def test_page_lists_all_four_files() -> None:
    html = _html()
    for name in ("SOUL.md", "MEMORY.md", "AGENTS.md", "EXTRA_PROMPT.md"):
        assert name in html, f"page must reference {name}"
    assert "memory_soft_limit" in html
    assert "managed_ok" in html


def test_readme_has_required_disclosures() -> None:
    readme = _read(README)
    for phrase in (
        "全局共享",
        "所有群可见",
        "不能修改内置 WebUI",
        "需要单独备份共享根",
        "plugin_data/astrbot_plugin_astral",
        "所有平台",
        "monkeypatch",
        "computer_use_runtime",
        "多进程",
        "Copy-Item -Recurse",
    ):
        assert phrase in readme, f"README is missing: {phrase}"


def test_readme_lists_all_config_keys() -> None:
    readme = _read(README)
    for key in CONFIG_KEYS:
        assert key in readme, f"README is missing config key: {key}"
    assert "插件配置页" in readme
    assert "重载插件" in readme


def test_readme_documents_commands_and_http_api() -> None:
    readme = _read(README)
    for command in (
        "/astral status",
        "/astral clean [days]",
        "/astral memory show [soul|memory|agents|extra]",
        "/astral commit",
        "/astral save",
        "/astral recover",
        "/astral recover <短哈希>",
    ):
        assert command in readme, f"README is missing command: {command}"
    for route in (
        "/api/v1/plugins/extensions/astrbot_plugin_astral/state",
        "/api/v1/plugins/extensions/astrbot_plugin_astral/files",
        "/api/v1/plugins/extensions/astrbot_plugin_astral/clean",
    ):
        assert route in readme, f"README is missing route: {route}"
    for status_code in ("200", "400", "503"):
        assert status_code in readme, f"README is missing HTTP status: {status_code}"


def test_readme_documents_memory_version_control() -> None:
    """The unreleased Git versioning feature must be documented for users."""
    readme = _read(README)
    for phrase in (
        "记忆版本控制",
        "系统 Git",
        "/astral commit",
        "/astral save",
        "/astral recover",
        ".git",
        "version_control.enabled",
        "version_control.git_path",
        "versioning",
    ):
        assert phrase in readme, f"README is missing version control phrase: {phrase}"
    # rollback must be described as append-only, never as a destructive reset
    assert "追加式" in readme
    assert "git reset --hard" in readme
    # history retention is a privacy fact, not an implementation detail
    assert "隐私擦除" in readme


def test_readme_faq_covers_known_pitfalls() -> None:
    readme = _read(README)
    for phrase in ("/reset", "/new", "第三方", "元信息", "群聊"):
        assert phrase in readme, f"README FAQ is missing: {phrase}"


def test_readme_has_no_placeholders_or_template_remnants() -> None:
    readme = _read(README).lower()
    for marker in ("todo", "tbd", "fixme", "待补", "占位符", "helloworld"):
        assert marker not in readme, f"README contains {marker!r}"


def test_changelog_records_scope_and_verification() -> None:
    changelog = _read(CHANGELOG)
    for phrase in ("v1.0.0", "v0.1.0", "pytest", "19 passed", "已知限制", "运行时"):
        assert phrase in changelog, f"CHANGELOG is missing: {phrase}"
    # internal-only verification notes do not belong in a published changelog
    assert "未随仓库发布" not in changelog


def test_page_is_memory_first() -> None:
    html = _html()
    assert "它现在记得" in html
    assert "它的人设" in html
    # the memory card comes before the maintenance fold
    assert html.index("它现在记得") < html.index("运行与维护")
    # patch internals are not page furniture anymore
    assert "基线" not in html
    assert "版本基线" not in html


def test_views_and_docs_contain_no_template_plugin_name() -> None:
    for path in (INDEX_HTML, README, CHANGELOG):
        assert "helloworld" not in _read(path).lower(), f"{path.name} mentions the template plugin"