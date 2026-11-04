"""Render the frozen per-conversation Astral memory snapshot.

No third-party dependencies beyond AstrBot's logger: no network, no database,
no writes to disk. The only side effects are read-only file access and log
records. Any unexpected failure degrades to a tiny block so an LLM request
never fails because of the snapshot (spec FR-3, NFR-7).

The rendered block is written for the model, not for a maintainer: it reads as
"here is who you are and what you remember", carries no file lengths, no
conversation ids and no plugin bookkeeping, and strips the HTML comments that
hold notes for humans.
"""

from __future__ import annotations

import re
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from astrbot.api import logger

# Fixed header / footer text. Keep them as constants so tests can assert on
# them and a v1 -> v2 upgrade is a one-line change.
HEADER = "# 关于你自己"
INTRO_LINES = (
    "下面两段是对话开始时你读到的东西：你是怎样说话的人，以及你记得的事。",
    "它们是你自己的东西，不是别人派给你的任务——照这个说话就行。",
    "别念标题，也别提「文件」「文档」「快照」「提示词」这类词。",
)
INTRO = "\n".join(INTRO_LINES)
SOUL_TITLE = "## 你的性格和说话方式"
MEMORY_TITLE = "## 你记得"
FOOTER_LINES = (
    "以上是对话开始时读到的，之后可能已经变过；和这轮说的冲突时，以现在为准。",
    "想改自己：先问，再改 SOUL.md；想记东西：写 MEMORY.md（相对工作区根）。",
)
FOOTER = "\n".join(FOOTER_LINES)

# Minimal Chinese fallback for unexpected failures. Must stay <= 200 chars.
DEGRADED_BLOCK = (
    HEADER + "\n"
    "（这份记忆这次没读出来，详见日志；对话可以正常继续。）"
)

ARCHIVE_INDEX_LIMIT = 10
DEFAULT_SOUL_BUDGET = 4000
DEFAULT_MEMORY_BUDGET = 8000
DEFAULT_SOFT_LIMIT = 20000

# Notes written for humans live in HTML comments; they must never reach the
# model together with the seeded boilerplate.
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_BLANK_LINES_RE = re.compile(r"\n{3,}")


@dataclass
class _Section:
    """Decoded state of one memory file."""

    name: str
    abs_path: str
    state: str  # ok | non_utf8 | empty | missing | error | escape
    text: str | None = None
    chars: int = 0
    notice: str | None = None


def _read_bytes(path: Path) -> bytes:
    """Read a memory file; tests monkeypatch this to count filesystem reads."""
    return path.read_bytes()


def _is_within(path: Path, root: Path) -> bool:
    try:
        return path.resolve().is_relative_to(root.resolve())
    except (OSError, RuntimeError, ValueError):
        return False


def strip_comments(text: str) -> str:
    """Drop HTML comments and the blank lines they leave behind."""
    cleaned = _COMMENT_RE.sub("", text)
    cleaned = _BLANK_LINES_RE.sub("\n\n", cleaned)
    return cleaned.strip("\r\n")


def _load_section(root: Path, root_resolved: Path, name: str) -> _Section:
    path = root / name
    try:
        abs_path = str(path.resolve())
    except OSError:
        abs_path = str(path.absolute())
    try:
        if path.is_symlink():
            try:
                target = path.resolve()
            except OSError:
                target = None
            if target is None or not _is_within(target, root_resolved):
                logger.warning(
                    "astral: snapshot: %s symlink escapes shared root: %s", name, path
                )
                return _Section(name, abs_path, "escape")
        if not path.exists() or path.is_dir():
            logger.info("astral: snapshot: %s missing: %s", name, path)
            return _Section(name, abs_path, "missing")
        data = _read_bytes(path)
    except OSError as exc:
        logger.warning(
            "astral: snapshot: cannot read %s: %s", name, path, exc_info=True
        )
        return _Section(
            name,
            abs_path,
            "error",
            notice=f"（读取失败：{type(exc).__name__}；详见日志）",
        )

    if not data.strip():
        return _Section(name, abs_path, "empty")
    try:
        text = data.decode("utf-8")
        state = "ok"
    except UnicodeDecodeError:
        text = data.decode("utf-8", errors="replace")
        state = "non_utf8"
        logger.warning("astral: snapshot: %s is not valid UTF-8: %s", name, path)
    return _Section(name, abs_path, state, text=text, chars=len(text))


def _usable(section: _Section) -> bool:
    return section.state in ("ok", "non_utf8", "empty")


def _truncation_notice(name: str, total: int, budget: int, abs_path: str) -> str:
    return (
        f"[太长：{name} 原文 {total} 字符，这里只留了前 {budget} 字符。"
        f"全文：{abs_path}]"
    )


def _archive_index(root: Path, limit: int = ARCHIVE_INDEX_LIMIT) -> str | None:
    """One-line index of memory/archive/*.md, or None when unavailable."""
    archive = Path(root) / "memory" / "archive"
    try:
        if not archive.is_dir():
            return None
        entries: list[tuple[str, int]] = []
        for item in archive.iterdir():
            try:
                if not item.is_file() or item.suffix.lower() != ".md":
                    continue
                entries.append((item.name, item.stat().st_size))
            except OSError:
                continue
        if not entries:
            return None
        entries.sort(key=lambda pair: pair[0])
        names = [f"{name} ({size / 1024:.1f} KB)" for name, size in entries[:limit]]
        return "（旧记忆在 memory/archive/：" + "、".join(names) + "）"
    except Exception:  # noqa: BLE001 - archive listing never breaks injection
        logger.debug("astral: snapshot: archive listing failed", exc_info=True)
        return None


def _section_body(section: _Section, budget: int, archive_root: Path | None) -> str:
    if section.state == "empty":
        return "（还是空的）"
    if section.state == "missing":
        return f"（还没有这份文件：{section.abs_path}）"
    if section.state == "escape":
        return "（指向共享根之外，已跳过）"
    if section.state == "error":
        return section.notice or "（读取失败：OSError；详见日志）"

    text = strip_comments(section.text or "")
    if not text:
        return "（还是空的）"
    budget = max(0, int(budget))
    body = text
    if len(text) > budget:
        body = text[:budget] + "\n" + _truncation_notice(
            section.name, len(text), budget, section.abs_path
        )
        if archive_root is not None:
            index = _archive_index(archive_root)
            if index:
                body += "\n" + index
    if section.state == "non_utf8":
        body += "\n（含非 UTF-8 字节，已按替换字符显示）"
    return body.rstrip("\r\n")


def _capacity_hint(chars: int, soft_limit: int) -> str:
    return (
        "## 容量提示\n"
        f"- MEMORY.md 现在 {chars} 字符，超过软上限 {soft_limit} 字符。\n"
        "- 先把重复和过期的合并，再把旧条目剪到 memory/archive/MEMORY-YYYYMM.md。"
    )


def _both_unavailable(root: Path, header: str = HEADER) -> str:
    label = "（这两份记忆文件这次都没读出来："
    path = str(root)
    block = f"{header}\n{label}{path}；对话可以正常继续，详见日志。）"
    if len(block) > 200:
        available = 200 - len(header) - 1 - len(label) - 1 - len("；对话可以正常继续，详见日志。）")
        if available > 1 and len(path) > available:
            path = "…" + path[-(available - 1):]
            block = f"{header}\n{label}{path}；对话可以正常继续，详见日志。）"
    if len(block) > 200:
        block = block[:200]
    return block


def _render(
    root: Path,
    *,
    soul_budget: int,
    memory_budget: int,
    soft_limit: int,
) -> str:
    root = Path(root)
    try:
        root_resolved = root.resolve()
    except OSError:
        root_resolved = root.absolute()
    if not root.is_dir():
        logger.warning("astral: snapshot: shared root missing: %s", root)
        return _both_unavailable(root)

    soul = _load_section(root, root_resolved, "SOUL.md")
    memory = _load_section(root, root_resolved, "MEMORY.md")
    if not _usable(soul) and not _usable(memory):
        logger.warning("astral: snapshot: both memory files unavailable: %s", root)
        return _both_unavailable(root)

    parts = [
        HEADER,
        "",
        INTRO,
        "",
        SOUL_TITLE,
        _section_body(soul, soul_budget, None),
        "",
        MEMORY_TITLE,
        _section_body(memory, memory_budget, root),
        "",
        FOOTER,
    ]
    block = "\n".join(parts)
    if _usable(memory) and memory.chars > soft_limit:
        block += "\n\n" + _capacity_hint(memory.chars, soft_limit)
    return block


def render(
    root: Path,
    *,
    soul_budget: int = DEFAULT_SOUL_BUDGET,
    memory_budget: int = DEFAULT_MEMORY_BUDGET,
    soft_limit: int = DEFAULT_SOFT_LIMIT,
) -> str:
    """Render one frozen snapshot block. Never raises."""
    try:
        return _render(
            Path(root),
            soul_budget=soul_budget,
            memory_budget=memory_budget,
            soft_limit=soft_limit,
        )
    except Exception:
        logger.exception("astral: snapshot render failed")
        return DEGRADED_BLOCK


class SnapshotCache:
    """Process-local LRU of rendered snapshot blocks (never persisted)."""

    def __init__(self, maxsize: int = 64) -> None:
        self._maxsize = max(0, int(maxsize))
        self._entries: OrderedDict[str, str] = OrderedDict()

    def get_or_render(
        self, key: str, render_fn: Callable[[], str]
    ) -> tuple[str, bool]:
        """Return (block, hit). A hit performs no filesystem access."""
        if key in self._entries:
            self._entries.move_to_end(key)
            return self._entries[key], True
        value = render_fn()
        self._entries[key] = value
        self._entries.move_to_end(key)
        if self._maxsize <= 0:
            self._entries.clear()
        else:
            while len(self._entries) > self._maxsize:
                self._entries.popitem(last=False)
        return value, False

    def clear(self, key: str | None = None) -> None:
        if key is None:
            self._entries.clear()
        else:
            self._entries.pop(key, None)

    def __len__(self) -> int:
        return len(self._entries)


__all__ = [
    "ARCHIVE_INDEX_LIMIT",
    "DEFAULT_MEMORY_BUDGET",
    "DEFAULT_SOFT_LIMIT",
    "DEFAULT_SOUL_BUDGET",
    "DEGRADED_BLOCK",
    "FOOTER",
    "FOOTER_LINES",
    "HEADER",
    "INTRO",
    "INTRO_LINES",
    "MEMORY_TITLE",
    "SOUL_TITLE",
    "SnapshotCache",
    "render",
    "strip_comments",
]
