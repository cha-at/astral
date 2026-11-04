"""Regression tests for the forbidden-path rule table (001 FR-2).

Live testing on a real instance showed that the default shared root lives
inside the AstrBot installation directory
(<install>/data/plugin_data/astrbot_plugin_astral), so the "AstrBot source"
rule must not reject <data> subtrees and the "data/plugin_data" rule must
allow the plugin's own directory. These tests pin those exceptions and prove
the source tree, data/config and sibling plugin data stay rejected.
"""

from __future__ import annotations

from pathlib import Path

import config


def _astrbot_source() -> Path:
    from astrbot.core.utils.astrbot_path import get_astrbot_path

    return Path(get_astrbot_path()).resolve(strict=False)


def test_default_root_inside_data_is_allowed():
    data = config.get_data_path()
    root = config.resolve_shared_root("", data_path=data)
    assert root == (data / "plugin_data" / config.PLUGIN_NAME).resolve()
    assert root.is_relative_to(data)
    assert config.check_forbidden(root, data_path=data) is None


def test_source_tree_outside_data_is_still_rejected():
    source = _astrbot_source()
    candidate = source / "astrbot" / "core" / "workspace.py"
    reason = config.check_forbidden(candidate, data_path=config.get_data_path())
    assert reason is not None
    assert "forbidden" in reason
    assert "AstrBot source" in reason


def test_data_config_still_rejected_even_though_inside_data():
    data = config.get_data_path()
    reason = config.check_forbidden(data / "config" / "nested", data_path=data)
    assert reason is not None and "data/config" in reason


def test_forbidden_roots_still_apply_inside_data(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    root = data / "plugin_data" / config.PLUGIN_NAME / "blocked"
    reason = config.check_forbidden(root, ["blocked"], data_path=data)
    assert reason is not None and "forbidden_roots" in reason


def test_sibling_plugin_data_is_still_rejected(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    sibling = data / "plugin_data" / "other_plugin"
    reason = config.check_forbidden(sibling, data_path=data)
    assert reason is not None and "data/plugin_data" in reason
