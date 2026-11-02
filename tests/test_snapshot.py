"""Unit tests for snapshot.py (spec 002 FR-3 / FR-5; plan sections 4 and 9.1).

The rendered block is written for the model, not for a maintainer: it must read
as "who you are and what you remember". These tests therefore assert both the
content and the absence of scaffolding (conversation ids, character counts, file
titles, HTML comments).
"""

from __future__ import annotations

from pathlib import Path

import pytest

import snapshot


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
    soul = "# 我是谁\n\n- 说话短一点。\n"
    memory = "## 谁是谁\n- 阿岚：群主，叫他岚总。\n"
    _write(tmp_path / "SOUL.md", soul)
    _write(tmp_path / "MEMORY.md", memory)
    calls = _counting_reads(monkeypatch)

    block = snapshot.render(tmp_path)

    assert len(calls) == 2
    assert block.startswith(snapshot.HEADER)
    assert snapshot.INTRO in block
    assert snapshot.SOUL_TITLE in block
    assert snapshot.MEMORY_TITLE in block
    assert "# 我是谁" in block
    assert "- 阿岚：群主，叫他岚总。" in block
    for line in snapshot.FOOTER_LINES:
        assert line in block
    assert block.index(snapshot.SOUL_TITLE) < block.index(snapshot.MEMORY_TITLE)
    assert block.index(snapshot.MEMORY_TITLE) < block.index(snapshot.FOOTER_LINES[0])
    fixed = (
        len(block)
        - len("# 我是谁\n\n- 说话短一点。")
        - len("## 谁是谁\n- 阿岚：群主，叫他岚总。")
    )
    assert 120 <= fixed <= 400


def test_render_has_no_maintainer_scaffolding(tmp_path):
    _write_both(tmp_path, soul="我是 Astral。\n", memory="- 条目\n")
    block = snapshot.render(tmp_path)
    for noise in (
        "conv=",
        "frozen_at",
        "Astral Memory Snapshot",
        "### SOUL.md",
        "### MEMORY.md",
        "字符）",
        "astral:seeded",
    ):
        assert noise not in block, noise


def test_strip_comments(tmp_path):
    _write(
        tmp_path / "SOUL.md",
        "<!-- astral:seeded:v1 -->\n<!-- 给人看的备注 -->\n# 我是谁\n\n- 短句。\n",
    )
    _write(tmp_path / "MEMORY.md", "## 谁是谁\n<!-- - 阿岚：群主。 -->\n")
    block = snapshot.render(tmp_path)
    assert "给人看的备注" not in block
    assert "阿岚" not in block
    assert "astral:seeded" not in block
    assert "# 我是谁" in block
    assert "- 短句。" in block


def test_strip_comments_multiline_and_blank_collapse():
    raw = "# 标题\n\n<!--\n  第一行\n  第二行\n-->\n\n\n\n正文\n"
    assert snapshot.strip_comments(raw) == "# 标题\n\n正文"
    assert snapshot.strip_comments("<!-- only comment -->") == ""


def test_render_missing_and_empty(tmp_path):
    _write(tmp_path / "MEMORY.md", "记忆内容\n")

    block = snapshot.render(tmp_path)
    assert "（还没有这份文件：" in block
    assert str((tmp_path / "SOUL.md").resolve()) in block
    assert "记忆内容" in block

    (tmp_path / "SOUL.md").write_bytes(b"")
    block = snapshot.render(tmp_path)
    assert "（还是空的）" in block
    assert "记忆内容" in block


def test_render_comment_only_file_counts_as_empty(tmp_path):
    _write(tmp_path / "SOUL.md", "<!-- 只有注释 -->\n")
    _write(tmp_path / "MEMORY.md", "记忆内容\n")
    block = snapshot.render(tmp_path)
    assert "（还是空的）" in block
    assert "只有注释" not in block


def test_render_directory_treated_as_missing(tmp_path):
    (tmp_path / "SOUL.md").mkdir()
    _write(tmp_path / "MEMORY.md", "记忆内容\n")
    block = snapshot.render(tmp_path)
    assert "（还没有这份文件：" in block
    assert "记忆内容" in block


def test_render_non_utf8(tmp_path):
    (tmp_path / "SOUL.md").write_bytes(b"\xff\xfe")
    _write(tmp_path / "MEMORY.md", "记忆内容\n")
    block = snapshot.render(tmp_path)
    assert "含非 UTF-8 字节，已按替换字符显示" in block
    assert "\ufffd" in block
    assert "记忆内容" in block


def test_render_both_missing(tmp_path):
    block = snapshot.render(tmp_path)
    assert len(block) <= 200
    assert "都没读出来" in block
    assert str(tmp_path) in block
    assert block.splitlines()[0] == snapshot.HEADER
    assert len(block.splitlines()) == 2


def test_render_root_missing(tmp_path):
    missing = tmp_path / "missing"
    block = snapshot.render(missing)
    assert len(block) <= 200
    assert "都没读出来" in block

    long_root = tmp_path / ("x" * 300)
    block = snapshot.render(long_root)
    assert len(block) <= 200


def test_degraded_block_is_small():
    assert snapshot.DEGRADED_BLOCK
    assert len(snapshot.DEGRADED_BLOCK) <= 200
    assert snapshot.DEGRADED_BLOCK.startswith(snapshot.HEADER)


def test_render_truncate(tmp_path):
    _write(tmp_path / "SOUL.md", "灵魂\n")
    _write(tmp_path / "MEMORY.md", "A" * 12000)

    block = snapshot.render(tmp_path, memory_budget=8000)
    title_index = block.index(snapshot.MEMORY_TITLE)
    body_start = title_index + len(snapshot.MEMORY_TITLE) + 1
    body_end = block.index("\n\n" + snapshot.FOOTER_LINES[0])
    body = block[body_start:body_end]
    abs_path = str((tmp_path / "MEMORY.md").resolve())
    notice = snapshot._truncation_notice("MEMORY.md", 12000, 8000, abs_path)
    assert body == "A" * 8000 + "\n" + notice
    assert abs_path in body


def test_render_truncate_soul_budget(tmp_path):
    _write(tmp_path / "SOUL.md", "S" * 5000)
    _write(tmp_path / "MEMORY.md", "记忆\n")
    block = snapshot.render(tmp_path, soul_budget=4000)
    assert "S" * 4000 in block
    assert "原文 5000 字符" in block


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

    block = snapshot.render(tmp_path)
    assert "（旧记忆在 memory/archive/：" in block
    assert "MEMORY-202608.md (12.3 KB)" in block
    assert "MEMORY-202609.md (8.1 KB)" in block


def test_render_archive_index_limited_to_ten(tmp_path):
    archive = _seed_truncated(tmp_path)
    for month in range(1, 13):
        (archive / ("MEMORY-2026%02d.md" % month)).write_bytes(b"x" * 1024)

    block = snapshot.render(tmp_path)
    index_line = next(line for line in block.splitlines() if "旧记忆在 memory/archive/" in line)
    assert index_line.count(".md") == 10
    for month in range(1, 11):
        assert ("MEMORY-2026%02d.md" % month) in index_line
    assert "MEMORY-202611.md" not in block
    assert "MEMORY-202612.md" not in block


def test_render_archive_index_omitted_without_dir(tmp_path):
    _write(tmp_path / "SOUL.md", "灵魂\n")
    _write(tmp_path / "MEMORY.md", "B" * 12000)
    block = snapshot.render(tmp_path)
    assert "[太长：" in block
    assert "旧记忆在 memory/archive/" not in block


def test_render_soft_limit(tmp_path):
    _write(tmp_path / "SOUL.md", "灵魂\n")
    _write(tmp_path / "MEMORY.md", "C" * 21000)
    block = snapshot.render(tmp_path)
    assert "## 容量提示" in block
    assert block.index(snapshot.FOOTER_LINES[0]) < block.index("## 容量提示")
    tail = block[block.index("## 容量提示"):]
    assert "21000" in tail
    assert "20000" in tail
    assert "memory/archive/MEMORY-YYYYMM.md" in tail


def test_render_soft_limit_configurable(tmp_path):
    _write(tmp_path / "SOUL.md", "灵魂\n")
    _write(tmp_path / "MEMORY.md", "D" * 600)
    block = snapshot.render(tmp_path, soft_limit=500)
    assert "## 容量提示" in block
    assert "500" in block[block.index("## 容量提示"):]


def test_render_read_error(tmp_path, monkeypatch):
    _write_both(tmp_path)
    original = snapshot._read_bytes

    def flaky(path):
        if Path(path).name == "SOUL.md":
            raise PermissionError("denied")
        return original(path)

    monkeypatch.setattr(snapshot, "_read_bytes", flaky)
    block = snapshot.render(tmp_path)
    assert "读取失败：PermissionError" in block
    assert "记忆条目" in block


def test_render_unexpected_exception_returns_degraded(tmp_path, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(snapshot, "_render", boom)
    block = snapshot.render(tmp_path)
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

    block = snapshot.render(root)
    assert "指向共享根之外，已跳过" in block
    assert "TOP-SECRET-CONTENT" not in block


def test_cache_hit_same_conv(tmp_path, monkeypatch):
    _write_both(tmp_path)
    calls = _counting_reads(monkeypatch)
    cache = snapshot.SnapshotCache()
    key = "umo:conv"

    first, hit_first = cache.get_or_render(
        key, lambda: snapshot.render(tmp_path)
    )
    reads_after_first = len(calls)
    second, hit_second = cache.get_or_render(
        key, lambda: snapshot.render(tmp_path)
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
    for cid in ("convAAAA1111", "convBBBB2222"):
        block, hit = cache.get_or_render("umo:" + cid, lambda: snapshot.render(tmp_path))
        assert hit is False
        assert block.startswith(snapshot.HEADER)

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
