"""State payload and user visible status rendering.

Kept out of main.py so the plugin entry point stays a thin assembly file.
Everything here is read-only: it inspects the plugin object, the patch status
and the shared root, then returns plain data or Chinese text.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

from astrbot.api import logger

if __package__:
    from . import store as store_mod
    from . import versioning
    from . import ws_shared
else:  # pragma: no cover - direct import for tests
    import store as store_mod
    import versioning
    import ws_shared

MEMORY_SHOW_PRIVATE_LIMIT = 3000

MEMORY_FILE_BY_ALIAS: dict[str, str] = {
    "SOUL": "SOUL.md",
    "MEMORY": "MEMORY.md",
    "AGENTS": "AGENTS.md",
    "EXTRA": "EXTRA_PROMPT.md",
    "EXTRA_PROMPT": "EXTRA_PROMPT.md",
}

DISCLOSURE = (
    "本工作区全局共享：所有平台、所有群、所有私聊共享同一份文件，"
    "以 Agent 身份从事管理操作的人也能看到它们；"
    "不要把密钥、口令或第三方隐私写进去。"
)

SNAPSHOT_REFRESH_HINT = "换新快照请用 /new（/reset 只清历史，不刷新快照）。"


def format_mtime(timestamp: float) -> str:
    """Format a POSIX timestamp as local ISO 8601."""
    return datetime.fromtimestamp(timestamp).astimezone().isoformat(timespec="seconds")


def is_group_message(event: Any) -> bool:
    """Best-effort private/group detection for read-only output."""
    try:
        return not bool(event.is_private_chat())
    except Exception:  # pragma: no cover - defensive
        return False


def memory_summary(root: Path | None) -> dict[str, Any]:
    """Cheap, read-only digest of SOUL.md / MEMORY.md for chat output.

    Counts non-placeholder "- " lines under "## " sections, which works for the
    seeded template and for hand-written memory alike.
    """
    summary: dict[str, Any] = {
        "memory_exists": False,
        "memory_entries": 0,
        "memory_chars": 0,
        "memory_mtime": None,
        "soul_exists": False,
        "soul_chars": 0,
    }
    if root is None or not root.is_dir():
        return summary
    for name, prefix in (("MEMORY.md", "memory"), ("SOUL.md", "soul")):
        path = root / name
        try:
            if not path.is_file() or path.is_symlink():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            stat = path.stat()
        except OSError:
            continue
        summary[f"{prefix}_exists"] = True
        summary[f"{prefix}_chars"] = len(text)
        if prefix == "memory":
            summary["memory_mtime"] = stat.st_mtime
            summary["memory_entries"] = _count_memory_entries(text)
    return summary


def _count_memory_entries(text: str) -> int:
    count = 0
    in_section = False
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("## "):
            in_section = True
            continue
        if not in_section or not line.startswith("- "):
            continue
        body = line[2:].strip()
        if not body or body in ("（暂无）", "（还没有）"):
            continue
        count += 1
    return count


def _format_chars(chars: int) -> str:
    if chars >= 1000:
        return f"{chars / 1000:.1f}k 字"
    return f"{chars} 字"


def _format_short_time(timestamp: float | None) -> str:
    if not timestamp:
        return "未知时间"
    return datetime.fromtimestamp(timestamp).astimezone().strftime("%m-%d %H:%M")


def _memory_overview(summary: dict[str, Any]) -> str:
    soul = (
        _format_chars(int(summary.get("soul_chars") or 0))
        if summary.get("soul_exists")
        else "未创建"
    )
    if not summary.get("memory_exists"):
        return f"SOUL {soul} / MEMORY 还没有（插件加载时会播种）"
    entries = int(summary.get("memory_entries") or 0)
    chars = _format_chars(int(summary.get("memory_chars") or 0))
    updated = _format_short_time(summary.get("memory_mtime"))
    if entries:
        head = f"SOUL {soul} / MEMORY {entries} 条（{chars}）"
    else:
        head = f"SOUL {soul} / MEMORY 还是空的（{chars}）"
    return f"{head}，最近 {updated} 更新"


_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def _has_cjk(text: str) -> bool:
    return bool(_CJK_RE.search(text or ""))


def build_warnings(
    plugin: Any,
    root: Path | None,
    status: dict[str, Any],
    version_state: dict[str, Any] | None = None,
) -> list[str]:
    """Collect actionable warnings for the state payload (001 FR-7 / FR-11)."""
    if not plugin.is_installed():
        return []
    warnings: list[str] = []
    if not plugin.options["patch_webchat_projects"]:
        warnings.append(
            "patch_webchat_projects=false：Dashboard 文件面板与 Agent 的 cwd 不一致（按配置预期）",
        )
    consumers = status.get("consumers") or {}
    empty = [name for name, modules in consumers.items() if not modules]
    if empty:
        warnings.append(
            "consumers 为空："
            + ", ".join(empty)
            + " —— 补丁可能未生效，请检查 AstrBot 版本",
        )
    if root is None or not root.is_dir():
        warnings.append("共享根不存在或不是目录，请检查 shared_root 配置")
    runtime = plugin.runtime()
    if runtime == "sandbox":
        warnings.append(
            "当前是沙箱运行时：Agent 在容器里干活，不读也不写这个共享根",
        )
    elif runtime != "local":
        warnings.append(
            "Computer Use 运行时不是「本机环境」：Bot 现在没有文件工具，"
            "记忆只能读、写不进去。到 配置 → Agent → Computer Use 运行时 "
            "改成本机环境，再重载插件",
        )
    if root is not None:
        quota = store_mod.quota_state(root, plugin.options["quota_gb"])
        if quota["warn"]:
            warnings.append(
                f"共享根体积 {quota['size_gb']} GB 超过阈值 {quota['limit_gb']} GB（只告警，不删除文件）",
            )
    if version_state and root is not None and root.is_dir():
        if version_state.get("enabled", True) and version_state.get("available") is False:
            warnings.append(
                "没找到 git：记忆版本控制不可用（/astral commit、/astral recover 会失败）；"
                "装好 Git 后重载插件，或在插件配置里填 version_control.git_path",
            )
        elif (
            version_state.get("enabled", True)
            and version_state.get("initialized") is False
            and version_state.get("reason")
        ):
            warnings.append(f"记忆版本仓库不可用：{version_state['reason']}")
    if plugin.managed_state() == "broken":
        warnings.append(
            "EXTRA_PROMPT.md 的 astral:managed 标记异常，Astral 已停止自动改写该区段",
        )
    if not plugin.memory_options()["enabled"]:
        warnings.append("记忆快照注入已关闭（memory.enabled=false）")
    return warnings


def _version_lines(state: dict[str, Any]) -> list[str]:
    """Render the version status lines for '/astral status'."""
    if not state:
        return []
    if not state.get("enabled", True):
        return ["- 记忆版本：已关闭"]
    if state.get("available") is False:
        reason = state.get("reason") or "没找到 git"
        return [f"- 记忆版本：不可用（{reason}）"]
    if not state.get("initialized"):
        reason = state.get("reason")
        if reason:
            return [f"- 记忆版本：还没初始化（{reason}）"]
        return ["- 记忆版本：还没初始化；下次重载插件会自动建仓"]
    head = state.get("head")
    if not head:
        return ["- 记忆版本：还没有检查点；发 /astral commit 保存一次"]
    count = int(state.get("count") or 0)
    last = state.get("last") or {}
    if last:
        when = _format_short_time(last.get("timestamp"))
        line = f"- 记忆版本：{count} 个检查点，最新 {head}（{when}）"
    else:
        line = f"- 记忆版本：{count} 个检查点，最新 {head}"
    if state.get("dirty"):
        line += "，有未提交改动"
    return [line, "- 保存 / 回退：/astral commit · /astral recover"]


def build_state(plugin: Any) -> dict[str, Any]:
    """Build the payload shared by the state API and '/astral status'."""
    status = ws_shared.status()
    root = plugin.shared_root()
    if root is None and status.get("root"):
        root = Path(str(status["root"]))
    installed = bool(status.get("installed")) and plugin.is_installed()
    disk = (
        store_mod.dir_stats(root)
        if root is not None
        else {"exists": False, "files": 0, "size_mb": 0.0, "bytes": 0}
    )
    quota = (
        store_mod.quota_state(root, plugin.options["quota_gb"])
        if root is not None
        else {"limit_gb": plugin.options["quota_gb"], "size_gb": 0.0, "warn": False}
    )
    version_options = (
        plugin.options.get("version_control")
        if isinstance(plugin.options, dict)
        else None
    )
    version_options = version_options if isinstance(version_options, dict) else {}
    version_enabled = bool(version_options.get("enabled", True))
    version_git_path = version_options.get("git_path")
    if not isinstance(version_git_path, str):
        version_git_path = ""
    if root is not None:
        try:
            version_state = versioning.summary(
                root,
                git_path=version_git_path,
                enabled=version_enabled,
            )
        except Exception:  # pragma: no cover - defensive
            logger.exception("astral: versioning summary failed")
            version_state = {
                "enabled": version_enabled,
                "available": False,
                "initialized": False,
                "reason": "读取版本状态失败",
            }
    else:
        version_state = {
            "enabled": version_enabled,
            "available": None,
            "initialized": False,
            "reason": "共享根不可用",
        }
    return {
        "installed": installed,
        "shared_root": str(root) if root is not None else None,
        "baseline": status.get("baseline") or dict(ws_shared.BASELINE),
        "runtime": plugin.runtime(),
        "consumers": status.get("consumers") or {},
        "patched": status.get("patched") or [],
        "patch_webchat_projects": plugin.options["patch_webchat_projects"],
        "installed_at": status.get("installed_at"),
        "reason": None if installed else (plugin.install_reason() or status.get("reason")),
        "disk": {
            "exists": bool(disk.get("exists")),
            "files": int(disk.get("files") or 0),
            "size_mb": float(disk.get("size_mb") or 0.0),
        },
        "quota": quota,
        "memory": dict(plugin.memory_options(), managed=plugin.managed_state()),
        "memory_summary": memory_summary(root),
        "versioning": version_state,
        "warnings": build_warnings(plugin, root, status, version_state),
    }


def status_text(payload: dict[str, Any]) -> str:
    """Render the human readable '/astral status' answer.

    Chat output carries only what a group member can act on: whether it works,
    where the memory lives, how much it remembers and what to look at next.
    Patch internals (consumers, baseline commit) stay in the state payload and
    the plugin page.
    """
    root = payload.get("shared_root")
    summary = payload.get("memory_summary") or {}
    if payload.get("installed"):
        lines = [
            "Astral 已生效 ✅",
            f"- 记忆目录：{root or '（未知）'}",
            f"- 记忆概况：{_memory_overview(summary)}",
            *_version_lines(payload.get("versioning") or {}),
            "- 看它记得什么：/astral memory show",
            "- 全局共享：所有平台、所有群共用这一份，别写密钥和别人的隐私。",
        ]
    else:
        reason = str(payload.get("reason") or "未知")
        lines = ["Astral 未生效 ❌"]
        if _has_cjk(reason):
            lines.append(f"- 原因：{reason}")
        else:
            lines.append(
                "- 结论：插件没装上。最常见的是 AstrBot 版本对不上，或共享根不可写。"
            )
            lines.append(f"- 原始信息：{reason}")
        lines.append("- 下一步：重载插件后再发一次 /astral status；还是不行就看插件页的提示。")
        if root:
            lines.append(f"- 记忆目录：{root}")
    for warning in payload.get("warnings") or []:
        lines.append(f"- 注意：{warning}")
    return "\n".join(lines)


def memory_show_text(plugin: Any, event: Any, target: str) -> str:
    """Render '/astral memory show [soul|memory|agents|extra]' (002 FR-6)."""
    root = plugin.shared_root()
    if root is None or not root.is_dir():
        return "Astral 未生效：共享根不可用。"
    name = (target or "memory").strip().upper()
    relative = MEMORY_FILE_BY_ALIAS.get(name)
    if relative is None:
        return "未知文件；可选：soul / memory / agents / extra。"
    path = root / relative
    try:
        text = (
            path.read_text(encoding="utf-8", errors="replace") if path.is_file() else None
        )
    except OSError:
        text = None
    if text is None:
        return f"{relative} 不存在：{path}"
    try:
        stat = path.stat()
    except OSError:
        return f"{relative} 读取失败：{path}"
    header = (
        f"{relative} —— {_format_chars(len(text))}，"
        f"更新于 {format_mtime(stat.st_mtime)}"
    )
    if relative == "MEMORY.md":
        header += f"，{_count_memory_entries(text)} 条记忆"
    if is_group_message(event):
        head = "\n".join(text.splitlines()[:3])
        return (
            f"{header}\n"
            "群聊里只显示这些和前三行；要看全文请私聊我，或到 WebUI 插件页。\n"
            f"{DISCLOSURE}\n---\n{head}\n{SNAPSHOT_REFRESH_HINT}"
        )
    if len(text) > MEMORY_SHOW_PRIVATE_LIMIT:
        return (
            f"{header}\n---\n{text[:MEMORY_SHOW_PRIVATE_LIMIT]}\n"
            f"...（已截断，全文见 {path}）\n{SNAPSHOT_REFRESH_HINT}"
        )
    return f"{header}\n---\n{text}\n{SNAPSHOT_REFRESH_HINT}"


def _join_names(names: list[Any], limit: int = 6) -> str:
    values = [str(name) for name in names if str(name).strip()]
    if not values:
        return "（无文件变化）"
    if len(values) <= limit:
        return "、".join(values)
    return "、".join(values[:limit]) + f" 等 {len(values)} 个"


def version_commit_text(result: dict[str, Any]) -> str:
    """Render '/astral commit' and '/astral save' answers."""
    status = str(result.get("status") or "")
    if status == "no_changes":
        head = result.get("hash")
        if head:
            return f"没有新改动，当前最新版本是 {head}。"
        return "没有可保存的内容。"
    if status != "committed":
        return f"保存失败：{result.get('reason') or '未知原因'}"
    head = result.get("hash") or "（未知）"
    files = _join_names(list(result.get("files") or []))
    return (
        "已保存记忆 ✅\n"
        f"- 版本：{head}\n"
        f"- 文件：{files}\n"
        f"- 回退：/astral recover {head}"
    )


def version_list_text(result: dict[str, Any]) -> str:
    """Render '/astral recover' without a hash."""
    status = str(result.get("status") or "")
    if status == "unavailable":
        return f"记忆版本控制不可用：{result.get('reason') or '没找到 git'}"
    versions = list(result.get("versions") or [])
    if status != "ok" or not versions:
        return "还没有记忆版本。发 /astral commit 保存一个。"
    lines = ["最近的记忆版本："]
    for item in versions:
        short = item.get("hash") or "（未知）"
        when = _format_short_time(item.get("timestamp"))
        count = len(item.get("files") or [])
        suffix = f"{count} 个文件" if count else "无文件变化"
        lines.append(f"- {short} · {when} · {suffix}")
    lines.append("回退：/astral recover <短哈希>（回退前会自动保存当前状态）")
    return "\n".join(lines)


def version_recover_text(result: dict[str, Any]) -> str:
    """Render '/astral recover <short-hash>' answers."""
    status = str(result.get("status") or "")
    target = result.get("target") or "（未知）"
    if status == "no_changes":
        return f"当前已经是 {target}，不需要回退。"
    if status == "recovered_uncommitted":
        error = result.get("error") or "未知原因"
        return (
            f"记忆文件已回退到 {target}，但没能记成新版本：{error}\n"
            "当前文件已经是回退后的内容；请发 /astral commit 保存，或检查 Git 状态。"
        )
    if status != "recovered":
        return f"回退失败：{result.get('reason') or '未知原因'}"
    lines = [f"已回退到 {target} ✅"]
    before = result.get("before")
    if before and before != target:
        lines.append(f"- 回退前已保存当前状态：{before}")
    lines.append(f"- 现在的最新版本：{result.get('hash') or '（未知）'}")
    files = list(result.get("files") or [])
    if files:
        lines.append(f"- 改动文件：{_join_names(files)}")
    lines.append("下一轮对话就会用回退后的记忆。")
    return "\n".join(lines)
