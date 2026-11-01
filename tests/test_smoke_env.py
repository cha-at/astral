from __future__ import annotations

import config  # noqa: F401  (smoke: plugin dir is importable)


def test_astrbot_importable():
    import astrbot.core.workspace as ws

    assert callable(ws.resolve_workspace_root_for_umo)
    assert callable(ws.resolve_project_workspace_root)
    assert callable(ws.default_workspace_root)
    assert callable(ws.project_workspace_root)
