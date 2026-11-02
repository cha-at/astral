"""Tests for versioning.py (system Git, four memory files).

The module is pure standard library, but the tests need a real git executable.
The whole module is skipped when git is not installed.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

import versioning

GIT_PATH = shutil.which("git")
pytestmark = pytest.mark.skipif(GIT_PATH is None, reason="git is not installed")
GIT = GIT_PATH or "git"
GIT_ARG = GIT_PATH or ""


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    cp = subprocess.run(
        [GIT, "-c", "core.autocrlf=false", *args],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if cp.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {cp.stderr}")
    return cp


def _test_commit(root: Path, message: str, *paths: str) -> None:
    if paths:
        _git(root, "add", "--", *paths)
    _git(
        root,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.com",
        "commit",
        "-q",
        "-m",
        message,
    )


def _workspace(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    root.mkdir()
    (root / "SOUL.md").write_text("soul\n", encoding="utf-8")
    (root / "MEMORY.md").write_text("v1\n", encoding="utf-8")
    (root / "AGENTS.md").write_text("agents\n", encoding="utf-8")
    (root / "EXTRA_PROMPT.md").write_text("extra\n", encoding="utf-8")
    (root / ".astral").mkdir()
    (root / "shared").mkdir()
    (root / "shared" / "out.txt").write_text("out\n", encoding="utf-8")
    (root / "skills").mkdir()
    return root


def _ensure(root: Path) -> dict:
    return versioning.ensure_repo(root, git_path=GIT_ARG)


def _commit(root: Path) -> dict:
    return versioning.commit(root, git_path=GIT_ARG)


def _summary(root: Path, **kwargs) -> dict:
    return versioning.summary(root, git_path=GIT_ARG, **kwargs)


def _recover(root: Path, target: str) -> dict:
    return versioning.recover(root, target=target, git_path=GIT_ARG)


def _ls_files(root: Path) -> set[str]:
    return {
        line
        for line in _git(root, "ls-files").stdout.splitlines()
        if line.strip()
    }


def test_ensure_repo_creates_baseline_and_tracks_four_files(tmp_path):
    root = _workspace(tmp_path)
    report = _ensure(root)

    assert report["ok"] is True
    assert report["available"] is True
    assert report["initialized"] is True
    assert report["owned"] is True
    assert report["created"] is True
    assert (root / ".git").is_dir()
    assert (root / ".git" / versioning.MARKER_FILE).is_file()

    exclude = (root / ".git" / "info" / "exclude").read_text(encoding="utf-8")
    assert "/*" in exclude
    for name in versioning.TRACKED_FILES:
        assert f"!/{name}" in exclude

    assert _ls_files(root) == set(versioning.TRACKED_FILES)
    assert report["baseline"] == _summary(root)["head"]
    assert _summary(root)["count"] == 1


def test_ensure_repo_is_idempotent(tmp_path):
    root = _workspace(tmp_path)
    first = _ensure(root)
    second = _ensure(root)

    assert second["created"] is False
    assert second["owned"] is True
    assert second["head"] == first["head"]
    assert _summary(root)["count"] == 1
    assert _git(root, "rev-list", "--count", "HEAD").stdout.strip() == "1"


def test_commit_only_tracks_memory_files(tmp_path):
    root = _workspace(tmp_path)
    _ensure(root)
    (root / "MEMORY.md").write_text("v2\n", encoding="utf-8")
    (root / "stray.txt").write_text("stray\n", encoding="utf-8")
    (root / "shared" / "out.txt").write_text("changed\n", encoding="utf-8")

    result = _commit(root)

    assert result["status"] == "committed"
    assert result["files"] == ["MEMORY.md"]
    assert _ls_files(root) == set(versioning.TRACKED_FILES)
    shown = [
        line
        for line in _git(
            root, "show", "--pretty=format:", "--name-only", "HEAD"
        ).stdout.splitlines()
        if line.strip()
    ]
    assert shown == ["MEMORY.md"]


def test_commit_no_changes_is_noop(tmp_path):
    root = _workspace(tmp_path)
    report = _ensure(root)
    (root / "stray.txt").write_text("stray\n", encoding="utf-8")

    result = _commit(root)

    assert result["status"] == "no_changes"
    assert result["hash"] == report["baseline"]
    assert _git(root, "rev-list", "--count", "HEAD").stdout.strip() == "1"


def test_commit_records_deletion_and_recover_restores(tmp_path):
    root = _workspace(tmp_path)
    report = _ensure(root)
    baseline = report["baseline"]
    (root / "AGENTS.md").unlink()

    result = _commit(root)

    assert result["status"] == "committed"
    assert "AGENTS.md" in result["files"]
    assert "AGENTS.md" not in _ls_files(root)

    recovered = _recover(root, baseline)

    assert recovered["status"] == "recovered"
    assert (root / "AGENTS.md").read_text(encoding="utf-8") == "agents\n"


def test_recover_auto_saves_dirty_state_and_appends(tmp_path):
    root = _workspace(tmp_path)
    report = _ensure(root)
    baseline = report["baseline"]
    (root / "MEMORY.md").write_text("v2\n", encoding="utf-8")
    _commit(root)
    (root / "MEMORY.md").write_text("dirty\n", encoding="utf-8")

    result = _recover(root, baseline)

    assert result["status"] == "recovered"
    assert result["target"] == baseline
    assert result["before"] != baseline
    assert (root / "MEMORY.md").read_text(encoding="utf-8") == "v1\n"
    assert (
        _git(root, "show", f"{result['before']}:MEMORY.md").stdout
        == "dirty\n"
    )
    assert _git(root, "rev-list", "--count", "HEAD").stdout.strip() == "4"

    # The recovery itself can be undone by recovering the auto-save commit.
    again = _recover(root, result["before"])
    assert again["status"] == "recovered"
    assert (root / "MEMORY.md").read_text(encoding="utf-8") == "dirty\n"


def test_recover_noop_when_target_is_head(tmp_path):
    root = _workspace(tmp_path)
    report = _ensure(root)

    result = _recover(root, report["baseline"])

    assert result["status"] == "no_changes"
    assert (root / "MEMORY.md").read_text(encoding="utf-8") == "v1\n"
    assert _git(root, "rev-list", "--count", "HEAD").stdout.strip() == "1"


def test_recover_rejects_invalid_targets(tmp_path):
    root = _workspace(tmp_path)
    _ensure(root)
    before = (root / "MEMORY.md").read_bytes()

    for target in ("--help", "zzzz", "deadbeef", ""):
        with pytest.raises(versioning.VersioningError):
            _recover(root, target)

    assert (root / "MEMORY.md").read_bytes() == before


def test_recover_rejects_non_checkpoint_commit(tmp_path):
    root = _workspace(tmp_path)
    _ensure(root)
    _git(
        root,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.com",
        "commit",
        "--allow-empty",
        "-q",
        "-m",
        "empty",
    )
    empty = _git(root, "rev-parse", "--short", "HEAD").stdout.strip()

    with pytest.raises(versioning.VersioningError):
        _recover(root, empty)


def test_recover_rejects_non_ancestor_commit(tmp_path):
    root = _workspace(tmp_path)
    report = _ensure(root)
    (root / "MEMORY.md").write_text("v2\n", encoding="utf-8")
    v2 = _commit(root)["hash"]
    _git(root, "reset", "--hard", report["baseline"])

    with pytest.raises(versioning.VersioningError):
        _recover(root, v2)


def test_recover_rejects_merge_commit(tmp_path):
    root = _workspace(tmp_path)
    _ensure(root)
    _git(root, "checkout", "-q", "-b", "side")
    (root / "SOUL.md").write_text("side\n", encoding="utf-8")
    _test_commit(root, "side", "SOUL.md")
    _git(root, "checkout", "-q", "main")
    (root / "MEMORY.md").write_text("main\n", encoding="utf-8")
    _test_commit(root, "main", "MEMORY.md")
    _git(
        root,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.com",
        "merge",
        "--no-ff",
        "side",
        "-m",
        "merge side",
    )
    merge = _git(root, "rev-parse", "--short", "HEAD").stdout.strip()

    with pytest.raises(versioning.VersioningError):
        _recover(root, merge)


def test_commit_rejects_symlink(tmp_path):
    root = _workspace(tmp_path)
    _ensure(root)
    (root / "SOUL.md").unlink()
    try:
        (root / "SOUL.md").symlink_to("MEMORY.md")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available")

    with pytest.raises(versioning.VersioningError):
        _commit(root)


def test_commit_and_recover_reject_oversized_files(tmp_path, monkeypatch):
    root = _workspace(tmp_path)
    _ensure(root)
    monkeypatch.setattr(versioning, "MAX_FILE_BYTES", 8)
    (root / "MEMORY.md").write_text("x" * 20, encoding="utf-8")
    with pytest.raises(versioning.VersioningError):
        _commit(root)

    monkeypatch.setattr(versioning, "MAX_FILE_BYTES", 10 * 1024 * 1024)
    huge = _commit(root)
    assert huge["status"] == "committed"
    (root / "MEMORY.md").write_text("small\n", encoding="utf-8")
    _commit(root)

    monkeypatch.setattr(versioning, "MAX_FILE_BYTES", 8)
    with pytest.raises(versioning.VersioningError):
        _recover(root, huge["hash"])


def test_crlf_and_non_utf8_bytes_are_preserved(tmp_path):
    root = _workspace(tmp_path)
    (root / "MEMORY.md").write_bytes(b"a\r\nb\r\n")
    (root / "SOUL.md").write_bytes(b"\xff\xfe")
    baseline = _ensure(root)["baseline"]
    (root / "MEMORY.md").write_bytes(b"c\r\n")
    _commit(root)

    result = _recover(root, baseline)

    assert result["status"] == "recovered"
    assert (root / "MEMORY.md").read_bytes() == b"a\r\nb\r\n"
    assert (root / "SOUL.md").read_bytes() == b"\xff\xfe"


def test_summary_reports_head_dirty_and_disabled(tmp_path, monkeypatch):
    root = _workspace(tmp_path)
    _ensure(root)
    summary = _summary(root)
    assert summary["enabled"] is True
    assert summary["initialized"] is True
    assert summary["head"]
    assert summary["count"] == 1
    assert summary["dirty"] is False

    (root / "MEMORY.md").write_text("dirty\n", encoding="utf-8")
    assert _summary(root)["dirty"] is True
    _commit(root)
    assert _summary(root)["dirty"] is False
    assert _summary(root, enabled=False) == {
        "enabled": False,
        "available": None,
        "initialized": False,
    }

    monkeypatch.setattr(versioning, "find_git", lambda configured="": None)
    missing = _summary(root)
    assert missing["available"] is False
    assert missing["reason"]


def test_invalid_repo_is_reported_without_touching_files(tmp_path):
    root = _workspace(tmp_path)
    (root / ".git").mkdir()
    before = (root / "MEMORY.md").read_bytes()

    report = _ensure(root)
    assert report["ok"] is False
    assert report["initialized"] is False
    assert report["reason"]
    with pytest.raises(versioning.VersioningError):
        _commit(root)
    assert (root / "MEMORY.md").read_bytes() == before


def test_adopted_repo_only_commits_memory_files(tmp_path):
    root = tmp_path / "adopted"
    root.mkdir()
    (root / "other.txt").write_text("base\n", encoding="utf-8")
    _git(root, "init", "-q")
    _test_commit(root, "base", "other.txt")
    for name, text in {
        "SOUL.md": "soul\n",
        "MEMORY.md": "v1\n",
        "AGENTS.md": "agents\n",
        "EXTRA_PROMPT.md": "extra\n",
    }.items():
        (root / name).write_text(text, encoding="utf-8")

    report = _ensure(root)
    assert report["owned"] is False
    assert report["created"] is False
    assert not (root / ".git" / versioning.MARKER_FILE).exists()

    (root / "other.txt").write_text("staged\n", encoding="utf-8")
    _git(root, "add", "other.txt")
    (root / "MEMORY.md").write_text("v2\n", encoding="utf-8")
    result = _commit(root)

    assert result["status"] == "committed"
    assert set(result["files"]) == set(versioning.TRACKED_FILES)
    assert "other.txt" not in result["files"]
    assert _git(root, "show", "HEAD:other.txt").stdout == "base\n"
    assert "other.txt" in _git(root, "diff", "--cached", "--name-only").stdout


def test_list_versions_limit_and_order(tmp_path):
    root = _workspace(tmp_path)
    _ensure(root)
    (root / "MEMORY.md").write_text("v2\n", encoding="utf-8")
    second = _commit(root)["hash"]
    (root / "MEMORY.md").write_text("v3\n", encoding="utf-8")
    third = _commit(root)["hash"]

    listing = versioning.list_versions(root, git_path=GIT_ARG, limit=2)
    assert listing["status"] == "ok"
    assert [item["hash"] for item in listing["versions"]] == [third, second]
    assert all(item["files"] == ["MEMORY.md"] for item in listing["versions"])

    one = versioning.list_versions(root, git_path=GIT_ARG, limit=0)
    assert len(one["versions"]) == 1


def test_find_git_accepts_file_directory_and_rejects_missing():
    assert Path(versioning.find_git(GIT_ARG)).samefile(GIT_PATH)
    assert Path(versioning.find_git(str(Path(GIT_PATH).parent))).samefile(GIT_PATH)
    assert versioning.find_git("definitely-missing-astral-git") is None
