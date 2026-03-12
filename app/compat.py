"""
Platform compatibility patches — import before any rq import.

Windows does not provide a 'fork' multiprocessing context. RQ 2.x calls
multiprocessing.get_context('fork') at scheduler import time, which raises
"cannot find context for 'fork'" on Windows.

Patch: redirect 'fork' -> 'spawn' on Windows.

Usage:
    import app.compat  # noqa: F401  — must appear before any rq import
"""
from __future__ import annotations

import multiprocessing
import sys

if sys.platform == "win32":
    _orig_get_context = multiprocessing.get_context

    def _win32_get_context(method=None):
        if method == "fork":
            method = "spawn"
        return _orig_get_context(method)

    multiprocessing.get_context = _win32_get_context  # type: ignore[assignment]
