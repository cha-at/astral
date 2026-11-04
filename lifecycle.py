"""Lifecycle orchestration for the Astral plugin.

Owns the install / fail-loud / seed / restore sequence so main.py stays a thin
entry point (001 plan section 1 keeps main.py under 250 lines). Every function
here takes the plugin instance and mutates its documented attributes only.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from astrbot.api import logger

if __package__:
    from . import config as config_mod
    from . import state as state_mod
    from . import store as store_mod
    from . import templates as templates_mod
    from . import versioning
    from . import ws_shared
else:  # pragma: no cover - direct import for tests
    import config as config_mod
    import state as state_mod
    import store as store_mod
    import templates as templates_mod
    import versioning
    import ws_shared


def install_patch(plugin: Any) -> None:
    """Resolve the shared root, build the skeleton and install the patch."""
    try:
        root = config_mod.get_shared_root(plugin.plugin_config)
        created = config_mod.ensure_shared_tree(root)
    except config_mod.ConfigError as exc:
        fail(plugin, exc.reason)
        return

    plugin._root = root
    first_run = not (store_mod.astral_dir(root) / store_mod.AUDIT_FILE).exists()
    report = ws_shared.install(root, patch_project=plugin.options["patch_webchat_projects"])
    if not report.get("installed"):
        fail(plugin, report.get("reason") or "patch not installed")
        return

    plugin._installed = True
    plugin._reason = None
    store_mod.append_audit(
        root,
        "install",
        {
            "root": str(root),
            "created": created,
            "patched": report.get("patched") or [],
            "rebound": len(report.get("rebound") or []),
            "skipped": report.get("skipped") or [],
        },
    )
    refresh_state_file(plugin)
    logger.info(
        "astral: shared workspace ready root=%s created=%d rebound=%d",
        root,
        len(created),
        len(report.get("rebound") or []),
    )
    if first_run:
        logger.warning("astral: 首次启用共享工作区 —— %s", state_mod.DISCLOSURE)


def fail(plugin: Any, reason: str) -> None:
    """Mark the plugin as not installed, restore any patch and log loudly."""
    plugin._installed = False
    plugin._reason = reason
    logger.error("astral: install failed reason=%s", reason)
    try:
        ws_shared.uninstall()
    except Exception:  # pragma: no cover - defensive
        logger.exception("astral: uninstall during failure handling failed")
    if plugin._root is not None:
        store_mod.append_audit(plugin._root, "install_failed", {"reason": reason})


def state_file_payload(plugin: Any) -> dict[str, Any]:
    return {
        "installed": plugin.is_installed(),
        "shared_root": plugin.shared_root_text(),
        "baseline": dict(ws_shared.BASELINE),
        "installed_at": store_mod.iso_now(),
        "patch_webchat_projects": plugin.options["patch_webchat_projects"],
        "quota_gb": plugin.options["quota_gb"],
        "memory": dict(plugin.options["memory"]),
        "version_control": dict(plugin.options.get("version_control") or {}),
    }


def refresh_state_file(plugin: Any) -> None:
    root = plugin.shared_root()
    if root is None:
        return
    try:
        store_mod.write_state(root, state_file_payload(plugin))
    except OSError as exc:
        logger.warning("astral: state.json write failed (%s)", exc)


def seed(plugin: Any) -> None:
    """Seed the four content files and sync the managed prompt section (002 FR-2)."""
    root = plugin.shared_root()
    if root is None or not root.is_dir():
        logger.warning("astral: shared root missing, seeding skipped: %s", root)
        return
    try:
        seeded = templates_mod.seed_workspace(root)
        managed = templates_mod.sync_managed_section(root)
    except Exception:
        logger.exception("astral: seeding failed")
        return
    plugin._managed_state = managed
    if seeded:
        store_mod.append_audit(root, "seed", {"files": seeded})
    if managed in {"updated", "appended", "created"}:
        store_mod.append_audit(root, "managed_sync", {"state": managed})
    if managed == "broken":
        logger.warning(
            "astral: managed section markers are broken, file left untouched: %s",
            root / "EXTRA_PROMPT.md",
        )
    logger.info("astral: seed=%s managed=%s", seeded, managed)
    version_options = plugin.options.get("version_control") or {}
    if isinstance(version_options, dict) and version_options.get("enabled", True):
        git_path = version_options.get("git_path")
        try:
            report = versioning.ensure_repo(
                root,
                git_path=git_path if isinstance(git_path, str) else "",
            )
        except Exception:
            logger.exception("astral: version repository init failed")
        else:
            if report.get("created"):
                store_mod.append_audit(
                    root,
                    "version_init",
                    {
                        "owned": report.get("owned"),
                        "baseline": report.get("baseline"),
                    },
                )
            if not report.get("ok"):
                logger.warning(
                    "astral: version repository unavailable: %s",
                    report.get("reason"),
                )
    refresh_state_file(plugin)


def terminate(plugin: Any) -> None:
    """Restore every patched module attribute (001 FR-5)."""
    report = ws_shared.uninstall()
    plugin._installed = False
    root: Path | None = plugin.shared_root()
    if root is not None:
        store_mod.append_audit(
            root,
            "uninstall",
            {"restored": len(report.get("restored") or [])},
        )
    logger.info("astral: terminated")


def reapply(plugin: Any) -> None:
    """Re-run the patch once the whole instance finished loading (idempotent)."""
    root = plugin.shared_root()
    if root is None:
        return
    report = ws_shared.install(root, patch_project=plugin.options["patch_webchat_projects"])
    if not report.get("installed"):
        fail(plugin, report.get("reason") or "patch not installed")
        return
    plugin._installed = True
    plugin._reason = None
    logger.info("astral: patch re-applied after AstrBot loaded")
