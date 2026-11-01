"""State payload and user visible status rendering.

Kept out of main.py so the plugin entry point stays a thin assembly file.
Everything here is read-only: it inspects the plugin object, the patch status
and the shared root, then returns plain data or Chinese text.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

if __package__:
    from . import store as store_mod
    from . import ws_shared
else:  # pragma: no cover - direct import for tests
    import store as store_mod
    import ws_shared

logger = logging.getLogger("astrbot")

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


def build_warnings(plugin: Any, root: Path | None, status: dict[str, Any]) -> list[str]:
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
    if plugin.runtime() == "sandbox":
        warnings.append("当前为沙箱运行时，工作区不参与 Agent 执行")
    if root is not None:
        quota = store_mod.quota_state(root, plugin.options["quota_gb"])
        if quota["warn"]:
            warnings.append(
                f"共享根体积 {quota['size_gb']} GB 超过阈值 {quota['limit_gb']} GB（只告警，不删除文件）",
            )
    if plugin.managed_state() == "broken":
        warnings.append(
            "EXTRA_PROMPT.md 的 astral:managed 标记异常，Astral 已停止自动改写该区段",
        )
    if not plugin.memory_options()["enabled"]:
        warnings.append("记忆快照注入已关闭（memory.enabled=false）")
    return warnings


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
        "warnings": build_warnings(plugin, root, status),
    }


def status_text(payload: dict[str, Any]) -> str:
    """Render the human readable '/astral status' answer."""
    consumers = payload.get("consumers") or {}
    total = sum(len(modules) for modules in consumers.values())
    baseline = payload.get("baseline") or {}
    lines = [
        "Astral 状态",
        f"- 共享根：{payload.get('shared_root') or '（未知）'}",
        f"- 安装状态：{'已生效' if payload.get('installed') else '未生效'}",
        f"- consumers：{total} 个引用（覆盖 {len(consumers)} 个函数）",
        f"- 运行时：{payload.get('runtime')}",
        f"- 版本基线：AstrBot {baseline.get('astrbot')} @ {baseline.get('commit')}",
    ]
    if payload.get("reason"):
        lines.append(f"- 失败原因：{payload['reason']}")
    for warning in payload.get("warnings") or []:
        lines.append(f"- 注意：{warning}")
    lines.append(f"- 隐私：{DISCLOSURE}")
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
        f"{relative} —— {len(text)} 字符 / {stat.st_size} 字节 / "
        f"更新于 {format_mtime(stat.st_mtime)}"
    )
    if is_group_message(event):
        head = "\n".join(text.splitlines()[:3])
        return (
            f"{header}\n"
            "群聊只显示元信息与前 3 行；完整内容请私聊 Bot 或到 WebUI 插件页查看。\n"
            f"{DISCLOSURE}\n---\n{head}\n{SNAPSHOT_REFRESH_HINT}"
        )
    if len(text) > MEMORY_SHOW_PRIVATE_LIMIT:
        return (
            f"{header}\n---\n{text[:MEMORY_SHOW_PRIVATE_LIMIT]}\n"
            f"...（已截断，全文见 {path}）\n{SNAPSHOT_REFRESH_HINT}"
        )
    return f"{header}\n---\n{text}\n{SNAPSHOT_REFRESH_HINT}"
