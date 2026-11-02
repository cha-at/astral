"""Load the plugin the way AstrBot does (001 FR-12 / AC-12.1).

AstrBot imports plugins as 'data.plugins.<dir>.main'. This test uses the same
package path so the relative imports, the command decorators and the hook
registration are exercised exactly as in a real instance.
"""

from __future__ import annotations

import importlib
import re
import sys
from pathlib import Path

import pytest


def _core_dir() -> Path:
    import astrbot

    return Path(astrbot.__file__).resolve().parent.parent


def test_plugin_imports_under_astrbot_package_path():
    core = _core_dir()
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))

    module = importlib.import_module("data.plugins.astrbot_plugin_astral.main")

    assert module.__package__ == "data.plugins.astrbot_plugin_astral"
    assert hasattr(module, "AstralPlugin")


def test_plugin_registers_commands_and_hooks():
    core = _core_dir()
    if str(core) not in sys.path:
        sys.path.insert(0, str(core))
    module = importlib.import_module("data.plugins.astrbot_plugin_astral.main")

    from astrbot.core.star.star_handler import star_handlers_registry

    handlers = {
        handler.handler_name: handler
        for handler in star_handlers_registry.get_handlers_by_module_name(module.__name__)
    }
    for name in (
        "astral",
        "astral_status",
        "astral_clean",
        "astral_memory",
        "astral_memory_show",
        "inject_memory_snapshot",
        "on_astrbot_loaded",
    ):
        assert name in handlers, sorted(handlers)

    status_handler = handlers["astral_status"]
    assert status_handler.event_type.name == "AdapterMessageEvent"
    hook = handlers["inject_memory_snapshot"]
    assert hook.event_type.name == "OnLLMRequestEvent"


def test_metadata_matches_spec():
    root = Path(__file__).resolve().parent.parent
    text = (root / "metadata.yaml").read_text(encoding="utf-8")
    assert "name: astrbot_plugin_astral" in text
    assert "display_name: Astral" in text
    assert "astrbot_version:" in text
    assert "4.29" in text  # the tested baseline stays visible in the description
    assert "helloworld" not in text


def test_astrbot_version_range_is_the_declared_window():
    """The market gate must be PEP 440 and cover 4.27.0 .. 5.0 (exclusive)."""
    from packaging.specifiers import SpecifierSet
    from packaging.version import Version

    root = Path(__file__).resolve().parent.parent
    text = (root / "metadata.yaml").read_text(encoding="utf-8")
    match = re.search(r'^astrbot_version:\s*"([^"]+)"', text, re.MULTILINE)
    assert match, "metadata.yaml has no astrbot_version"
    spec = SpecifierSet(match.group(1))

    assert Version("4.27.0") in spec, "the documented lower bound must install"
    assert Version("4.29.4") in spec, "the tested baseline must install"
    assert Version("4.26.9") not in spec, "below the supported window"
    assert Version("5.0.0") not in spec, "5.x is outside the declared window"


def test_no_helloworld_template_remnants():
    """AC-12.3: plugin artefacts must not mention the template plugin."""
    root = Path(__file__).resolve().parent.parent
    skip_roots = {"specs", "docs", "tests", "__pycache__", ".pytest_cache"}
    suffixes = {".py", ".json", ".yaml", ".yml", ".html", ".md"}
    offenders: list[str] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix not in suffixes:
            continue
        relative = path.relative_to(root)
        if relative.parts and relative.parts[0] in skip_roots:
            continue
        text = path.read_text(encoding="utf-8", errors="replace").lower()
        if "helloworld" in text:
            offenders.append(str(relative))
    assert offenders == [], offenders
