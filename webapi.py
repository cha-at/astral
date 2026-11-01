"""HTTP endpoints of the Astral plugin.

001 owns 'state' and 'clean'; 002 owns 'files'. Every route starts with the
plugin directory name because AstrBot matches plugin APIs by that first
segment (001 plan section 6). All handlers check the patch state first: a
disabled plugin must answer 503 instead of exposing stale data (001 FR-10).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from astrbot.api.web import json_response, request

if __package__:
    from . import templates as templates_mod
else:  # pragma: no cover - direct import for tests
    import templates as templates_mod

logger = logging.getLogger("astrbot")

ROUTE_STATE = "/astrbot_plugin_astral/state"
ROUTE_FILES = "/astrbot_plugin_astral/files"
ROUTE_CLEAN = "/astrbot_plugin_astral/clean"

MEMORY_NAMES: tuple[str, ...] = ("SOUL", "MEMORY", "AGENTS", "EXTRA")
FILE_BY_NAME: dict[str, str] = {
    "SOUL": "SOUL.md",
    "MEMORY": "MEMORY.md",
    "AGENTS": "AGENTS.md",
    "EXTRA": "EXTRA_PROMPT.md",
}

MAX_PREVIEW_BYTES = 512 * 1024
ARCHIVE_INDEX_LIMIT = 10


def _iso(timestamp: float) -> str:
    from datetime import datetime

    return datetime.fromtimestamp(timestamp).astimezone().isoformat(timespec="seconds")


def _seeded_version(text: str) -> int | None:
    first_line = text.splitlines()[0] if text.splitlines() else ""
    for version in range(1, 10):
        if f"astral:seeded:v{version}" in first_line:
            return version
    return None


def _read_limited(path: Path) -> tuple[str, bool]:
    """Read at most MAX_PREVIEW_BYTES; returns (text, truncated)."""
    with path.open("rb") as handle:
        raw = handle.read(MAX_PREVIEW_BYTES + 1)
    truncated = len(raw) > MAX_PREVIEW_BYTES
    if truncated:
        raw = raw[:MAX_PREVIEW_BYTES]
    return raw.decode("utf-8", errors="replace"), truncated


def _within_root(root: Path, candidate: Path) -> bool:
    try:
        resolved = candidate.resolve(strict=False)
        return resolved.is_relative_to(root)
    except (OSError, ValueError):
        return False


def _file_meta(root: Path, name: str) -> dict[str, Any]:
    relative = FILE_BY_NAME[name]
    path = root / relative
    meta: dict[str, Any] = {
        "name": name,
        "path": relative,
        "abs_path": str(path),
        "exists": False,
        "size": 0,
        "chars": 0,
        "mtime": None,
        "seeded_version": None,
        "managed_ok": None,
    }
    try:
        if not path.exists() or path.is_dir() or path.is_symlink():
            if path.is_symlink():
                meta["managed_ok"] = None
            return meta
        stat = path.stat()
        text, _ = _read_limited(path)
    except OSError:
        return meta
    meta.update(
        {
            "exists": True,
            "size": stat.st_size,
            "chars": len(text),
            "mtime": _iso(stat.st_mtime),
            "seeded_version": _seeded_version(text),
        }
    )
    if name == "EXTRA":
        try:
            meta["managed_ok"] = templates_mod.managed_state(root) in {
                "updated",
                "unchanged",
                "appended",
                "created",
            }
        except Exception:  # pragma: no cover - defensive
            meta["managed_ok"] = None
    return meta


def _archive_index(root: Path) -> dict[str, Any]:
    directory = root / "memory" / "archive"
    names: list[str] = []
    try:
        if directory.is_dir():
            names = sorted(item.name for item in directory.iterdir() if item.is_file())
    except OSError:
        names = []
    return {
        "dir": "memory/archive",
        "count": len(names),
        "names": names[:ARCHIVE_INDEX_LIMIT],
    }


def _unavailable(plugin: Any) -> Any:
    reason = plugin.install_reason() or "patch not installed"
    return json_response(
        {
            "installed": False,
            "reason": reason,
            # "message" mirrors "reason" so the plugin page bridge, which only
            # surfaces message/error, can show the actionable cause.
            "message": reason,
            "shared_root": plugin.shared_root_text(),
        },
        status_code=503,
    )


async def state_handler(plugin: Any) -> Any:
    """GET /astrbot_plugin_astral/state (001 FR-7)."""
    payload = plugin.state_payload()
    if payload.get("installed"):
        return json_response(payload)
    body = dict(payload)
    body["message"] = body.get("reason") or "patch not installed"
    return json_response(body, status_code=503)


async def files_handler(plugin: Any) -> Any:
    """GET /astrbot_plugin_astral/files (002 FR-6)."""
    if not plugin.is_installed():
        return _unavailable(plugin)
    root = plugin.shared_root()
    if root is None or not root.is_dir():
        return _unavailable(plugin)

    requested = request.query.get("name")
    raw_name = "" if requested is None else str(requested).strip()
    if not raw_name:
        return json_response(
            {
                "shared_root": str(root),
                "memory_soft_limit": plugin.memory_options()["soft_limit_chars"],
                "files": [_file_meta(root, name) for name in MEMORY_NAMES],
                "archive": _archive_index(root),
            },
        )

    name = raw_name.upper()
    relative = FILE_BY_NAME.get(name)
    if relative is None:
        return json_response(
            {"error": "unknown_name", "message": f"unknown name: {raw_name}"},
            status_code=400,
        )

    path = root / relative
    if not _within_root(root, path):
        return json_response(
            {"error": "forbidden_path", "message": "path escapes the shared root"},
            status_code=400,
        )
    try:
        if not path.exists() or path.is_dir():
            return json_response(
                {"error": "not_found", "message": "file not found", "name": name},
                status_code=404,
            )
        if path.is_symlink():
            return json_response(
                {"error": "forbidden_path", "message": "symbolic link rejected"},
                status_code=400,
            )
        text, truncated = _read_limited(path)
        stat = path.stat()
    except OSError as exc:
        return json_response(
            {
                "error": "read_failed",
                "message": "read failed",
                "detail": exc.__class__.__name__,
            },
            status_code=500,
        )

    return json_response(
        {
            "name": name,
            "path": relative,
            "abs_path": str(path),
            "exists": True,
            "size": stat.st_size,
            "chars": len(text),
            "mtime": _iso(stat.st_mtime),
            "encoding": "utf-8",
            "truncated": truncated,
            "content": text,
        },
    )


async def clean_handler(plugin: Any) -> Any:
    """POST /astrbot_plugin_astral/clean (001 FR-11)."""
    if not plugin.is_installed():
        return _unavailable(plugin)
    root = plugin.shared_root()
    if root is None or not root.is_dir():
        return _unavailable(plugin)

    body = await request.json(default={})
    days_value: Any = 0
    if isinstance(body, dict):
        days_value = body.get("days", 0)
    try:
        days = max(float(days_value), 0.0)
    except (TypeError, ValueError):
        days = 0.0

    result = plugin.clean_archive(days)
    return json_response(
        {
            "moved": result.get("moved", 0),
            "dest": result.get("dest"),
            "files": result.get("files", []),
            "days": days,
        },
    )
