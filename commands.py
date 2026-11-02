"""Chat command orchestration for Astral memory versioning.

This module turns the plugin object into user-facing text for '/astral commit',
'/astral save' and '/astral recover'. It never runs git itself: versioning.py
owns that. Audit records and snapshot-cache invalidation live here so the
command handlers in main.py stay thin.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

if __package__:
    from . import state as state_mod
    from . import store as store_mod
    from . import versioning
else:  # pragma: no cover - direct import for tests
    import state as state_mod
    import store as store_mod
    import versioning

logger = logging.getLogger("astrbot")

HELP_TEXT = """Astral · 让我记住群里的事
/astral status —— 我现在记了多少、有没有生效
/astral memory show [soul|memory|agents|extra] —— 看我的设定和记忆
/astral commit（或 /astral save）—— 把现在的记忆存成一个版本
/astral recover [短哈希] —— 看历史版本；带哈希则回退（管理员）
/astral clean [天数] —— 把 N 天没动过的散落文件挪进 archive/（默认 30 天，只移动不删除）"""


def help_text() -> str:
    """Return the '/astral' group help text."""
    return HELP_TEXT


def _root(plugin: Any) -> Path | None:
    try:
        root = plugin.shared_root()
    except Exception:  # pragma: no cover - defensive
        return None
    if root is None:
        return None
    try:
        return root if Path(root).is_dir() else None
    except OSError:
        return None


def _version_options(plugin: Any) -> dict[str, Any]:
    options = getattr(plugin, "options", None)
    group = options.get("version_control") if isinstance(options, dict) else None
    return group if isinstance(group, dict) else {"enabled": True, "git_path": ""}


def _git_path(plugin: Any) -> str:
    value = _version_options(plugin).get("git_path")
    return value.strip() if isinstance(value, str) else ""


def _enabled(plugin: Any) -> bool:
    return bool(_version_options(plugin).get("enabled", True))


def _clear_cache(plugin: Any) -> None:
    clear = getattr(plugin, "clear_snapshot_cache", None)
    if callable(clear):
        clear()


def _audit(root: Path, action: str, detail: Any) -> None:
    try:
        store_mod.append_audit(root, action, detail)
    except Exception:  # pragma: no cover - audit must never break chat
        logger.exception("astral: version audit write failed action=%s", action)


async def save_memory(plugin: Any) -> str:
    """Commit the current four memory files and render the chat answer."""
    root = _root(plugin)
    if root is None:
        return "Astral 未生效：共享根不可用，无法保存。"
    if not _enabled(plugin):
        return "记忆版本控制已关闭（version_control.enabled=false）。"
    try:
        result = await asyncio.to_thread(
            versioning.commit,
            root,
            git_path=_git_path(plugin),
        )
    except versioning.VersioningError as exc:
        _audit(root, "version_commit_failed", {"reason": str(exc)})
        return f"保存失败：{exc}"
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("astral: version commit failed")
        _audit(root, "version_commit_failed", {"reason": exc.__class__.__name__})
        return f"保存失败：{exc.__class__.__name__}（详见日志）"
    if result.get("status") == "committed":
        _audit(
            root,
            "version_commit",
            {"hash": result.get("hash"), "files": result.get("files") or []},
        )
    return state_mod.version_commit_text(result)


async def recover_memory(plugin: Any, target: str = "") -> str:
    """List checkpoints, or restore the four memory files to a checkpoint."""
    root = _root(plugin)
    if root is None:
        return "Astral 未生效：共享根不可用，无法回退。"
    if not _enabled(plugin):
        return "记忆版本控制已关闭（version_control.enabled=false）。"
    text = (target or "").strip()
    if not text:
        try:
            result = await asyncio.to_thread(
                versioning.list_versions,
                root,
                git_path=_git_path(plugin),
                limit=10,
            )
        except versioning.VersioningError as exc:
            return f"读取版本列表失败：{exc}"
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("astral: version list failed")
            return f"读取版本列表失败：{exc.__class__.__name__}（详见日志）"
        return state_mod.version_list_text(result)

    try:
        result = await asyncio.to_thread(
            versioning.recover,
            root,
            target=text,
            git_path=_git_path(plugin),
        )
    except versioning.VersioningError as exc:
        _clear_cache(plugin)
        _audit(root, "version_recover_failed", {"target": text, "reason": str(exc)})
        return f"回退失败：{exc}"
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("astral: version recover failed")
        _clear_cache(plugin)
        _audit(
            root,
            "version_recover_failed",
            {"target": text, "reason": exc.__class__.__name__},
        )
        return f"回退失败：{exc.__class__.__name__}（详见日志）"

    status = result.get("status")
    if status == "recovered":
        _clear_cache(plugin)
        _audit(
            root,
            "version_recover",
            {
                "target": result.get("target"),
                "before": result.get("before"),
                "hash": result.get("hash"),
                "files": result.get("files") or [],
            },
        )
    elif status == "recovered_uncommitted":
        _clear_cache(plugin)
        _audit(
            root,
            "version_recover_partial",
            {
                "target": result.get("target"),
                "before": result.get("before"),
                "files": result.get("files") or [],
                "error": result.get("error"),
            },
        )
    return state_mod.version_recover_text(result)
