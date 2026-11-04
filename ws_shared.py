"""Shared workspace patch core (001 FR-3 .. FR-7).

The module replaces four AstrBot workspace resolvers with wrappers that always
return one shared root. Two writes are required:

1. the source module attribute (astrbot.core.workspace.<name>);
2. every already imported consumer that holds the original callable in its own
   module globals (AstrBot consumers use top level 'from ... import').

Patch state lives on the astrbot.core.workspace module itself so that a plugin
reload cannot lose the original callables. This module never reads plugin
configuration and never creates directories.
"""

from __future__ import annotations

import importlib
import sys
from datetime import datetime
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

from astrbot.api import logger

BASELINE: dict[str, str] = {"astrbot": "4.29.4", "commit": "831abae85"}
TARGET_MODULE = "astrbot.core.workspace"
STATE_ATTR = "__astral_patch_state__"
ERROR_ATTR = "__astral_patch_error__"
WRAPPER_MARKER = "__astral_name__"

ALL_FUNCS: tuple[str, ...] = (
    "resolve_workspace_root_for_umo",
    "resolve_project_workspace_root",
    "default_workspace_root",
    "project_workspace_root",
)
PROJECT_FUNCS: tuple[str, ...] = (
    "resolve_project_workspace_root",
    "project_workspace_root",
)
TOP_FUNCS: tuple[str, ...] = (
    "resolve_workspace_root_for_umo",
    "default_workspace_root",
)


def _workspace_module() -> ModuleType:
    return importlib.import_module(TARGET_MODULE)


def _iso_now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _build_wrapper(name: str, root: Path) -> Callable[..., Any]:
    """Build a minimal wrapper with the exact baseline signature."""
    if name == "resolve_workspace_root_for_umo":

        async def wrapper(umo: str, db: Any = None) -> Path:
            return root

    elif name == "resolve_project_workspace_root":

        def wrapper(project: Any, *, fallback_umo: str) -> Path:
            return root

    elif name == "default_workspace_root":

        def wrapper(umo: str) -> Path:
            return root

    elif name == "project_workspace_root":

        def wrapper(project_id: str) -> Path:
            return root

    else:  # pragma: no cover - guarded by ALL_FUNCS
        raise ValueError(f"unknown patch target: {name}")

    wrapper.__name__ = name
    wrapper.__qualname__ = name
    wrapper.__doc__ = f"Astral wrapper: always returns the shared root ({name})."
    setattr(wrapper, WRAPPER_MARKER, name)
    setattr(wrapper, "__astral_root__", str(root))
    return wrapper


def _new_state(ws: ModuleType) -> dict[str, Any]:
    return {
        "originals": {name: getattr(ws, name) for name in ALL_FUNCS},
        "wrappers": {},
        "root": None,
        "installed_at": None,
        "patched": [],
        "patch_project": True,
        "baseline": dict(BASELINE),
    }


def _failure(reason: str) -> dict[str, Any]:
    return {
        "installed": False,
        "root": None,
        "patched": [],
        "rebound": [],
        "skipped": [],
        "reason": reason,
    }


def _record_error(reason: str) -> None:
    """Persist the last install failure on the core module (001 FR-6)."""
    try:
        setattr(_workspace_module(), ERROR_ATTR, reason)
    except Exception:  # pragma: no cover - defensive
        logger.debug("astral: cannot record install failure reason")


def _clear_error() -> None:
    try:
        delattr(_workspace_module(), ERROR_ATTR)
    except Exception:
        pass


def _last_error() -> str | None:
    try:
        value = getattr(_workspace_module(), ERROR_ATTR, None)
    except Exception:
        return None
    return str(value) if value else None


def _current_state(ws: ModuleType) -> dict[str, Any] | None:
    state = getattr(ws, STATE_ATTR, None)
    return state if isinstance(state, dict) else None


def _is_our_wrapper(value: Any, name: str) -> bool:
    return getattr(value, WRAPPER_MARKER, None) == name


def install(
    root: Path,
    *,
    patch_project: bool = True,
    scan_modules: bool = True,
) -> dict[str, Any]:
    """Install (or refresh) the shared workspace patch.

    Args:
        root: Shared workspace root the wrappers return.
        patch_project: Also take over the two project workspace resolvers
            (Dashboard file panel, 001 FR-9).
        scan_modules: Rebind already imported consumers. Kept as a switch so
            the negative control of AC-3.4 can be exercised in tests.

    Returns:
        Report dict: installed / root / patched / rebound / skipped / reason.
    """
    target_root = Path(root)
    try:
        ws = _workspace_module()
    except Exception as exc:
        reason = f"cannot import {TARGET_MODULE}: {exc.__class__.__name__}: {exc}"
        logger.error("astral: install failed reason=%s", reason)
        return _failure(reason)

    missing = [name for name in ALL_FUNCS if not callable(getattr(ws, name, None))]
    if missing:
        reason = f"missing AstrBot workspace symbols: {', '.join(missing)}"
        logger.error("astral: install failed reason=%s", reason)
        _record_error(reason)
        return _failure(reason)

    state = _current_state(ws)
    if state is None:
        state = _new_state(ws)
        setattr(ws, STATE_ATTR, state)
    originals: dict[str, Any] = state["originals"]

    patched_names = list(ALL_FUNCS) if patch_project else list(TOP_FUNCS)
    wrappers = {name: _build_wrapper(name, target_root) for name in patched_names}
    state["wrappers"] = wrappers
    state["root"] = str(target_root)
    state["patched"] = patched_names
    state["patch_project"] = bool(patch_project)
    state["baseline"] = dict(BASELINE)
    if not state.get("installed_at"):
        state["installed_at"] = _iso_now()

    rebound: list[str] = []
    skipped: list[str] = []

    # Write 1: the source module attributes.
    for name in ALL_FUNCS:
        value = wrappers.get(name, originals.get(name))
        if value is None:
            continue
        try:
            setattr(ws, name, value)
        except Exception as exc:  # pragma: no cover - core module is writable
            skipped.append(f"{ws.__name__}.{name} ({exc.__class__.__name__})")

    # Write 2: every module that already imported the callables.
    if scan_modules:
        for module_name, module in list(sys.modules.items()):
            if module is None or module is ws:
                continue
            for name in ALL_FUNCS:
                current = getattr(module, name, None)
                if current is None:
                    continue
                original = originals.get(name)
                if name in wrappers:
                    if current is original or _is_our_wrapper(current, name):
                        try:
                            setattr(module, name, wrappers[name])
                            rebound.append(f"{module_name}.{name}")
                        except Exception as exc:  # pragma: no cover
                            skipped.append(
                                f"{module_name}.{name} ({exc.__class__.__name__})"
                            )
                elif _is_our_wrapper(current, name) and original is not None:
                    try:
                        setattr(module, name, original)
                        rebound.append(f"{module_name}.{name}")
                    except Exception as exc:  # pragma: no cover
                        skipped.append(
                            f"{module_name}.{name} ({exc.__class__.__name__})"
                        )

    _clear_error()
    unique_rebound = sorted(set(rebound))
    unique_skipped = sorted(set(skipped))
    if unique_skipped:
        logger.warning(
            "astral: patch skipped %d attribute(s): %s",
            len(unique_skipped),
            ", ".join(unique_skipped[:10]),
        )
    logger.info(
        "astral: shared workspace installed root=%s rebound=%d",
        target_root,
        len(unique_rebound),
    )
    return {
        "installed": True,
        "root": str(target_root),
        "patched": patched_names,
        "rebound": unique_rebound,
        "skipped": unique_skipped,
        "reason": None,
    }


def uninstall() -> dict[str, Any]:
    """Restore every rewritten module attribute to the original callable."""
    try:
        ws = _workspace_module()
    except Exception as exc:
        return {
            "uninstalled": False,
            "restored": [],
            "reason": f"cannot import {TARGET_MODULE}: {exc.__class__.__name__}",
        }

    state = _current_state(ws)
    if state is None:
        return {"uninstalled": False, "restored": [], "reason": "not installed"}

    originals: dict[str, Any] = state.get("originals") or {}
    restored: list[str] = []

    for name in ALL_FUNCS:
        original = originals.get(name)
        if original is None:
            continue
        if getattr(ws, name, None) is not original:
            try:
                setattr(ws, name, original)
                restored.append(f"{ws.__name__}.{name}")
            except Exception as exc:  # pragma: no cover
                logger.warning("astral: restore failed for %s (%s)", name, exc)
        for module_name, module in list(sys.modules.items()):
            if module is None or module is ws:
                continue
            current = getattr(module, name, None)
            if current is None or current is original:
                continue
            if _is_our_wrapper(current, name):
                try:
                    setattr(module, name, original)
                    restored.append(f"{module_name}.{name}")
                except Exception as exc:  # pragma: no cover
                    logger.warning(
                        "astral: restore failed for %s.%s (%s)",
                        module_name,
                        name,
                        exc,
                    )

    try:
        delattr(ws, STATE_ATTR)
    except Exception:  # pragma: no cover
        pass
    _clear_error()

    unique = sorted(set(restored))
    logger.info("astral: patch uninstalled restored=%d", len(unique))
    return {"uninstalled": True, "restored": unique, "reason": None}


def status() -> dict[str, Any]:
    """Return the current patch state (001 plan section 3.6 / 6.1)."""
    base = {
        "installed": False,
        "root": None,
        "consumers": {},
        "baseline": dict(BASELINE),
        "installed_at": None,
        "patched": [],
        "patch_project": True,
        "reason": None,
    }
    try:
        ws = _workspace_module()
    except Exception as exc:
        base["reason"] = f"cannot import {TARGET_MODULE}: {exc.__class__.__name__}"
        return base

    state = _current_state(ws)
    if state is None:
        base["reason"] = _last_error() or "patch not installed"
        return base

    wrappers: dict[str, Any] = state.get("wrappers") or {}
    patched = [name for name in ALL_FUNCS if name in wrappers]
    healthy = all(getattr(ws, name, None) is wrappers[name] for name in patched)

    consumers: dict[str, list[str]] = {}
    for name in patched:
        holders: list[str] = []
        for module_name, module in list(sys.modules.items()):
            if module is None:
                continue
            if getattr(module, name, None) is wrappers[name]:
                holders.append(module_name)
        consumers[name] = sorted(set(holders))

    base.update(
        {
            "root": state.get("root"),
            "consumers": consumers,
            "installed_at": state.get("installed_at"),
            "patched": patched,
            "patch_project": bool(state.get("patch_project", True)),
            "baseline": dict(state.get("baseline") or BASELINE),
        }
    )
    if healthy:
        base["installed"] = True
    else:
        base["reason"] = (
            "patch is not active: AstrBot no longer exposes the Astral wrappers"
        )
    return base


def originals() -> dict[str, Any]:
    """Return the captured original callables (introspection / tests)."""
    try:
        ws = _workspace_module()
    except Exception:
        return {}
    state = _current_state(ws)
    if state is None:
        return {}
    return dict(state.get("originals") or {})
