"""Render the frozen per-conversation Astral memory snapshot.

Pure standard-library module: no network, no database, no writes to disk. The
only side effects are read-only file access and log records. Any unexpected
failure degrades to a tiny block so an LLM request never fails because of the
snapshot (spec FR-3, NFR-7).
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("astrbot")

# Fixed header / footer text (plan section 4.1). Keep them as constants so tests
# can assert on them and a v1 -> v2 upgrade is a one-line change.
HEADER = "[Astral Memory Snapshot] v1"
HEADER_TEMPLATE = HEADER + " | conv={conv} | frozen_at={frozen_at}"
PRIORITY_LINE = (
    "优先级：AstrBot 安全策略 > SOUL.md 的边界 > Persona Instructions > "
    "SOUL.md 的身份与语气 > MEMORY.md 的事实。"
)
FREEZE_LINE = "本快照在本对话内冻结，不随后续消息刷新。"
SOUL_TITLE = "### SOUL.md（身份与边界 · {chars} 字符）"
MEMORY_TITLE = "### MEMORY.md（长期记忆 · {chars} 字符）"
FOOTER_TITLE = "### 快照说明"
FOOTER_LINES = (
    "- 以上内容在本对话开始时冻结，磁盘上的文件可能已更新。",
    "- 需要最新内容：用 astrbot_file_read_tool 读取 SOUL.md / MEMORY.md（相对路径以当前工作区根为基准）。",
    "- 想换成新快照：/new。",
)
FOOTER = "\n".join((FOOTER_TITLE,) + FOOTER_LINES)

# Minimal Chinese fallback for unexpected failures. Must stay <= 200 chars.
DEGRADED_BLOCK = (
    HEADER + " | conv=unavailable\n"
    "（记忆快照不可用：读取记忆文件时发生未预期错误，详见日志；本对话可继续正常进行。）"
)

ARCHIVE_INDEX_LIMIT = 10
DEFAULT_SOUL_BUDGET = 4000
DEFAULT_MEMORY_BUDGET = 8000
DEFAULT_SOFT_LIMIT = 20000


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
        f"[已截断：{name} 原文 {total} 字符，此处仅保留前 {budget} 字符。"
        f"完整内容：{abs_path}（可用 astrbot_file_read_tool 读取）]"
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
        return "[归档索引] memory/archive/：" + "、".join(names)
    except Exception:  # noqa: BLE001 - archive listing never breaks injection
        logger.debug("astral: snapshot: archive listing failed", exc_info=True)
        return None


def _section_body(section: _Section, budget: int, archive_root: Path | None) -> str:
    if section.state == "empty":
        return f"（{section.name} 为空文件）"
    if section.state == "missing":
        return (
            f"（{section.name} 不存在：{section.abs_path}；"
            "需要时可用 astrbot_file_write_tool 创建）"
        )
    if section.state == "escape":
        return f"（{section.name} 指向共享根之外，已拒绝读取）"
    if section.state == "error":
        return section.notice or f"（读取失败：OSError；详见日志）"

    text = section.text or ""
    body = text
    budget = max(0, int(budget))
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
    # Trailing newlines are not meaningful in the rendered block; drop them so
    # exactly one blank line separates sections (plan section 4.1).
    return body.rstrip("\r\n")


def _capacity_hint(chars: int, soft_limit: int) -> str:
    return (
        "### 容量提示\n"
        f"- MEMORY.md 当前 {chars} 字符，超过软上限 {soft_limit} 字符。\n"
        "- 建议：先合并重复与过期条目，再把历史条目剪切到 "
        "memory/archive/MEMORY-YYYYMM.md。"
    )


def _format_header(cid: str | None, now: str | None) -> str:
    conv = (cid or "no-conv")[:8]
    if now is None:
        frozen_at = datetime.now().astimezone().isoformat(timespec="seconds")
    else:
        frozen_at = now
    return HEADER_TEMPLATE.format(conv=conv, frozen_at=frozen_at)


def _both_unavailable(root: Path, header: str) -> str:
    label = "（SOUL.md 与 MEMORY.md 均不可用："
    path = str(root)
    block = f"{header}\n{label}{path}）"
    if len(block) > 200:
        available = 200 - len(header) - 1 - len(label) - 1
        if available > 1 and len(path) > available:
            path = "…" + path[-(available - 1):]
            block = f"{header}\n{label}{path}）"
    if len(block) > 200:
        block = block[:200]
    return block


def _render(
    root: Path,
    *,
    soul_budget: int,
    memory_budget: int,
    soft_limit: int,
    cid: str | None,
    now: str | None,
) -> str:
    header = _format_header(cid, now)
    root = Path(root)
    try:
        root_resolved = root.resolve()
    except OSError:
        root_resolved = root.absolute()
    if not root.is_dir():
        logger.warning("astral: snapshot: shared root missing: %s", root)
        return _both_unavailable(root, header)

    soul = _load_section(root, root_resolved, "SOUL.md")
    memory = _load_section(root, root_resolved, "MEMORY.md")
    if not _usable(soul) and not _usable(memory):
        logger.warning("astral: snapshot: both memory files unavailable: %s", root)
        return _both_unavailable(root, header)

    parts = [
        header,
        PRIORITY_LINE,
        FREEZE_LINE,
        "",
        SOUL_TITLE.format(chars=soul.chars),
        _section_body(soul, soul_budget, None),
        "",
        MEMORY_TITLE.format(chars=memory.chars),
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
    cid: str | None = None,
    now: str | None = None,
) -> str:
    """Render one frozen snapshot block. Never raises."""
    try:
        return _render(
            Path(root),
            soul_budget=soul_budget,
            memory_budget=memory_budget,
            soft_limit=soft_limit,
            cid=cid,
            now=now,
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
    "FOOTER_TITLE",
    "FREEZE_LINE",
    "HEADER",
    "HEADER_TEMPLATE",
    "MEMORY_TITLE",
    "PRIORITY_LINE",
    "SOUL_TITLE",
    "SnapshotCache",
    "render",
]
