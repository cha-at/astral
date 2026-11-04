"""Static contracts for the plugin-market review rules.

The market reviewer requires every logger to come from astrbot.api and every
persistent default to be rooted at StarTools.get_data_dir(<plugin>). These
checks are intentionally static: they never start AstrBot or touch disk.
"""

from __future__ import annotations

from pathlib import Path

import config

ROOT = Path(__file__).resolve().parent.parent
MODULES = sorted(path for path in ROOT.glob("*.py"))
FORBIDDEN_LOGGER = "logging" + "." + "getLogger"
FORBIDDEN_IMPORT = "import " + "logging"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_no_builtin_logging_module():
    """Review rule: never use Python's built-in logging module."""
    for path in MODULES:
        text = _text(path)
        assert FORBIDDEN_LOGGER not in text, path.name
        assert FORBIDDEN_IMPORT not in text, path.name


def test_logger_is_imported_from_astrbot_api():
    """Review rule: modules that log must import astrbot.api.logger."""
    for path in MODULES:
        text = _text(path)
        if "logger." in text:
            assert "from astrbot.api import logger" in text, path.name


def test_default_data_dir_uses_startools():
    """Review rule: persistent default goes through StarTools.get_data_dir."""
    text = _text(ROOT / "config.py")
    assert "from astrbot.api.star import StarTools" in text
    assert "StarTools.get_data_dir" in text


def test_plugin_name_matches_metadata():
    metadata = _text(ROOT / "metadata.yaml")
    assert f"name: {config.PLUGIN_NAME}" in metadata


def test_main_imports_filter_from_astrbot_api_event():
    text = _text(ROOT / "main.py")
    assert "from astrbot.api.event import" in text
    assert "filter" in text
