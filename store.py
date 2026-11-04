"""Filesystem helpers for Astral metadata, audit, quota and archive.

Covers 001 FR-11 (audit / archive / quota) and the '.astral/' self metadata
from FR-8. The module has no third-party dependencies; log records go through
AstrBot's logger so plugin log levels apply.
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from astrbot.api import logger

ASTRAL_DIR = ".astral"
STATE_FILE = "state.json"
AUDIT_FILE = "audit.jsonl"
ARCHIVE_DIR = "archive"

#: Top level entries that '/astral clean' never touches.
ARCHIVE_EXCLUDES: tuple[str, ...] = ("skills", "shared", "memory", "archive", ASTRAL_DIR)

#: Workspace content files: never archived, never rewritten.
PROTECTED_FILES: tuple[str, ...] = (
    "SOUL.md",
    "MEMORY.md",
    "AGENTS.md",
    "EXTRA_PROMPT.md",
)


def iso_now() -> str:
    """Return the current local time as an ISO 8601 string."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def astral_dir(root: Path) -> Path:
    """Return the '.astral' metadata directory of a shared root."""
    return Path(root) / ASTRAL_DIR


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.tmp.{os.getpid()}"
    try:
        with tmp.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def write_state(root: Path, payload: dict[str, Any]) -> Path:
    """Atomically write '.astral/state.json' and return its path."""
    target = astral_dir(root) / STATE_FILE
    _atomic_write(target, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return target


def read_state(root: Path) -> dict[str, Any] | None:
    """Read '.astral/state.json'; returns None when absent or unreadable."""
    target = astral_dir(root) / STATE_FILE
    try:
        with target.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def append_audit(
    root: Path,
    action: str,
    detail: Any = None,
    *,
    ts: str | None = None,
) -> dict[str, Any]:
    """Append one JSON line to '.astral/audit.jsonl' (001 FR-11).

    Audit failures never block the caller: they are logged and the record is
    still returned.
    """
    record = {
        "ts": ts or iso_now(),
        "action": str(action),
        "detail": detail,
    }
    target = astral_dir(root) / AUDIT_FILE
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        logger.warning("astral: audit write failed action=%s (%s)", action, exc)
    return record


def read_audit(root: Path, limit: int | None = None) -> list[dict[str, Any]]:
    """Read audit records, oldest first; unparsable lines are skipped."""
    target = astral_dir(root) / AUDIT_FILE
    records: list[dict[str, Any]] = []
    try:
        with target.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except ValueError:
                    continue
                if isinstance(item, dict):
                    records.append(item)
    except OSError:
        return []
    if limit is not None and limit >= 0:
        return records[-limit:] if limit else []
    return records


def dir_stats(path: Path) -> dict[str, Any]:
    """Return file count and size of a directory tree (never raises)."""
    target = Path(path)
    if not target.is_dir():
        return {"exists": False, "files": 0, "size_mb": 0.0, "bytes": 0}
    files = 0
    total = 0
    try:
        for current, dirnames, filenames in os.walk(target):
            dirnames[:] = [
                name for name in dirnames if not (Path(current) / name).is_symlink()
            ]
            for name in filenames:
                candidate = Path(current) / name
                try:
                    if candidate.is_symlink():
                        continue
                    total += candidate.stat().st_size
                    files += 1
                except OSError:
                    continue
    except OSError:
        pass
    return {
        "exists": True,
        "files": files,
        "size_mb": round(total / (1024 * 1024), 3),
        "bytes": total,
    }


def quota_state(root: Path, limit_gb: float) -> dict[str, Any]:
    """Compare the shared root size against the warn-only quota (FR-11)."""
    stats = dir_stats(root)
    limit = max(float(limit_gb or 0.0), 0.0)
    size_bytes = int(stats.get("bytes") or 0)
    size_gb = round(size_bytes / (1024 * 1024 * 1024), 4)
    limit_bytes = limit * 1024 * 1024 * 1024
    return {
        "limit_gb": limit,
        "size_gb": size_gb,
        "warn": bool(limit > 0 and size_bytes > limit_bytes),
        "files": stats["files"],
    }


def archive_candidates(
    root: Path,
    older_than_days: float,
    *,
    now: datetime | None = None,
) -> list[Path]:
    """List stray top level files older than the given age (FR-11)."""
    target_root = Path(root)
    if not target_root.is_dir():
        return []
    current = now or datetime.now().astimezone()
    try:
        cutoff = current.timestamp() - max(float(older_than_days), 0.0) * 86400
    except (TypeError, ValueError):
        cutoff = current.timestamp()
    candidates: list[Path] = []
    try:
        entries = list(target_root.iterdir())
    except OSError:
        return []
    for entry in entries:
        name = entry.name
        if name in ARCHIVE_EXCLUDES or name in PROTECTED_FILES:
            continue
        try:
            if entry.is_symlink() or not entry.is_file():
                continue
            if entry.stat().st_mtime > cutoff:
                continue
        except OSError:
            continue
        candidates.append(entry)
    return sorted(candidates, key=lambda item: item.name)


def archive_move(
    root: Path,
    older_than_days: float,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Move expired stray files into 'archive/<yyyyMMdd-HHmmss>/'.

    Files are moved, never deleted and never copied, so a wipe of 'archive/'
    restores the previous state.
    """
    target_root = Path(root)
    stamp_source = now or datetime.now().astimezone()
    candidates = archive_candidates(target_root, older_than_days, now=stamp_source)
    if not candidates:
        return {"moved": 0, "dest": None, "files": []}
    stamp = stamp_source.strftime("%Y%m%d-%H%M%S")
    destination = target_root / ARCHIVE_DIR / stamp
    suffix = 1
    while destination.exists():
        suffix += 1
        destination = target_root / ARCHIVE_DIR / f"{stamp}-{suffix}"
    destination.mkdir(parents=True, exist_ok=True)
    moved: list[str] = []
    for source in candidates:
        target = destination / source.name
        counter = 1
        while target.exists():
            counter += 1
            target = destination / f"{source.stem}-{counter}{source.suffix}"
        try:
            shutil.move(str(source), str(target))
        except OSError as exc:
            logger.warning("astral: archive move failed for %s (%s)", source, exc)
            continue
        moved.append(target.name)
    if not moved:
        try:
            destination.rmdir()
        except OSError:
            pass
        return {"moved": 0, "dest": None, "files": []}
    logger.info(
        "astral: archived %d file(s) into %s",
        len(moved),
        destination,
    )
    return {
        "moved": len(moved),
        "dest": str(destination),
        "files": sorted(moved),
    }
