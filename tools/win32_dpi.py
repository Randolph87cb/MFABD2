"""DPI-awareness helpers for Win32 automation scripts."""

from __future__ import annotations

import ctypes


def enable_dpi_awareness() -> None:
    """Ask Windows for physical pixels instead of DPI-virtualized coordinates."""
    user32 = ctypes.windll.user32
    shcore = ctypes.windll.shcore

    # Windows 10+: Per-monitor DPI aware v2.
    try:
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return
    except (AttributeError, OSError):
        pass

    # Windows 8.1 fallback: PROCESS_PER_MONITOR_DPI_AWARE.
    try:
        shcore.SetProcessDpiAwareness(2)
        return
    except (AttributeError, OSError):
        pass

    # Windows 7 fallback.
    try:
        user32.SetProcessDPIAware()
    except (AttributeError, OSError):
        pass
