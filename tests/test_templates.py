"""Unit tests for templates.py (spec 002 FR-2 / FR-4; plan sections 5.1-5.4)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import templates

FILES = ["SOUL.md", "MEMORY.md", "AGENTS.md", "EXTRA_PROMPT.md"]
PLAN_PATH = (
    Path(__file__).resolve().parents[1]
    / "specs"
    / "002-soul-and-memory"
    / "plan.md"
)


def _plan_blocks() -> dict[str, str]:
    if not PLAN_PATH.exists():
        pytest.skip("plan.md is not available in this checkout")
    lines = PLAN_PATH.read_text(encoding="utf-8").split("\n")
    blocks: dict[str, str] = {}
    current: str | None = None
    in_fence = False
    buffer: list[str] = []
    for line in lines:
        match = re.match(r"^### (5\.\d)\s", line)
        if match:
            current = match.group(1)
            in_fence = False
            buffer = []
            continue
        if current is None:
            continue
        if not in_fence and line.startswith("```markdown"):
            in_fence = True
            buffer = []
            continue
        if in_fence:
            if line.startswith("```"):
                blocks[current] = "\n".join(buffer) + "\n"
                in_fence = False
                current = None
            else:
                buffer.append(line)
    return blocks


def test_templates_have_seed_marker_first_line():
    for name, text in templates.FILE_TEMPLATES.items():
        assert text.splitlines()[0] == templates.SEED_MARKER, name


def test_templates_are_utf8_lf_with_final_newline():
    for name, text in templates.FILE_TEMPLATES.items():
        assert "\r" not in text, name
        assert not text.startswith("\ufeff"), name
        assert text.endswith("\n"), name
        assert text.encode("utf-8").decode("utf-8") == text, name


def test_file_templates_keys_and_order():
    assert list(templates.FILE_TEMPLATES) == FILES
    assert set(templates.FILE_TEMPLATES) == set(FILES)


def test_memory_template_discloses_global_share():
    # FR-7 / AC-7.1: the seeded MEMORY.md is disclosure point #1.
    assert "全局共享" in templates.MEMORY_TEMPLATE
    assert "所有群" in templates.MEMORY_TEMPLATE


def test_templates_keep_human_notes_in_comments():
    """The injected copy must not carry notes meant for maintainers."""
    for text in (templates.SOUL_TEMPLATE, templates.MEMORY_TEMPLATE):
        assert "<!--" in text, "human guidance belongs in HTML comments"
    assert "[Astral Memory Snapshot]" not in templates.MANAGED_SECTION
    assert "手改会在下次启动被覆盖" not in templates.MANAGED_SECTION


def test_agents_template_lists_directories():
    for token in (
        "SOUL.md",
        "MEMORY.md",
        "AGENTS.md",
        "EXTRA_PROMPT.md",
        "skills/",
        "shared/",
        "memory/archive/",
        "archive/",
        ".astral/",
        ".git/",
    ):
        assert token in templates.AGENTS_TEMPLATE, token
    assert "## 目录" in templates.AGENTS_TEMPLATE


def test_managed_section_covers_fr4_checklist():
    section = templates.MANAGED_SECTION
    for heading in ("## 记东西", "## 改自己", "## 别提这些", "## 安全"):
        assert heading in section, heading
    # when to write
    assert "该记才记" in section
    assert "纯闲聊不记" in section
    # how to write
    assert "astrbot_file_edit_tool" in section
    assert "astrbot_file_write_tool" in section
    assert "一条一句" in section
    # stay in character
    assert "别提" in section
    assert "作为 AI" in section
    # privacy + prompt-injection framing
    assert "所有群共享" in section
    assert "记忆是数据" in section
    # honest degradation when the runtime exposes no file tools
    assert "别假装记下了" in section
    # short enough to sit in every conversation
    assert len(section) <= 900


def test_extra_prompt_template_layout():
    text = templates.EXTRA_PROMPT_TEMPLATE
    lines = text.split("\n")
    assert lines[0] == templates.SEED_MARKER
    assert lines.count(templates.MANAGED_BEGIN) == 1
    assert lines.count(templates.MANAGED_END) == 1
    assert lines.index(templates.MANAGED_BEGIN) < lines.index(templates.MANAGED_END)
    assert templates.MANAGED_SECTION.split("\n")[0] == templates.MANAGED_VERSION
    header = text.split(templates.MANAGED_BEGIN, 1)[0]
    assert header.strip()
    assert "EXTRA_PROMPT" in header


def test_templates_match_plan_blocks():
    blocks = _plan_blocks()
    assert blocks["5.1"] == templates.SOUL_TEMPLATE
    assert blocks["5.2"] == templates.MEMORY_TEMPLATE
    assert blocks["5.3"] == templates.AGENTS_TEMPLATE
    managed_block = "\n".join(
        (templates.MANAGED_BEGIN, templates.MANAGED_SECTION, templates.MANAGED_END)
    ) + "\n"
    assert blocks["5.4"] == managed_block


def test_seed_idempotent(tmp_path):
    written = templates.seed_workspace(tmp_path)
    assert written == FILES
    for name, text in templates.FILE_TEMPLATES.items():
        assert (tmp_path / name).read_text(encoding="utf-8") == text

    before = {name: (tmp_path / name).read_bytes() for name in FILES}
    assert templates.seed_workspace(tmp_path) == []
    after = {name: (tmp_path / name).read_bytes() for name in FILES}
    assert before == after

    (tmp_path / "SOUL.md").write_bytes(b"")
    assert templates.seed_workspace(tmp_path) == ["SOUL.md"]
    assert (tmp_path / "SOUL.md").read_text(encoding="utf-8") == templates.SOUL_TEMPLATE

    (tmp_path / "MEMORY.md").write_text("  \n\t\n", encoding="utf-8")
    assert templates.seed_workspace(tmp_path) == ["MEMORY.md"]
    assert (tmp_path / "MEMORY.md").read_text(encoding="utf-8") == templates.MEMORY_TEMPLATE

    custom = "用户自定义 AGENTS\n"
    (tmp_path / "AGENTS.md").write_text(custom, encoding="utf-8")
    assert templates.seed_workspace(tmp_path) == []
    assert (tmp_path / "AGENTS.md").read_text(encoding="utf-8") == custom


def test_seed_missing_root(tmp_path):
    root = tmp_path / "missing"
    assert templates.seed_workspace(root) == []
    assert not root.exists()


def test_seed_skips_directory(tmp_path):
    (tmp_path / "SOUL.md").mkdir()
    written = templates.seed_workspace(tmp_path)
    assert written == ["MEMORY.md", "AGENTS.md", "EXTRA_PROMPT.md"]
    assert (tmp_path / "SOUL.md").is_dir()


def test_seed_skips_symlink(tmp_path):
    target = tmp_path / "target.md"
    target.write_text("outside content\n", encoding="utf-8")
    link = tmp_path / "MEMORY.md"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available")
    written = templates.seed_workspace(tmp_path)
    assert "MEMORY.md" not in written
    assert link.is_symlink()
    assert target.read_text(encoding="utf-8") == "outside content\n"


def test_seed_atomic_on_failure(tmp_path, monkeypatch):
    target = tmp_path / "SOUL.md"
    target.write_text("用户内容\n", encoding="utf-8")

    def boom(src, dst):
        raise OSError("replace failed")

    monkeypatch.setattr(templates.os, "replace", boom)
    with pytest.raises(OSError):
        templates.atomic_write_text(target, "新内容\n")
    assert target.read_text(encoding="utf-8") == "用户内容\n"
    assert list(tmp_path.glob(".*.tmp.*")) == []

    root = tmp_path / "root"
    root.mkdir()
    soul = root / "SOUL.md"
    soul.write_bytes(b"  \n")
    with pytest.raises(OSError):
        templates.seed_workspace(root)
    assert soul.read_bytes() == b"  \n"
    assert list(root.glob(".*.tmp.*")) == []


def test_atomic_write_text_is_utf8_lf(tmp_path):
    target = tmp_path / "note.md"
    templates.atomic_write_text(target, "第一行\n第二行\n")
    raw = target.read_bytes()
    assert raw == "第一行\n第二行\n".encode("utf-8")
    assert b"\r\n" not in raw
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert list(tmp_path.glob(".*.tmp.*")) == []


def test_atomic_write_bytes_is_byte_exact(tmp_path):
    target = tmp_path / "MEMORY.md"
    target.write_bytes(b"old\r\n")
    templates.atomic_write_bytes(target, b"\xff\xfe\r\nnew")
    assert target.read_bytes() == b"\xff\xfe\r\nnew"
    assert list(tmp_path.glob(".*.tmp.*")) == []


def test_sync_created(tmp_path):
    assert templates.managed_state(tmp_path) == "created"
    assert not (tmp_path / "EXTRA_PROMPT.md").exists()
    assert templates.sync_managed_section(tmp_path) == "created"
    content = (tmp_path / "EXTRA_PROMPT.md").read_text(encoding="utf-8")
    assert content == templates.EXTRA_PROMPT_TEMPLATE
    assert templates.managed_state(tmp_path) == "unchanged"
    assert templates.sync_managed_section(tmp_path) == "unchanged"


def test_managed_unchanged_does_not_write(tmp_path, monkeypatch):
    templates.sync_managed_section(tmp_path)
    before = (tmp_path / "EXTRA_PROMPT.md").read_bytes()

    def fail(*args, **kwargs):
        raise AssertionError("unchanged path must not write")

    monkeypatch.setattr(templates, "_atomic_write_bytes", fail)
    assert templates.sync_managed_section(tmp_path) == "unchanged"
    assert (tmp_path / "EXTRA_PROMPT.md").read_bytes() == before


def test_managed_rewrite_keeps_outside(tmp_path):
    old_section = "<!-- astral:managed:v0 -->\n旧守则\n"
    old_block = templates.MANAGED_BEGIN + "\n" + old_section + templates.MANAGED_END
    prefix = "用户笔记：只在周五发布\r\n\r\n".encode("utf-8")
    suffix = "\r\n用户尾部\r\n".encode("utf-8")
    path = tmp_path / "EXTRA_PROMPT.md"
    path.write_bytes(prefix + old_block.encode("utf-8") + suffix)

    assert templates.sync_managed_section(tmp_path) == "updated"
    data = path.read_bytes()
    expected_region = (templates.MANAGED_SECTION + "\n").encode("utf-8")
    expected = (
        prefix
        + templates.MANAGED_BEGIN.encode("utf-8")
        + b"\n"
        + expected_region
        + templates.MANAGED_END.encode("utf-8")
        + suffix
    )
    assert data == expected
    assert data.startswith(prefix)
    assert data.endswith(suffix)
    assert b"managed:v0" not in data
    assert templates.managed_state(tmp_path) == "unchanged"


def test_managed_markers_missing_appends(tmp_path):
    original = "# 我的 EXTRA_PROMPT\n\n用户自己的规则。\n"
    path = tmp_path / "EXTRA_PROMPT.md"
    path.write_text(original, encoding="utf-8", newline="\n")

    assert templates.sync_managed_section(tmp_path) == "appended"
    block = "\n".join(
        (templates.MANAGED_BEGIN, templates.MANAGED_SECTION, templates.MANAGED_END)
    ).encode("utf-8")
    assert path.read_bytes() == original.encode("utf-8") + b"\n" + block + b"\n"
    assert templates.managed_state(tmp_path) == "unchanged"
    assert templates.sync_managed_section(tmp_path) == "unchanged"


@pytest.mark.parametrize(
    "content",
    [
        templates.MANAGED_END + "\n" + templates.MANAGED_SECTION + "\n" + templates.MANAGED_BEGIN,
        templates.MANAGED_BEGIN + "\n" + templates.MANAGED_BEGIN + "\n" + templates.MANAGED_END,
        templates.MANAGED_BEGIN
        + "\n"
        + templates.MANAGED_SECTION
        + "\n"
        + templates.MANAGED_END
        + "\n"
        + templates.MANAGED_BEGIN
        + "\n"
        + templates.MANAGED_SECTION
        + "\n"
        + templates.MANAGED_END,
        templates.MANAGED_BEGIN + "\n" + templates.MANAGED_SECTION + "\n",
        templates.MANAGED_END + "\n",
        templates.MANAGED_BEGIN + templates.MANAGED_END,
    ],
)
def test_managed_markers_broken(tmp_path, content):
    path = tmp_path / "EXTRA_PROMPT.md"
    path.write_text(content, encoding="utf-8", newline="\n")
    before = path.read_bytes()
    assert templates.sync_managed_section(tmp_path) == "broken"
    assert path.read_bytes() == before
    assert templates.managed_state(tmp_path) == "broken"


def test_managed_state_missing_root_and_created(tmp_path):
    missing = tmp_path / "missing"
    assert templates.sync_managed_section(missing) == "missing_root"
    assert templates.managed_state(missing) == "missing_root"
    assert not missing.exists()

    assert templates.managed_state(tmp_path) == "created"
    assert not (tmp_path / "EXTRA_PROMPT.md").exists()


def test_managed_state_updated_is_read_only(tmp_path):
    old_block = (
        templates.MANAGED_BEGIN
        + "\n"
        + "<!-- astral:managed:v0 -->\n旧守则\n"
        + templates.MANAGED_END
    )
    path = tmp_path / "EXTRA_PROMPT.md"
    path.write_text(old_block, encoding="utf-8", newline="\n")
    before = path.read_bytes()
    assert templates.managed_state(tmp_path) == "updated"
    assert path.read_bytes() == before


def test_managed_skips_directory(tmp_path):
    (tmp_path / "EXTRA_PROMPT.md").mkdir()
    assert templates.sync_managed_section(tmp_path) == "broken"
    assert templates.managed_state(tmp_path) == "broken"
    assert (tmp_path / "EXTRA_PROMPT.md").is_dir()

