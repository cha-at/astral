"""Tests for the Astral metadata store (store.py).

Covers archive by moving, append-only JSONL audit and quota warnings
without deleting, plus the robustness case.
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime
from pathlib import Path

import pytest

import store

CONTENT_FILES = ("SOUL.md", "MEMORY.md", "AGENTS.md", "EXTRA_PROMPT.md")
PROTECTED_DIRS = ("skills", "shared", "memory", "archive", ".astral")


def _age_file(path: Path, days: float) -> None:
    """Backdate a file's mtime by ``days`` days."""
    stamp = time.time() - days * 86400
    os.utime(path, (stamp, stamp))


# ---------------------------------------------------------------------------
# .astral metadata directory
# ---------------------------------------------------------------------------


def test_astral_dir_layout(tmp_path):
    assert store.ASTRAL_DIR == ".astral"
    assert store.STATE_FILE == "state.json"
    assert store.AUDIT_FILE == "audit.jsonl"
    assert store.astral_dir(tmp_path) == tmp_path / ".astral"


def test_write_state_creates_directory_and_roundtrips(tmp_path):
    root = tmp_path / "shared"
    root.mkdir()
    payload = {
        "installed": True,
        "root": str(root),
        "note": "共享根",
        "nested": {"items": [1, 2, 3]},
        "flag": False,
    }
    written = store.write_state(root, payload)
    assert written == store.astral_dir(root) / store.STATE_FILE
    assert written.is_file()
    assert store.read_state(root) == payload


def test_write_state_overwrites_atomically(tmp_path):
    root = tmp_path / "shared"
    store.write_state(root, {"version": 1})
    store.write_state(root, {"version": 2})
    assert store.read_state(root) == {"version": 2}
    # No temporary files may be left behind next to state.json.
    leftovers = [item.name for item in store.astral_dir(root).iterdir() if item.is_file()]
    assert leftovers == [store.STATE_FILE]


def test_read_state_returns_none_when_missing(tmp_path):
    root = tmp_path / "shared"
    root.mkdir()
    assert store.read_state(root) is None


def test_iso_now_is_iso8601():
    value = store.iso_now()
    assert isinstance(value, str)
    assert len(value) >= 19
    assert value[4] == "-"
    assert value[10] == "T"
    assert datetime.fromisoformat(value)


# ---------------------------------------------------------------------------
# FR-11 audit log (AC-11.2)
# ---------------------------------------------------------------------------


def test_append_audit_writes_jsonl(tmp_path):
    """AC-11.2: every line is JSON with ts/action/detail."""
    root = tmp_path / "shared"
    root.mkdir()
    first = store.append_audit(root, "install", {"root": str(root)})
    assert first["action"] == "install"
    assert first["detail"] == {"root": str(root)}
    assert isinstance(first["ts"], str) and first["ts"]

    second = store.append_audit(root, "clean")
    assert second["action"] == "clean"
    assert "detail" in second
    assert not second["detail"]

    audit_file = store.astral_dir(root) / store.AUDIT_FILE
    lines = [line for line in audit_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == 2
    entries = [json.loads(line) for line in lines]
    for entry in entries:
        assert {"ts", "action", "detail"} <= set(entry)
    assert [entry["action"] for entry in entries] == ["install", "clean"]


def test_append_audit_honours_explicit_ts(tmp_path):
    root = tmp_path / "shared"
    entry = store.append_audit(root, "install", None, ts="2026-01-02T03:04:05+08:00")
    assert entry["ts"] == "2026-01-02T03:04:05+08:00"
    assert store.read_audit(root)[0]["ts"] == "2026-01-02T03:04:05+08:00"


def test_read_audit_preserves_order_and_limit(tmp_path):
    root = tmp_path / "shared"
    for action in ("seed", "archive", "clean"):
        store.append_audit(root, action, {"step": action})

    entries = store.read_audit(root)
    assert [entry["action"] for entry in entries] == ["seed", "archive", "clean"]

    limited = store.read_audit(root, limit=2)
    assert len(limited) == 2
    assert all(isinstance(entry, dict) for entry in limited)


def test_read_audit_returns_empty_list_when_missing(tmp_path):
    assert store.read_audit(tmp_path / "shared") == []


# ---------------------------------------------------------------------------
# FR-11 disk statistics and quota (AC-11.3, E-9)
# ---------------------------------------------------------------------------


def test_dir_stats_missing_path(tmp_path):
    """E-9: a missing path is reported, not raised."""
    stats = store.dir_stats(tmp_path / "does-not-exist")
    assert stats["exists"] is False
    assert stats["files"] == 0
    assert stats["size_mb"] == 0.0


def test_dir_stats_counts_whole_tree(tmp_path):
    """AC-11.3: quota accounting must include nested directories."""
    root = tmp_path / "shared"
    (root / "shared").mkdir(parents=True)
    (root / "a.txt").write_text("hello", encoding="utf-8")
    (root / "b.bin").write_bytes(b"x" * 1024)
    (root / "shared" / "c.txt").write_text("nested", encoding="utf-8")

    stats = store.dir_stats(root)
    assert stats["exists"] is True
    assert stats["files"] == 3
    assert isinstance(stats["size_mb"], float)
    assert 0 < stats["size_mb"] < 1


def test_quota_state_is_disabled_for_non_positive_limits(tmp_path):
    root = tmp_path / "shared"
    root.mkdir()
    (root / "a.txt").write_text("data", encoding="utf-8")
    for limit in (0, 0.0, -1.0):
        state = store.quota_state(root, limit)
        assert state["warn"] is False
        assert isinstance(state["size_gb"], float)
        assert "limit_gb" in state


def test_quota_state_warns_without_deleting(tmp_path):
    """AC-11.3: over-quota only warns; files stay untouched."""
    root = tmp_path / "shared"
    root.mkdir()
    payload = b"x" * (2 * 1024 * 1024)
    big = root / "big.bin"
    big.write_bytes(payload)

    state = store.quota_state(root, 0.0001)
    assert state["warn"] is True
    assert float(state["limit_gb"]) == pytest.approx(0.0001)
    assert state["size_gb"] > 0
    assert big.exists()
    assert big.stat().st_size == len(payload)


def test_quota_state_does_not_warn_below_limit(tmp_path):
    root = tmp_path / "shared"
    (root / "shared").mkdir(parents=True)
    (root / "a.txt").write_text("data", encoding="utf-8")
    state = store.quota_state(root, 10.0)
    assert state["warn"] is False
    assert 0 <= state["size_gb"] < 10.0


# ---------------------------------------------------------------------------
# FR-11 archive (AC-11.1)
# ---------------------------------------------------------------------------


def _prepare_workspace(root: Path) -> dict:
    """Create a workspace with stale, fresh and protected files."""
    protected: dict[str, Path] = {}
    for relative in (
        ("skills", "demo", "SKILL.md"),
        ("shared", "asset.txt"),
        ("memory", "note.md"),
        ("archive", "older.txt"),
        (".astral", "state.json"),
    ):
        path = root.joinpath(*relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(path.name, encoding="utf-8")
        _age_file(path, 90)
        protected[str(path)] = path

    for name in CONTENT_FILES:
        path = root / name
        path.write_text(f"# {name}\n", encoding="utf-8")
        _age_file(path, 90)
        protected[str(path)] = path

    stale: dict[str, Path] = {}
    for name in ("scratch.txt", "build.log"):
        path = root / name
        path.write_text(f"stale {name}", encoding="utf-8")
        _age_file(path, 30)
        stale[name] = path

    fresh = root / "fresh.txt"
    fresh.write_text("fresh", encoding="utf-8")
    return {"protected": protected, "stale": stale, "fresh": fresh}


def test_archive_move_moves_only_stale_top_level_files(tmp_path):
    """AC-11.1: stale top-level files are moved, never copied or deleted."""
    root = tmp_path / "shared"
    root.mkdir()
    layout = _prepare_workspace(root)

    result = store.archive_move(root, 7)

    assert result["moved"] == 2
    dest = Path(result["dest"])
    assert dest.is_dir()
    assert dest.parent == root / "archive"
    assert re.fullmatch(r"\d{8}-\d{6}", dest.name)

    moved_names = {str(item).replace("\\", "/") for item in result["files"]}
    assert moved_names == {"scratch.txt", "build.log"}

    for name, path in layout["stale"].items():
        assert not path.exists()
        target = dest / name
        assert target.is_file()
        assert target.read_text(encoding="utf-8") == f"stale {name}"

    for path in layout["protected"].values():
        assert path.exists()
    assert layout["fresh"].exists()


def test_archive_move_uses_provided_now_for_destination(tmp_path):
    root = tmp_path / "shared"
    root.mkdir()
    old = root / "old.txt"
    old.write_text("old", encoding="utf-8")
    stamp = datetime(2021, 1, 1, 0, 0, 0).timestamp()
    os.utime(old, (stamp, stamp))

    now = datetime(2021, 6, 1, 12, 34, 56)
    result = store.archive_move(root, 30, now=now)

    assert result["moved"] == 1
    assert Path(result["dest"]).name == "20210601-123456"
    assert (Path(result["dest"]) / "old.txt").is_file()
    assert not old.exists()


def test_archive_move_zero_days_moves_recent_files(tmp_path):
    """AC-11.1: /astral clean 0 archives every stale-enough file."""
    root = tmp_path / "shared"
    root.mkdir()
    recent = root / "artifact.txt"
    recent.write_text("artifact", encoding="utf-8")
    _age_file(recent, 1 / 24)

    result = store.archive_move(root, 0)

    assert result["moved"] == 1
    assert not recent.exists()


def test_archive_move_returns_zero_when_nothing_is_stale(tmp_path):
    root = tmp_path / "shared"
    root.mkdir()
    soul = root / "SOUL.md"
    soul.write_text("soul", encoding="utf-8")
    _age_file(soul, 365)

    result = store.archive_move(root, 1)

    assert result["moved"] == 0
    assert list(result["files"]) == []
    assert soul.exists()
