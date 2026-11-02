"""Tests for the Astral config layer (config.py).

Covers shared root resolution and creation, forbidden roots and directory
tree creation.

Every test passes an explicit ``data_path`` so nothing is ever written into
the real AstrBot data directory.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

import config


def test_module_constants_and_defaults():
    """Pin the public constants so downstream modules can rely on them."""
    assert tuple(config.DEFAULT_SHARED_RELATIVE) == ("workspaces", "astral")
    assert tuple(config.SHARED_DIRS) == (
        "skills",
        "shared",
        "memory/archive",
        "archive",
        ".astral",
    )
    assert config.MEMORY_DEFAULTS["enabled"] is True
    assert config.MEMORY_DEFAULTS["soul_budget_chars"] == 4000
    assert config.MEMORY_DEFAULTS["memory_budget_chars"] == 8000
    assert config.MEMORY_DEFAULTS["soft_limit_chars"] == 20000
    assert config.VERSION_CONTROL_DEFAULTS["enabled"] is True
    assert config.VERSION_CONTROL_DEFAULTS["git_path"] == ""


def test_config_error_carries_reason():
    error = config.ConfigError("shared root is forbidden")
    assert isinstance(error, Exception)
    assert error.reason == "shared root is forbidden"


# ---------------------------------------------------------------------------
# FR-1: resolve_shared_root (four path shapes)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw", [None, "", "   "])
def test_resolve_default_root(tmp_path, raw):
    """AC-1.1: empty config resolves to <data>/workspaces/astral."""
    data_path = tmp_path / "data"
    assert config.resolve_shared_root(raw, data_path=data_path) == (
        data_path / "workspaces" / "astral"
    ).resolve()


def test_resolve_absolute_root(tmp_path):
    """AC-1.2: an absolute path is used as provided."""
    data_path = tmp_path / "data"
    target = tmp_path / "astral-workspace"
    assert config.resolve_shared_root(str(target), data_path=data_path) == target.resolve()


def test_resolve_tilde_root(tmp_path):
    """AC-1.3: a leading tilde is expanded to the user home directory."""
    data_path = tmp_path / "data"
    expected = Path("~/astral").expanduser().resolve()
    assert config.resolve_shared_root("~/astral", data_path=data_path) == expected
    assert config.resolve_shared_root("~", data_path=data_path) == Path.home().resolve()


def test_resolve_relative_root(tmp_path):
    """AC-1.4: relative paths are rooted under <data>/workspaces."""
    data_path = tmp_path / "data"
    assert config.resolve_shared_root("myws", data_path=data_path) == (
        data_path / "workspaces" / "myws"
    ).resolve()


def test_resolve_default_uses_astrbot_data_path_when_omitted():
    """Without data_path the AstrBot data directory is used as the base."""
    from astrbot.core.utils.astrbot_path import get_astrbot_data_path

    expected = (Path(get_astrbot_data_path()) / "workspaces" / "astral").resolve()
    assert config.resolve_shared_root(None) == expected


# ---------------------------------------------------------------------------
# FR-2: check_forbidden
# ---------------------------------------------------------------------------


def test_forbidden_astrbot_source_tree(tmp_path):
    """AC-2.1: the AstrBot source tree (and everything below) is forbidden."""
    from astrbot.core.utils.astrbot_path import get_astrbot_path

    source = Path(get_astrbot_path())
    data_path = tmp_path / "data"
    for candidate in (source, source / "astrbot" / "core"):
        reason = config.check_forbidden(candidate, data_path=data_path)
        assert reason is not None
        assert "forbidden" in reason.lower()


def test_forbidden_sensitive_data_directories(tmp_path):
    """<data>/config, <data>/plugins and <data>/plugin_data are forbidden."""
    data_path = tmp_path / "data"
    for relative in ("config", "plugins", "plugin_data"):
        for suffix in (Path(), Path("sub")):
            reason = config.check_forbidden(data_path / relative / suffix, data_path=data_path)
            assert reason is not None
            assert "forbidden" in reason.lower()


def test_forbidden_home_secret_directories(tmp_path):
    """~/.ssh (and siblings) are forbidden subtrees."""
    data_path = tmp_path / "data"
    for relative in (".ssh", ".aws", ".config"):
        reason = config.check_forbidden(
            Path.home() / relative / "keys", data_path=data_path
        )
        assert reason is not None
        assert "forbidden" in reason.lower()


def test_forbidden_system_directory(tmp_path):
    """System directories are forbidden subtrees."""
    data_path = tmp_path / "data"
    if os.name == "nt":
        system_dir = Path("C:/Windows")
        sibling = Path("C:/Program Files")
    else:
        system_dir = Path("/etc")
        sibling = Path("/usr")
    for candidate in (system_dir / "astral-forbidden-test", sibling / "astral-forbidden-test"):
        reason = config.check_forbidden(candidate, data_path=data_path)
        assert reason is not None
        assert "forbidden" in reason.lower()


def test_forbidden_home_is_exact_match_only(tmp_path):
    """The user home itself is forbidden, but its children are allowed."""
    data_path = tmp_path / "data"
    home = Path.home()
    reason = config.check_forbidden(home, data_path=data_path)
    assert reason is not None
    assert "forbidden" in reason.lower()
    assert config.check_forbidden(home / "projects" / "astral-ok", data_path=data_path) is None


def test_forbidden_matching_is_case_insensitive_on_windows(tmp_path):
    """Windows path comparison must ignore letter case."""
    data_path = tmp_path / "data"
    if os.name == "nt":
        upper = Path(str(Path("C:/Windows")).upper()) / "astral-case"
        reason = config.check_forbidden(upper, data_path=data_path)
    else:
        reason = config.check_forbidden(Path("/etc/astral-case"), data_path=data_path)
    assert reason is not None
    assert "forbidden" in reason.lower()


def test_forbidden_extra_roots(tmp_path):
    """AC-2.2: forbidden_roots entries reject the root and their subtree."""
    data_path = tmp_path / "data"
    secret = tmp_path / "secret"
    assert config.check_forbidden(secret, [str(secret)], data_path=data_path) is not None
    reason = config.check_forbidden(secret / "sub", [str(secret)], data_path=data_path)
    assert reason is not None
    assert "forbidden" in reason.lower()
    # The same path is allowed once the extra rule is gone.
    assert config.check_forbidden(secret / "sub", None, data_path=data_path) is None
    assert config.check_forbidden(secret / "sub", [], data_path=data_path) is None


def test_valid_roots_are_not_forbidden(tmp_path):
    """AC-2.3: the default root and common custom roots are not false positives."""
    data_path = tmp_path / "data"
    assert config.check_forbidden(
        config.resolve_shared_root(None, data_path=data_path), data_path=data_path
    ) is None
    assert config.check_forbidden(Path("D:/astral-workspace"), data_path=data_path) is None
    assert config.check_forbidden(
        Path("~/astral").expanduser(), data_path=data_path
    ) is None
    assert config.check_forbidden(tmp_path / "workspaces" / "myws", data_path=data_path) is None


# ---------------------------------------------------------------------------
# get_shared_root: resolve + validate in one entry point
# ---------------------------------------------------------------------------


def test_get_shared_root_resolves_valid_config(tmp_path):
    data_path = tmp_path / "data"
    assert config.get_shared_root(None, data_path=data_path) == (
        data_path / "workspaces" / "astral"
    ).resolve()
    assert config.get_shared_root({"shared_root": "myws"}, data_path=data_path) == (
        data_path / "workspaces" / "myws"
    ).resolve()


def test_get_shared_root_raises_on_forbidden_path(tmp_path):
    data_path = tmp_path / "data"
    cfg = {"shared_root": str(data_path / "config" / "workspace")}
    with pytest.raises(config.ConfigError) as excinfo:
        config.get_shared_root(cfg, data_path=data_path)
    assert "forbidden" in excinfo.value.reason.lower()


# ---------------------------------------------------------------------------
# FR-8: ensure_shared_tree
# ---------------------------------------------------------------------------


def test_ensure_shared_tree_creates_directories_only(tmp_path):
    """AC-8.1: 001 creates the directories and no content files."""
    root = tmp_path / "shared"
    created = config.ensure_shared_tree(root)

    assert root.is_dir()
    for relative in config.SHARED_DIRS:
        assert (root / relative).is_dir()

    normalized = {str(item).replace("\\", "/").rstrip("/") for item in created}
    assert set(config.SHARED_DIRS) <= normalized

    # 001 only guarantees directories; content files are seeded by 002.
    for name in ("AGENTS.md", "EXTRA_PROMPT.md", "SOUL.md", "MEMORY.md"):
        assert not (root / name).exists()


def test_ensure_shared_tree_is_idempotent(tmp_path):
    """AC-8.1: a second call reports nothing new and changes nothing."""
    root = tmp_path / "shared"
    config.ensure_shared_tree(root)
    assert config.ensure_shared_tree(root) == []
    for relative in config.SHARED_DIRS:
        assert (root / relative).is_dir()


def test_ensure_shared_tree_raises_when_root_is_a_file(tmp_path):
    """AC-1.5 proxy: an unusable root fails loudly with ConfigError."""
    target = tmp_path / "shared"
    target.write_text("not a directory", encoding="utf-8")
    with pytest.raises(config.ConfigError) as excinfo:
        config.ensure_shared_tree(target)
    assert excinfo.value.reason


# ---------------------------------------------------------------------------
# read_options / memory_options: never raise, always fall back
# ---------------------------------------------------------------------------


def test_read_options_defaults():
    options = config.read_options(None)
    assert options["shared_root_raw"] == ""
    assert options["patch_webchat_projects"] is True
    assert options["forbidden_roots"] == []
    assert isinstance(options["quota_gb"], float)
    assert options["quota_gb"] == 0.0
    assert isinstance(options["memory"], dict)
    for key, value in config.MEMORY_DEFAULTS.items():
        assert options["memory"][key] == value
    assert isinstance(options["version_control"], dict)
    for key, value in config.VERSION_CONTROL_DEFAULTS.items():
        assert options["version_control"][key] == value


def test_read_options_passes_valid_values_through():
    cfg = {
        "shared_root": "myws",
        "patch_webchat_projects": False,
        "forbidden_roots": ["D:/secret"],
        "quota_gb": 5,
        "memory": {"enabled": False, "soft_limit_chars": 12000},
        "version_control": {
            "enabled": False,
            "git_path": "C:/Program Files/Git/bin/git.exe",
        },
    }
    options = config.read_options(cfg)
    assert options["shared_root_raw"] == "myws"
    assert options["patch_webchat_projects"] is False
    assert options["forbidden_roots"] == ["D:/secret"]
    assert float(options["quota_gb"]) == 5.0
    assert options["memory"]["enabled"] is False
    assert options["memory"]["soft_limit_chars"] == 12000
    assert (
        options["memory"]["memory_budget_chars"]
        == config.MEMORY_DEFAULTS["memory_budget_chars"]
    )
    assert options["version_control"]["enabled"] is False
    assert options["version_control"]["git_path"] == "C:/Program Files/Git/bin/git.exe"


def test_read_options_falls_back_on_dirty_values():
    cfg = {
        "shared_root": 123,
        "patch_webchat_projects": "not-a-bool",
        "forbidden_roots": "D:/secret",
        "quota_gb": "not-a-number",
        "memory": "not-a-dict",
        "version_control": "not-a-dict",
    }
    options = config.read_options(cfg)
    assert options["shared_root_raw"] == ""
    assert options["patch_webchat_projects"] is True
    assert options["forbidden_roots"] == []
    assert float(options["quota_gb"]) == 0.0
    assert isinstance(options["memory"], dict)
    for key, value in config.MEMORY_DEFAULTS.items():
        assert options["memory"][key] == value
    assert isinstance(options["version_control"], dict)
    for key, value in config.VERSION_CONTROL_DEFAULTS.items():
        assert options["version_control"][key] == value


def test_memory_options_defaults_and_partial_merge():
    defaults = config.memory_options(None)
    for key, value in config.MEMORY_DEFAULTS.items():
        assert defaults[key] == value

    partial = config.memory_options({"memory": {"enabled": False, "soul_budget_chars": 2500}})
    assert partial["enabled"] is False
    assert partial["soul_budget_chars"] == 2500
    assert partial["memory_budget_chars"] == config.MEMORY_DEFAULTS["memory_budget_chars"]
    assert partial["soft_limit_chars"] == config.MEMORY_DEFAULTS["soft_limit_chars"]


@pytest.mark.parametrize(
    "cfg",
    [
        {},
        {"memory": "not-a-dict"},
        {"memory": None},
        {"memory": {"enabled": "not-a-bool", "soul_budget_chars": "not-an-int"}},
    ],
)
def test_memory_options_ignores_invalid_values(cfg):
    options = config.memory_options(cfg)
    assert isinstance(options, dict)
    for key, value in config.MEMORY_DEFAULTS.items():
        assert options[key] == value


# ---------------------------------------------------------------------------
# version_control_options: tolerant normalisation (unreleased feature)
# ---------------------------------------------------------------------------


def test_version_control_options_defaults_and_partial_merge():
    defaults = config.version_control_options(None)
    assert defaults == dict(config.VERSION_CONTROL_DEFAULTS)

    partial = config.version_control_options({"version_control": {"enabled": False}})
    assert partial["enabled"] is False
    assert partial["git_path"] == config.VERSION_CONTROL_DEFAULTS["git_path"]

    path_only = config.version_control_options(
        {"version_control": {"git_path": "  C:/Program Files/Git/bin/git.exe  "}}
    )
    assert path_only["enabled"] is True
    assert path_only["git_path"] == "C:/Program Files/Git/bin/git.exe"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (True, True),
        (False, False),
        ("false", False),
        ("off", False),
        ("0", False),
        (0, False),
        ("true", True),
        ("yes", True),
        ("1", True),
        (1, True),
        ("not-a-bool", True),
        (None, True),
    ],
)
def test_version_control_enabled_is_coerced_tolerantly(raw, expected):
    options = config.version_control_options({"version_control": {"enabled": raw}})
    assert options["enabled"] is expected


@pytest.mark.parametrize(
    "group",
    [None, "not-a-dict", {"git_path": 123}, {"git_path": None}, {"git_path": ""}],
)
def test_version_control_ignores_invalid_git_path(group):
    options = config.version_control_options({"version_control": group})
    assert options["git_path"] == ""
    assert options["enabled"] is True
