"""Configuration resolution and safety checks for the Astral plugin.

Responsibilities (001 FR-1, FR-2, FR-8):

* turn the raw 'shared_root' option into an absolute Path, defaulting to
  the plugin data directory (StarTools.get_data_dir);
* reject dangerous locations instead of silently falling back to a default;
* create the shared workspace directory skeleton and verify it is writable;
* normalise the remaining options (001 keys plus the 'memory' and
  'version_control' groups).

The module never touches sys.modules and never installs the patch.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

PLUGIN_NAME = "astrbot_plugin_astral"

SHARED_DIRS: tuple[str, ...] = (
    "skills",
    "shared",
    "memory/archive",
    "archive",
    ".astral",
)

MEMORY_DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "soul_budget_chars": 4000,
    "memory_budget_chars": 8000,
    "soft_limit_chars": 20000,
}

VERSION_CONTROL_DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "git_path": "",
}

PROBE_NAME = ".astral/.write-probe"

RULE_SELF = "self"
RULE_TREE = "tree"


class ConfigError(Exception):
    """Configuration cannot be applied.

    The 'reason' attribute is user visible (log line, '/astral status', HTTP
    payload) and must always say what the user has to fix.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# --------------------------------------------------------------------------
# value coercion helpers (never raise, keep the plugin loadable)
# --------------------------------------------------------------------------
def _as_str(value: Any, default: str = "") -> str:
    return value.strip() if isinstance(value, str) else default


def _as_list(value: Any) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        return [item.strip() for item in value if isinstance(item, str) and item.strip()]
    return []


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off"}:
            return False
    return default


def _as_int(value: Any, default: int, *, minimum: int = 1) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return number if number >= minimum else default


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------
# path helpers
# --------------------------------------------------------------------------
def _resolve(path: Path) -> Path:
    try:
        return Path(path).expanduser().resolve(strict=False)
    except OSError:
        return Path(path)


def get_data_path() -> Path:
    """Return the AstrBot data directory (<root>/data).

    Used to locate plugin_data and the safety rule table; the plugin's own
    default storage directory is resolved through StarTools.get_data_dir().
    """
    from astrbot.core.utils.astrbot_path import get_astrbot_data_path

    return _resolve(Path(get_astrbot_data_path()))


def _data_path_or_default(data_path: Path | None) -> Path:
    if data_path is not None:
        return _resolve(Path(data_path))
    try:
        return get_data_path()
    except Exception:  # pragma: no cover - only when AstrBot is not importable
        return _resolve(Path.cwd() / "data")


# The default storage directory must come from StarTools; this helper only
# computes the canonical path for safety checks, relative resolution and the
# test-only data_path override, and never creates anything.
def plugin_data_path(data_path: Path | None = None) -> Path:
    """Return <data>/plugin_data/astrbot_plugin_astral without creating it.

    Kept separate from get_plugin_data_dir() so safety checks and relative
    path resolution never create directories as a side effect.
    """
    data = _data_path_or_default(data_path)
    return _resolve(data / "plugin_data" / PLUGIN_NAME)


def get_plugin_data_dir() -> Path:
    """Return the plugin data directory through AstrBot's StarTools.

    AstralPlugin.__init__ runs before AstrBot registers the plugin in
    star_map, so the plugin name must be passed explicitly instead of letting
    StarTools detect the caller from the stack.
    """
    try:
        from astrbot.api.star import StarTools

        return _resolve(Path(StarTools.get_data_dir(PLUGIN_NAME)))
    except Exception:
        try:
            from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path

            return _resolve(Path(get_astrbot_plugin_data_path()) / PLUGIN_NAME)
        except Exception:
            return plugin_data_path()


def resolve_shared_root(raw: Any, *, data_path: Path | None = None) -> Path:
    """Resolve the shared_root option into an absolute path (001 FR-1).

    Args:
        raw: Raw option value; empty means the plugin data directory
            (<data>/plugin_data/astrbot_plugin_astral).
        data_path: AstrBot data directory override, mostly for tests.

    Returns:
        Absolute, non-strict resolved path.
    """
    text = _as_str(raw)
    if not text:
        if data_path is None:
            return get_plugin_data_dir()
        return plugin_data_path(data_path)
    candidate = Path(text).expanduser()
    if not candidate.is_absolute():
        candidate = plugin_data_path(data_path) / candidate
    return _resolve(candidate)


def _astrbot_source_path() -> Path | None:
    try:
        from astrbot.core.utils.astrbot_path import get_astrbot_path

        return _resolve(Path(get_astrbot_path()))
    except Exception:
        return None


def _system_rules() -> list[tuple[str, Path, str]]:
    if sys.platform == "win32":
        return [
            ("system:Windows", _resolve(Path("C:/Windows")), RULE_TREE),
            ("system:Program Files", _resolve(Path("C:/Program Files")), RULE_TREE),
        ]
    return [
        ("system:/etc", _resolve(Path("/etc")), RULE_TREE),
        ("system:/usr", _resolve(Path("/usr")), RULE_TREE),
        ("system:/var", _resolve(Path("/var")), RULE_TREE),
        ("system:/System", _resolve(Path("/System")), RULE_TREE),
    ]


def forbidden_rules(
    extra: Any = None,
    *,
    data_path: Path | None = None,
) -> list[tuple[str, Path, str]]:
    """Build the ordered forbidden path rule table (001 FR-2).

    Returns:
        List of (label, path, mode) where mode is "self" (only the exact path
        is rejected) or "tree" (the path and everything below it).
    """
    data = _data_path_or_default(data_path)
    home = _resolve(Path.home())
    rules: list[tuple[str, Path, str]] = []
    source = _astrbot_source_path()
    if source is not None:
        rules.append(("AstrBot source", source, RULE_TREE))
    rules.extend(
        [
            ("data/config", _resolve(data / "config"), RULE_TREE),
            ("data/plugins", _resolve(data / "plugins"), RULE_TREE),
            ("data/plugin_data", _resolve(data / "plugin_data"), RULE_TREE),
            ("user home", home, RULE_SELF),
            ("user home/.ssh", _resolve(home / ".ssh"), RULE_TREE),
            ("user home/.aws", _resolve(home / ".aws"), RULE_TREE),
            ("user home/.config", _resolve(home / ".config"), RULE_TREE),
        ]
    )
    rules.extend(_system_rules())
    for item in _as_list(extra):
        candidate = Path(item).expanduser()
        if not candidate.is_absolute():
            candidate = plugin_data_path(data_path) / candidate
        rules.append((f"forbidden_roots:{item}", _resolve(candidate), RULE_TREE))
    return rules


def _norm_text(path: Path) -> str:
    return os.path.normcase(os.path.normpath(str(path)))


def _matches(root: Path, rule: Path, mode: str) -> bool:
    root_text = _norm_text(root)
    rule_text = _norm_text(rule)
    if root_text == rule_text:
        return True
    if mode == RULE_SELF:
        return False
    prefix = rule_text.rstrip(os.sep) + os.sep
    return root_text.startswith(prefix)


def check_forbidden(
    root: Path,
    extra: Any = None,
    *,
    data_path: Path | None = None,
) -> str | None:
    """Return a user visible reason when root is a forbidden location.

    Two narrow exceptions keep the defaults usable: the AstrBot source rule is
    skipped for roots inside <data> (AstrBot keeps its runtime data directory
    inside the installation directory), and the data/plugin_data rule is
    skipped for the plugin's own data directory. Every other data/config,
    data/plugins and data/plugin_data path plus user supplied forbidden_roots
    entries remain rejected.
    """
    resolved = _resolve(Path(root))
    data = _data_path_or_default(data_path)
    own_data = plugin_data_path(data_path)
    inside_data = _matches(resolved, data, RULE_TREE)
    for label, rule, mode in forbidden_rules(extra, data_path=data):
        if label == "AstrBot source" and inside_data:
            continue
        if label == "data/plugin_data" and _matches(resolved, own_data, RULE_TREE):
            continue
        if _matches(resolved, rule, mode):
            return f"forbidden（共享根命中危险路径 {label}）：{resolved}"
    return None


def get_shared_root(cfg: Any, *, data_path: Path | None = None) -> Path:
    """Resolve and validate the shared root; raises ConfigError."""
    conf = cfg if isinstance(cfg, dict) else {}
    root = resolve_shared_root(conf.get("shared_root"), data_path=data_path)
    reason = check_forbidden(root, conf.get("forbidden_roots"), data_path=data_path)
    if reason:
        raise ConfigError(reason)
    return root


def ensure_shared_tree(root: Path) -> list[str]:
    """Create the shared root skeleton and verify it is writable (001 FR-8).

    Args:
        root: Absolute shared root path.

    Returns:
        Relative paths that were created by this call.

    Raises:
        ConfigError: When the root cannot be created or written to.
    """
    target_root = Path(root)
    created: list[str] = []
    try:
        target_root.mkdir(parents=True, exist_ok=True)
        if not target_root.is_dir():
            raise ConfigError(f"共享根不是目录：{target_root}")
        for relative in SHARED_DIRS:
            directory = target_root / relative
            if directory.is_dir():
                continue
            if directory.exists():
                raise ConfigError(f"共享根下同名路径不是目录：{directory}")
            directory.mkdir(parents=True, exist_ok=True)
            created.append(relative)
        probe = target_root / PROBE_NAME
        probe.parent.mkdir(parents=True, exist_ok=True)
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except ConfigError:
        raise
    except OSError as exc:
        raise ConfigError(
            f"共享根无法创建或写入：{target_root}（{exc.__class__.__name__}）",
        ) from exc
    return created


# --------------------------------------------------------------------------
# option groups
# --------------------------------------------------------------------------
def memory_options(cfg: Any) -> dict[str, Any]:
    """Return the normalised memory option group (002)."""
    raw = cfg.get("memory") if isinstance(cfg, dict) else None
    group = raw if isinstance(raw, dict) else {}
    return {
        "enabled": _as_bool(group.get("enabled"), MEMORY_DEFAULTS["enabled"]),
        "soul_budget_chars": _as_int(
            group.get("soul_budget_chars"),
            MEMORY_DEFAULTS["soul_budget_chars"],
        ),
        "memory_budget_chars": _as_int(
            group.get("memory_budget_chars"),
            MEMORY_DEFAULTS["memory_budget_chars"],
        ),
        "soft_limit_chars": _as_int(
            group.get("soft_limit_chars"),
            MEMORY_DEFAULTS["soft_limit_chars"],
        ),
    }


def version_control_options(cfg: Any) -> dict[str, Any]:
    """Return the normalised version_control option group (unreleased).

    Coercion is deliberately tolerant: a malformed section must never make the
    plugin unloadable, so every value falls back to VERSION_CONTROL_DEFAULTS.
    """
    raw = cfg.get("version_control") if isinstance(cfg, dict) else None
    group = raw if isinstance(raw, dict) else {}
    return {
        "enabled": _as_bool(
            group.get("enabled"),
            VERSION_CONTROL_DEFAULTS["enabled"],
        ),
        "git_path": _as_str(
            group.get("git_path"),
            VERSION_CONTROL_DEFAULTS["git_path"],
        ),
    }


def read_options(cfg: Any) -> dict[str, Any]:
    """Return every plugin option, normalised and defaulted."""
    conf = cfg if isinstance(cfg, dict) else {}
    return {
        "shared_root_raw": _as_str(conf.get("shared_root")),
        "patch_webchat_projects": _as_bool(
            conf.get("patch_webchat_projects"),
            True,
        ),
        "forbidden_roots": _as_list(conf.get("forbidden_roots")),
        "quota_gb": max(_as_float(conf.get("quota_gb"), 0.0), 0.0),
        "memory": memory_options(conf),
        "version_control": version_control_options(conf),
    }
