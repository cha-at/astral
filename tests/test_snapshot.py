"""Unit tests for snapshot.py (spec 002 FR-3 / FR-5; plan sections 4 and 9.1)."""

from __future__ import annotations

from pathlib import Path

import pytest

import snapshot

NOW = "2026-11-01T12:34:56+08:00"


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")


def _counting_reads(monkeypatch) -> list[Path]:
    calls: list[Path] = []
    original = snapshot._read_bytes

    def read(path):
        calls.append(Path(path))
        return original(path)

    monkeypatch.setattr(snapshot, "_read_bytes", read)
    return calls


def _write_both(root: Path, soul: str = "灵魂内容\n", memory: str = "- 记忆条目\n") -> None:
    _write(root / "SOUL.md", soul)
    _write(root / "MEMORY.md", memory)


def test_render_basic(tmp_path, monkeypatch):
    soul = "我是 Astral。\n"
    memory = "- [2026-11-01] [系统] 测试条目。\n"
    _write(tmp_path / "SOUL.md", soul)
    _write(tmp_path / "MEMORY.md", memory)
    calls = _counting_reads(monkeypatch)

    block = snapshot.render(tmp_path, cid="abc123456789", now=NOW)

    assert len(calls) == 2
    assert block.startswith(snapshot.HEADER + " | conv=abc12345 | frozen_at=" + NOW)
    assert snapshot.PRIORITY_LINE in block
    assert snapshot.FREEZE_LINE in block
    assert "### SOUL.md（身份与边界 · " + str(len(soul)) + " 字符）" in block
    assert "### MEMORY.md（长期记忆 · " + str(len(memory)) + " 字符）" in block
    assert soul.rstrip("\n") in block
    assert memory.rstrip("\n") in block
    for line in snapshot.FOOTER_LINES:
        assert line in block
    assert block.index("### SOUL.md") < block.index("### MEMORY.md")
    assert block.index("### MEMORY.md") < block.index("### 快照说明")
    fixed = len(block) - len(soul.rstrip("\n")) - len(memory.rstrip("\n"))
    assert 300 <= fixed <= 500


def test_render_missing_and_empty(tmp_path):
    _write(tmp_path / "MEMORY.md", "记忆内容\n")

    block = snapshot.render(tmp_path, cid="c", now=NOW)
    assert "（SOUL.md 不存在：" in block
    assert str((tmp_path / "SOUL.md").resolve()) in block
    assert "需要时可用 astrbot_file_write_tool 创建" in block
    assert "记忆内容" in block

    (tmp_path / "SOUL.md").write_bytes(b"")
    block = snapshot.render(tmp_path, cid="c", now=NOW)
    assert "（SOUL.md 为空文件）" in block
    assert "记忆内容" in block


def test_render_directory_treated_as_missing(tmp_path):
    (tmp_path / "SOUL.md").mkdir()
    _write(tmp_path / "MEMORY.md", "记忆内容\n")
    block = snapshot.render(tmp_path, cid="c", now=NOW)
    assert "（SOUL.md 不存在：" in block
    assert "记忆内容" in block


def test_render_non_utf8(tmp_path):
    (tmp_path / "SOUL.md").write_bytes(b"\xff\xfe")
    _write(tmp_path / "MEMORY.md", "记忆内容\n")
    block = snapshot.render(tmp_path, cid="c", now=NOW)
    assert "含非 UTF-8 字节，已按替换字符显示" in block
    assert "\ufffd" in block
    assert "记忆内容" in block


def test_render_both_missing(tmp_path):
    block = snapshot.render(tmp_path, cid="c", now=NOW)
    assert len(block) <= 200
    assert "均不可用" in block
    assert str(tmp_path) in block
    assert block.splitlines()[0].startswith(snapshot.HEADER)
    assert len(block.splitlines()) == 2


def test_render_root_missing(tmp_path):
    missing = tmp_path / "missing"
    block = snapshot.render(missing, cid="c", now=NOW)
    assert len(block) <= 200
    assert "均不可用" in block

    long_root = tmp_path / ("x" * 300)
    block = snapshot.render(long_root, cid="c", now=NOW)
    assert len(block) <= 200


def test_degraded_block_is_small():
    assert snapshot.DEGRADED_BLOCK
    assert len(snapshot.DEGRADED_BLOCK) <= 200


def test_render_cid_first_eight_and_no_conv(tmp_path):
    _write_both(tmp_path)
    block = snapshot.render(tmp_path, cid="0123456789abcdef", now=NOW)
    assert "conv=01234567 |" in block

    block = snapshot.render(tmp_path, now=NOW)
    assert "conv=no-conv |" in block


def test_render_truncate(tmp_path):
    _write(tmp_path / "SOUL.md", "灵魂\n")
    _write(tmp_path / "MEMORY.md", "A" * 12000)

    block = snapshot.render(tmp_path, cid="c", now=NOW, memory_budget=8000)
    title = "### MEMORY.md（长期记忆 · 12000 字符）"
    assert title in block
    body_start = block.index(title) + len(title) + 1
    body_end = block.index("\n\n### 快照说明")
    body = block[body_start:body_end]
    abs_path = str((tmp_path / "MEMORY.md").resolve())
    notice = snapshot._truncation_notice("MEMORY.md", 12000, 8000, abs_path)
    assert body == "A" * 8000 + "\n" + notice
    assert len(body) <= 8000 + len(notice) + 1
    assert abs_path in body


def test_render_truncate_soul_budget(tmp_path):
    _write(tmp_path / "SOUL.md", "S" * 5000)
    _write(tmp_path / "MEMORY.md", "记忆\n")
    block = snapshot.render(tmp_path, cid="c", now=NOW, soul_budget=4000)
    assert "### SOUL.md（身份与边界 · 5000 字符）" in block
    assert "[已截断：SOUL.md 原文 5000 字符，此处仅保留前 4000 字符。" in block


def _seed_truncated(tmp_path: Path) -> Path:
    _write(tmp_path / "SOUL.md", "灵魂\n")
    _write(tmp_path / "MEMORY.md", "B" * 12000)
    archive = tmp_path / "memory" / "archive"
    archive.mkdir(parents=True)
    return archive


def test_render_archive_index(tmp_path):
    archive = _seed_truncated(tmp_path)
    (archive / "MEMORY-202608.md").write_bytes(b"x" * 12595)
    (archive / "MEMORY-202609.md").write_bytes(b"x" * 8294)

    block = snapshot.render(tmp_path, cid="c", now=NOW)
    assert "[归档索引]" in block
    assert "MEMORY-202608.md (12.3 KB)" in block
    assert "MEMORY-202609.md (8.1 KB)" in block


def test_render_archive_index_limited_to_ten(tmp_path):
    archive = _seed_truncated(tmp_path)
    for month in range(1, 13):
        (archive / ("MEMORY-2026%02d.md" % month)).write_bytes(b"x" * 1024)

    block = snapshot.render(tmp_path, cid="c", now=NOW)
    index_line = next(line for line in block.splitlines() if line.startswith("[归档索引]"))
    assert index_line.count(".md") == 10
    for month in range(1, 11):
        assert ("MEMORY-2026%02d.md" % month) in index_line
    assert "MEMORY-202611.md" not in block
    assert "MEMORY-202612.md" not in block


def test_render_archive_index_omitted_without_dir(tmp_path):
    _write(tmp_path / "SOUL.md", "灵魂\n")
    _write(tmp_path / "MEMORY.md", "B" * 12000)
    block = snapshot.render(tmp_path, cid="c", now=NOW)
    assert "[已截断：" in block
    assert "[归档索引]" not in block


def test_render_soft_limit(tmp_path):
    _write(tmp_path / "SOUL.md", "灵魂\n")
    _write(tmp_path / "MEMORY.md", "C" * 21000)
    block = snapshot.render(tmp_path, cid="c", now=NOW)
    assert "### 容量提示" in block
    assert block.index("### 快照说明") < block.index("### 容量提示")
    tail = block[block.index("### 容量提示"):]
    assert "21000" in tail
    assert "20000" in tail
    assert "memory/archive/MEMORY-YYYYMM.md" in tail


def test_render_soft_limit_configurable(tmp_path):
    _write(tmp_path / "SOUL.md", "灵魂\n")
    _write(tmp_path / "MEMORY.md", "D" * 600)
    block = snapshot.render(tmp_path, cid="c", now=NOW, soft_limit=500)
    assert "### 容量提示" in block
    assert "500" in block[block.index("### 容量提示"):]


def test_render_read_error(tmp_path, monkeypatch):
    _write_both(tmp_path)
    original = snapshot._read_bytes

    def flaky(path):
        if Path(path).name == "SOUL.md":
            raise PermissionError("denied")
        return original(path)

    monkeypatch.setattr(snapshot, "_read_bytes", flaky)
    block = snapshot.render(tmp_path, cid="c", now=NOW)
    assert "读取失败：PermissionError" in block
    assert "记忆条目" in block


def test_render_unexpected_exception_returns_degraded(tmp_path, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(snapshot, "_render", boom)
    block = snapshot.render(tmp_path, cid="c", now=NOW)
    assert block == snapshot.DEGRADED_BLOCK


def test_render_symlink_escape(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("TOP-SECRET-CONTENT\n", encoding="utf-8")
    link = root / "SOUL.md"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available")
    _write(root / "MEMORY.md", "mem\n")

    block = snapshot.render(root, cid="c", now=NOW)
    assert "指向共享根之外，已拒绝读取" in block
    assert "TOP-SECRET-CONTENT" not in block


def test_cache_hit_same_conv(tmp_path, monkeypatch):
    _write_both(tmp_path)
    calls = _counting_reads(monkeypatch)
    cache = snapshot.SnapshotCache()
    key = "umo:conv"

    first, hit_first = cache.get_or_render(
        key, lambda: snapshot.render(tmp_path, cid="conv12345678", now=NOW)
    )
    reads_after_first = len(calls)
    second, hit_second = cache.get_or_render(
        key, lambda: snapshot.render(tmp_path, cid="conv12345678", now=NOW)
    )

    assert hit_first is False
    assert hit_second is True
    assert first == second
    assert reads_after_first == 2
    assert len(calls) == 2
    assert len(cache) == 1


def test_cache_miss_new_conv(tmp_path, monkeypatch):
    _write_both(tmp_path)
    calls = _counting_reads(monkeypatch)
    cache = snapshot.SnapshotCache()
    blocks = []
    for cid in ("convAAAA1111", "convBBBB2222"):
        block, hit = cache.get_or_render(
            "umo:" + cid, lambda cid=cid: snapshot.render(tmp_path, cid=cid, now=NOW)
        )
        assert hit is False
        blocks.append(block)

    assert "conv=convAAAA" in blocks[0]
    assert "conv=convBBBB" in blocks[1]
    assert blocks[0] != blocks[1]
    assert len(calls) == 4
    assert len(cache) == 2


def test_cache_lru_eviction():
    cache = snapshot.SnapshotCache(maxsize=2)
    for key in ("a", "b", "c"):
        cache.get_or_render(key, lambda key=key: "value-" + key)
    assert len(cache) == 2

    value, hit = cache.get_or_render("c", lambda: "fresh")
    assert hit is True
    assert value == "value-c"

    value, hit = cache.get_or_render("a", lambda: "value-a2")
    assert hit is False
    assert value == "value-a2"
    assert len(cache) == 2


def test_cache_clear():
    cache = snapshot.SnapshotCache()
    cache.get_or_render("a", lambda: "1")
    cache.get_or_render("b", lambda: "2")
    cache.clear("a")
    assert len(cache) == 1
    cache.clear()
    assert len(cache) == 0

