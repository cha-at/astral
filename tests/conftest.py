"""Shared pytest setup for the Astral plugin test suite.

The plugin directory is added to sys.path so the standalone modules (config,
ws_shared, store, templates, snapshot, state, webapi) can be imported directly
without packaging them, and ASTRBOT_ROOT is pointed at a throwaway directory so
tests never touch the real AstrBot data directory.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

_TEST_ROOT = Path(tempfile.gettempdir()) / "astral-testroot"
if not os.environ.get("ASTRBOT_ROOT"):
    _TEST_ROOT.mkdir(parents=True, exist_ok=True)
    os.environ["ASTRBOT_ROOT"] = str(_TEST_ROOT)
