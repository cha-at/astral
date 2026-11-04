"""Astral: one workspace, one memory.

Plugin entry point. Configuration lives in config.py, the monkeypatch in
ws_shared.py, the install/seed/restore sequence in lifecycle.py, memory
rendering in snapshot.py, state rendering in state.py and the HTTP handlers in
webapi.py; this module only declares the Star subclass, its commands and the
LLM request hook.
"""

from __future__ import annotations

import functools
import hashlib
from pathlib import Path
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

if __package__:
    from . import commands as commands_mod
    from . import config as config_mod
    from . import lifecycle
    from . import snapshot as snapshot_mod
    from . import state as state_mod
    from . import store as store_mod
    from . import webapi as webapi_mod
    from . import ws_shared
else:  # pragma: no cover - direct import for tests
    import commands as commands_mod
    import config as config_mod
    import lifecycle
    import snapshot as snapshot_mod
    import state as state_mod
    import store as store_mod
    import webapi as webapi_mod
    import ws_shared


class AstralPlugin(Star):
    """AstrBot plugin that pins every session to one shared workspace root."""

    def __init__(self, context: Context, config: dict | None = None) -> None:
        super().__init__(context, config)
        self.plugin_config: dict[str, Any] = config if isinstance(config, dict) else {}
        self.options = config_mod.read_options(self.plugin_config)
        self._root: Path | None = None
        self._installed = False
        self._reason: str | None = None
        self._managed_state = "unknown"
        self._cache = snapshot_mod.SnapshotCache(maxsize=64)
        lifecycle.install_patch(self)
        self._register_web_apis()

    # ------------------------------------------------------------------
    # accessors consumed by lifecycle.py, state.py and webapi.py
    # ------------------------------------------------------------------
    def shared_root(self) -> Path | None:
        return self._root

    def shared_root_text(self) -> str | None:
        return str(self._root) if self._root is not None else None

    def is_installed(self) -> bool:
        return self._installed

    def install_reason(self) -> str | None:
        return None if self._installed else self._reason

    def memory_options(self) -> dict[str, Any]:
        return self.options["memory"]

    def managed_state(self) -> str:
        return self._managed_state

    def clear_snapshot_cache(self) -> None:
        """Drop every frozen memory snapshot so the next request re-renders."""
        self._cache.clear()

    def runtime(self) -> str:
        try:
            config = self.context.get_config()
            settings = config.get("provider_settings", {}) if config else {}
            value = settings.get("computer_use_runtime", "none")
        except Exception:  # pragma: no cover - defensive
            value = "none"
        return str(value or "none")

    def state_payload(self) -> dict[str, Any]:
        return state_mod.build_state(self)

    def clean_archive(self, days: float) -> dict[str, Any]:
        root = self._root
        if root is None:
            return {"moved": 0, "dest": None, "files": []}
        result = store_mod.archive_move(root, days)
        store_mod.append_audit(root, "clean", {"days": days, **result})
        return result

    # ------------------------------------------------------------------
    # AstrBot lifecycle hooks
    # ------------------------------------------------------------------
    async def initialize(self) -> None:
        """Seed the workspace files and sync the managed prompt section."""
        lifecycle.seed(self)

    async def terminate(self) -> None:
        """Restore every patched module attribute and drop the snapshot cache."""
        lifecycle.terminate(self)
        self.clear_snapshot_cache()

    @filter.on_astrbot_loaded()
    async def on_astrbot_loaded(self) -> None:
        """Re-apply the patch once the whole instance finished loading."""
        lifecycle.reapply(self)

    # ------------------------------------------------------------------
    # HTTP endpoints (001 FR-7 / FR-10, 002 FR-6)
    # ------------------------------------------------------------------
    def _register_web_apis(self) -> None:
        apis = (
            (webapi_mod.ROUTE_STATE, webapi_mod.state_handler, ["GET"], "Astral patch state"),
            (
                webapi_mod.ROUTE_FILES,
                webapi_mod.files_handler,
                ["GET"],
                "Astral read-only memory preview",
            ),
            (
                webapi_mod.ROUTE_CLEAN,
                webapi_mod.clean_handler,
                ["POST"],
                "Astral archive clean",
            ),
        )
        for route, handler, methods, description in apis:
            try:
                self.context.register_web_api(
                    route,
                    functools.partial(handler, self),
                    methods,
                    description,
                )
            except Exception as exc:  # pragma: no cover - defensive
                logger.error(
                    "astral: web api registration failed route=%s (%s)",
                    route,
                    exc,
                )

    # ------------------------------------------------------------------
    # memory snapshot injection (002 FR-3)
    # ------------------------------------------------------------------
    async def _snapshot_key(self, event: AstrMessageEvent, req: Any) -> tuple[str, str]:
        umo = str(getattr(event, "unified_msg_origin", "") or "")
        cid = getattr(getattr(req, "conversation", None), "cid", None)
        if not cid:
            try:
                manager = getattr(self.context, "conversation_manager", None)
                if manager is not None:
                    cid = await manager.get_curr_conversation_id(umo)
            except Exception:  # pragma: no cover - defensive
                cid = None
        return umo, str(cid) if cid else "no-conv"

    @filter.on_llm_request()
    async def inject_memory_snapshot(self, event: AstrMessageEvent, req: Any) -> None:
        """Append the frozen memory snapshot to the system prompt."""
        options = self.memory_options()
        root = self._root
        if not options["enabled"] or root is None or not root.is_dir():
            return
        try:
            _umo, cid = await self._snapshot_key(event, req)

            def render() -> str:
                return snapshot_mod.render(
                    root,
                    soul_budget=options["soul_budget_chars"],
                    memory_budget=options["memory_budget_chars"],
                    soft_limit=options["soft_limit_chars"],
                )

            block, hit = self._cache.get_or_render(f"{_umo}:{cid}", render)
            existing = getattr(req, "system_prompt", "") or ""
            req.system_prompt = f"{existing}\n{block}"
            logger.debug(
                "astral: snapshot conv=%s sha=%s chars=%d hit=%s",
                cid[:8],
                hashlib.sha256(block.encode("utf-8")).hexdigest()[:8],
                len(block),
                hit,
            )
        except Exception:
            logger.exception("astral: snapshot injection failed")
            try:
                existing = getattr(req, "system_prompt", "") or ""
                req.system_prompt = f"{existing}\n{snapshot_mod.DEGRADED_BLOCK}"
            except Exception:  # pragma: no cover - defensive
                pass

    # ------------------------------------------------------------------
    # commands (001 FR-7 / FR-11, 002 FR-6)
    # ------------------------------------------------------------------
    @filter.command_group("astral")
    async def astral(self, event: AstrMessageEvent):
        """Astral 指令组。"""
        yield event.plain_result(commands_mod.help_text())

    @astral.command("status")
    async def astral_status(self, event: AstrMessageEvent):
        """查看 Astral 共享工作区状态。"""
        yield event.plain_result(state_mod.status_text(self.state_payload()))

    @astral.command("clean")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def astral_clean(self, event: AstrMessageEvent, days: float = 30.0):
        """把共享根下超期的散落文件移动到 archive/。"""
        if self._root is None:
            yield event.plain_result("Astral 未生效，无法清理。")
            return
        result = self.clean_archive(days)
        if not result.get("moved"):
            yield event.plain_result(f"没有需要归档的文件（阈值 {days:g} 天）。")
            return
        yield event.plain_result(
            f"已把 {result['moved']} 个文件挪到 {result['dest']}（只移动不删除）。\n"
            f"要还原就把那个目录里的文件拷回共享根。",
        )

    @astral.command("commit", alias={"save"})
    async def astral_commit(self, event: AstrMessageEvent):
        """把当前记忆文件存成一个版本。"""
        yield event.plain_result(await commands_mod.save_memory(self))

    @astral.command("recover")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def astral_recover(self, event: AstrMessageEvent, target: str = ""):
        """查看记忆版本；带短哈希时回退到该版本。"""
        yield event.plain_result(await commands_mod.recover_memory(self, target))

    @astral.group("memory")
    async def astral_memory(self, event: AstrMessageEvent):
        """记忆文件只读查看指令组。"""
        yield event.plain_result("用法：/astral memory show [soul|memory|agents|extra]")

    @astral_memory.command("show")
    async def astral_memory_show(self, event: AstrMessageEvent, target: str = ""):
        """只读查看 SOUL/MEMORY/AGENTS/EXTRA 文件。"""
        yield event.plain_result(state_mod.memory_show_text(self, event, target))
