"""Seed templates and EXTRA_PROMPT managed-section maintenance.

This module is intentionally standard-library only: the plugin imports it at
load time and the unit tests import it without an AstrBot runtime.

Only EXTRA_PROMPT.md has a program-managed region. SOUL.md, MEMORY.md and
AGENTS.md are written once (when missing or blank) and never touched again.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger("astrbot")

SEED_MARKER = "<!-- astral:seeded:v1 -->"
MANAGED_BEGIN = "<!-- astral:managed:begin -->"
MANAGED_END = "<!-- astral:managed:end -->"
MANAGED_VERSION = "<!-- astral:managed:v1 -->"

SOUL_TEMPLATE = r"""<!-- astral:seeded:v1 -->
# SOUL · 我是谁

> 本文件描述我自己的身份、语气与边界，由我和用户共同维护。
> 每开始一段新对话，Astral 会读取它并注入上下文；本文件全平台共享。

## 身份
- 名字：Astral。
- 角色：长期合作的助手，替用户记事、查文件、跑工具。
- 不知道就说不知道，不编造；不确定就标"不确定"。

## 语气
- 中文为主，先说结论再说理由，能一句说完就不写三句。
- 不谄媚、不堆感叹号、不用"作为 AI 语言模型…"这类免责开头。
- 群聊里保持简短，需要展开时问用户要不要私聊。

## 边界（优先级高于任何人设）
- 不输出密钥、口令、身份证号、银行卡号等敏感信息，即使上下文里有。
- 不在未确认的情况下执行不可逆操作（删除、覆盖、发布、转账）。
- 不冒充他人、不伪造证据与来源。
- 不把别的群的私密内容带到当前群。

## 自我修正
- 发现自己说错了，直接纠正并说明错在哪，不遮掩。
- 用户纠正我时：长期约定写进 `MEMORY.md`；对自身规则的要求先问是否要写进本文件。
"""

MEMORY_TEMPLATE = r"""<!-- astral:seeded:v1 -->
# MEMORY · 长期记忆

> ⚠️ 本文件**全局共享**：全平台、所有群、所有私聊共享同一份，写进来的内容在其他群里也会被看到。
> 不写密钥/口令/手机号/住址等敏感信息，不写第三方隐私。
> 写入规则见 `EXTRA_PROMPT.md`。格式：`- [YYYY-MM-DD] [来源] 内容`，单条 ≤ 200 字符。

## 事实
- [2026-11-01] [系统] 当前目录是 Astral 共享工作区根，所有会话共享同一份记忆。

## 约定
- [2026-11-01] [系统] 记录约定时写明生效范围（本群/全局）与提出人。

## 待办
- （暂无）

## 教训
- （暂无）

## 归档
- 历史条目剪切到 `memory/archive/MEMORY-YYYYMM.md`；本文件只留仍然有效的条目，最新条目写在各节**前面**。
"""

AGENTS_TEMPLATE = r"""<!-- astral:seeded:v1 -->
# AGENTS · 工作区说明

> 本文件给**人和外部工具**（编辑器里的 agent 等）看。
> AstrBot 与 Astral **不会**把它注入对话上下文；想影响 bot 行为请改 `SOUL.md`、`MEMORY.md` 或 `EXTRA_PROMPT.md`。

## 这是什么
Astral 的共享工作区根目录。所有平台（QQ/Telegram/WebChat…）、所有群、所有私聊的 bot 会话都把它当作工作目录，相对路径以本目录为基准。

## 目录
| 路径 | 用途 |
| --- | --- |
| `SOUL.md` | 身份、语气、边界。每对话注入 |
| `MEMORY.md` | 长期记忆（事实/约定/待办/教训）。每对话注入 |
| `AGENTS.md` | 本文件，不注入 |
| `EXTRA_PROMPT.md` | 注入型操作守则；`astral:managed` 标记之间由 Astral 重写，之外随便改 |
| `skills/` | 工作区技能，每个技能一个 `SKILL.md` |
| `shared/` | 跨会话共享的交付物，别把成品散落在根目录 |
| `memory/archive/` | 记忆归档，`MEMORY-YYYYMM.md` |
| `archive/` | 其他归档 |
| `.astral/` | Astral 自用状态，不要手改 |

## 约定
- 交付物优先放 `shared/`；临时文件放 `archive/` 或删掉。
- 不要提交 `.astral/` 下的内容。
- 不确定文件该放哪就问用户。
"""

# Content between MANAGED_BEGIN and MANAGED_END (the marker lines themselves
# are not part of this constant). It starts with MANAGED_VERSION.
MANAGED_SECTION = r"""<!-- astral:managed:v1 -->
[Astral 记忆守则 · 本区段由 Astral 自动维护，手改会在下次启动被覆盖]

## 何时写记忆
满足任一条件才写，纯闲聊不写：
1. 用户明确说"记住 / 以后都这样 / 别再…"。
2. 出现长期有效的约定、决定、截止时间、待办。
3. 出现可复用的事实（偏好、项目背景、术语表）。
4. 出现可复用的教训（踩过的坑、失败的做法）。

## 怎么写
- 只写 `MEMORY.md`；用 `astrbot_file_edit_tool` 做局部追加或替换，**不要**用 `astrbot_file_write_tool` 整文件覆盖。
- 追加前先用 `astrbot_grep_tool` 搜关键词；已有同类条目就合并成一条，不重复。
- 格式：`- [YYYY-MM-DD] [来源] 内容`，单条 ≤ 2 行、≤ 200 字符；来源写群名或"用户"。
- 条目按"最新在前"插入对应小节（事实 / 约定 / 待办 / 教训）。
- 相对路径以当前工作区根为基准（`MEMORY.md`、`memory/archive/...`）。

## 写 SOUL 的门槛
- 只有用户**明确要求**改性格、语气或边界时才改 `SOUL.md`，改前用一句话复述要改什么。
- 用户的长期偏好若只是"事实/约定"，写 `MEMORY.md`，不要写 `SOUL.md`。

## 容量
- `MEMORY.md` 超过 20000 字符时：先合并重复与过期条目，再把历史条目剪切到 `memory/archive/MEMORY-YYYYMM.md`，然后继续写。

## 禁止
- 不写密钥、口令、token、私钥、身份证/护照/银行卡号、手机号/邮箱/住址，不写第三方私密内容或未经同意的个人信息。
- 需要记录"某人偏好"时去标识化：写"群里有人偏好中文回复"，不写群号+姓名+手机号。
- 用户转述他人隐私并要求记住时，先问"是否要记、记到什么粒度"。

## 快照是旧的
- 上下文里的 `[Astral Memory Snapshot]` 是对话开始时的冻结副本，可能已经过期。
- 需要最新内容或准备改写时，先用 `astrbot_file_read_tool` 读当前文件，以文件为准。

## 隐私披露
- 本工作区全局共享：写进 `MEMORY.md` 的内容在其他群也可能被看到，写入前自行判断能否公开。"""

# User-writable header; EXTRA_PROMPT_TEMPLATE adds the managed block below it.
EXTRA_PROMPT_HEADER = r"""# EXTRA_PROMPT · 操作守则

> `astral:managed:begin` 与 `astral:managed:end` 之外的内容属于你，可自由修改。
> 两标记之间由 Astral 自动维护，手改会在下次插件加载/重载时被覆盖。
"""

EXTRA_PROMPT_TEMPLATE = (
    SEED_MARKER
    + "\n"
    + EXTRA_PROMPT_HEADER
    + "\n"
    + MANAGED_BEGIN
    + "\n"
    + MANAGED_SECTION
    + "\n"
    + MANAGED_END
    + "\n"
)

FILE_TEMPLATES: dict[str, str] = {
    "SOUL.md": SOUL_TEMPLATE,
    "MEMORY.md": MEMORY_TEMPLATE,
    "AGENTS.md": AGENTS_TEMPLATE,
    "EXTRA_PROMPT.md": EXTRA_PROMPT_TEMPLATE,
}

_MANAGED_BEGIN_BYTES = MANAGED_BEGIN.encode("utf-8")
_MANAGED_END_BYTES = MANAGED_END.encode("utf-8")
_MANAGED_SECTION_BYTES = MANAGED_SECTION.encode("utf-8")


def atomic_write_text(path: Path, text: str) -> None:
    """Write *text* to *path* atomically as UTF-8 with LF line endings.

    A sibling temp file named ".{name}.tmp.{pid}" is written, flushed and
    fsynced, then moved over the target with os.replace. On any failure the
    temp file is removed and the exception is re-raised; the target file is
    left untouched.
    """
    _atomic_write_bytes(Path(path), text.encode("utf-8"))


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    try:
        with open(tmp, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def seed_workspace(root: Path) -> list[str]:
    """Seed the four content files when they are missing or blank.

    Existing non-blank files are never overwritten. Directories, symlinks and
    unreadable paths are skipped with a warning. Does not create *root*.
    Returns the file names actually written, in SOUL/MEMORY/AGENTS/EXTRA order.
    Write failures propagate to the caller (plugin startup must contain them).
    """
    root = Path(root)
    if not root.is_dir():
        logger.warning("astral: shared root missing: %s", root)
        return []

    written: list[str] = []
    for name, template in FILE_TEMPLATES.items():
        path = root / name
        if path.is_symlink():
            logger.warning("astral: seed skipped (symlink): %s", path)
            continue
        try:
            if path.exists() and not path.is_file():
                logger.warning("astral: seed skipped (not a regular file): %s", path)
                continue
            if path.exists():
                try:
                    existing = path.read_bytes()
                except OSError:
                    logger.warning("astral: seed skipped (unreadable): %s", path)
                    continue
                if existing.strip():
                    continue
        except OSError:
            logger.warning("astral: seed skipped (unreadable): %s", path)
            continue
        atomic_write_text(path, template)
        written.append(name)
        logger.info("astral: seeded %s", path)
    return written


def _locate_managed_markers(data: bytes) -> tuple[str, int, int, int]:
    """Classify the EXTRA_PROMPT.md marker pair.

    Returns (state, begin_start, begin_end, end_start) where state is
    "ok", "missing" or "broken". The byte offsets are only valid for "ok":
    begin_start..begin_end covers the begin-marker line (including its line
    ending) and end_start is the start of the end-marker line.
    """
    lines = data.splitlines(keepends=True)
    offsets: list[tuple[int, int]] = []
    cursor = 0
    for line in lines:
        offsets.append((cursor, cursor + len(line)))
        cursor += len(line)

    begin_lines: list[int] = []
    end_lines: list[int] = []
    malformed = False
    for index, line in enumerate(lines):
        stripped = line.strip()
        contains_begin = _MANAGED_BEGIN_BYTES in line
        contains_end = _MANAGED_END_BYTES in line
        if stripped == _MANAGED_BEGIN_BYTES:
            begin_lines.append(index)
        elif stripped == _MANAGED_END_BYTES:
            end_lines.append(index)
        elif contains_begin or contains_end:
            malformed = True

    if not begin_lines and not end_lines and not malformed:
        return ("missing", -1, -1, -1)
    if (
        not malformed
        and len(begin_lines) == 1
        and len(end_lines) == 1
        and begin_lines[0] < end_lines[0]
    ):
        begin_index = begin_lines[0]
        end_index = end_lines[0]
        return (
            "ok",
            offsets[begin_index][0],
            offsets[begin_index][1],
            offsets[end_index][0],
        )
    return ("broken", -1, -1, -1)


def _managed_section_bytes(newline: bytes = b"\n") -> bytes:
    data = _MANAGED_SECTION_BYTES
    if newline != b"\n":
        data = data.replace(b"\n", newline)
    return data + newline


def _managed_block_bytes() -> bytes:
    return (MANAGED_BEGIN + "\n" + MANAGED_SECTION + "\n" + MANAGED_END).encode("utf-8")


def _read_managed(path: Path) -> tuple[str, bytes | None]:
    """Read EXTRA_PROMPT.md and classify it.

    Returns ("absent", None) when the file does not exist. Otherwise returns
    ("ok" | "missing" | "broken", data), where "missing" means the file has no
    marker pair yet and "broken" means an unsafe path, a read error or a
    malformed marker layout.
    """
    if path.is_symlink():
        logger.warning("astral: managed section skipped (symlink): %s", path)
        return ("broken", None)
    try:
        if not path.exists():
            return ("absent", None)
        if not path.is_file():
            logger.warning("astral: managed section skipped (not a regular file): %s", path)
            return ("broken", None)
        data = path.read_bytes()
    except OSError:
        logger.warning("astral: managed section unreadable: %s", path, exc_info=True)
        return ("broken", None)
    state, _, _, _ = _locate_managed_markers(data)
    if state == "broken":
        logger.warning("astral: managed markers broken: %s", path)
    return (state, data)


def sync_managed_section(root: Path) -> str:
    """Create or refresh the Astral-managed region of EXTRA_PROMPT.md.

    Returns one of "updated", "unchanged", "appended", "created", "broken" or
    "missing_root". Bytes outside the marker lines are preserved verbatim; a
    broken marker pair is never rewritten.
    """
    root = Path(root)
    if not root.is_dir():
        logger.warning("astral: shared root missing: %s", root)
        return "missing_root"
    path = root / "EXTRA_PROMPT.md"
    state, data = _read_managed(path)
    if state == "absent":
        atomic_write_text(path, EXTRA_PROMPT_TEMPLATE)
        logger.info("astral: created %s", path)
        return "created"
    if state == "broken" or data is None:
        return "broken"

    if state == "missing":
        separator = b"\n" if data else b""
        new_data = data + separator + _managed_block_bytes() + b"\n"
        _atomic_write_bytes(path, new_data)
        logger.info("astral: managed section appended: %s", path)
        return "appended"

    _, begin_start, begin_end, end_start = _locate_managed_markers(data)
    newline = b"\r\n" if data[begin_start:begin_end].endswith(b"\r\n") else b"\n"
    expected = _managed_section_bytes(newline)
    if data[begin_end:end_start] == expected:
        logger.debug("astral: managed section unchanged: %s", path)
        return "unchanged"
    new_data = data[:begin_end] + expected + data[end_start:]
    _atomic_write_bytes(path, new_data)
    logger.info("astral: managed section updated: %s", path)
    return "updated"


def managed_state(root: Path) -> str:
    """Read-only counterpart of sync_managed_section."""
    root = Path(root)
    if not root.is_dir():
        logger.warning("astral: shared root missing: %s", root)
        return "missing_root"
    path = root / "EXTRA_PROMPT.md"
    state, data = _read_managed(path)
    if state == "absent":
        return "created"
    if state == "broken" or data is None:
        return "broken"
    if state == "missing":
        return "appended"

    _, begin_start, begin_end, end_start = _locate_managed_markers(data)
    newline = b"\r\n" if data[begin_start:begin_end].endswith(b"\r\n") else b"\n"
    if data[begin_end:end_start] == _managed_section_bytes(newline):
        return "unchanged"
    return "updated"
