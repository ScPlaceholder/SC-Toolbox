"""
Windows-specific platform utilities shared across tools.
"""
import ctypes
import logging
import sys
import zlib

log = logging.getLogger(__name__)


def set_dpi_awareness() -> None:
    """Set per-monitor DPI awareness, preferring V2.  No-op on non-Windows or failure."""
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(-4)  # V2
    except (OSError, AttributeError, ValueError):
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)  # V1
        except (OSError, AttributeError, ValueError):
            pass


def boost_responsiveness() -> None:
    """Boost the current process so it stays responsive under contention.

    Used by latency-sensitive overlays (e.g. Mouse Blocker) that must
    appear instantly when the user presses their hotkey, even when
    other tools are saturating CPU/memory (e.g. Mining Signals'
    continuous ONNX OCR pipeline allocating ~500 MB+).

    Two adjustments:

    * ``ABOVE_NORMAL_PRIORITY_CLASS`` so our threads are scheduled
      ahead of NORMAL-priority processes — prevents the Qt main
      thread and IPC poll thread from being starved.
    * ``MEMORY_PRIORITY_NORMAL`` (5) on the process — without this,
      idle hidden processes drift down to ``MEMORY_PRIORITY_VERY_LOW``
      and Windows aggressively trims their working set under memory
      pressure.  The first show then stalls on page-faults bringing
      rendering code back from disk, which is exactly what we see
      when Mining Signals' OCR is hot.

    No-op on non-Windows or any failure.
    """
    if not sys.platform.startswith("win"):
        return
    try:
        kernel32 = ctypes.windll.kernel32
        # ABOVE_NORMAL_PRIORITY_CLASS = 0x00008000
        kernel32.SetPriorityClass(kernel32.GetCurrentProcess(), 0x00008000)
    except (OSError, AttributeError, ValueError) as exc:
        log.debug("boost_responsiveness: SetPriorityClass failed: %s", exc)

    try:
        # SetProcessInformation(ProcessMemoryPriority, MEMORY_PRIORITY_INFORMATION)
        # ProcessMemoryPriority = 39 (0x27)
        # MEMORY_PRIORITY_NORMAL = 5
        class _MEMORY_PRIORITY_INFORMATION(ctypes.Structure):
            _fields_ = [("MemoryPriority", ctypes.c_ulong)]

        mpi = _MEMORY_PRIORITY_INFORMATION()
        mpi.MemoryPriority = 5
        kernel32 = ctypes.windll.kernel32
        kernel32.SetProcessInformation(
            kernel32.GetCurrentProcess(),
            39,
            ctypes.byref(mpi),
            ctypes.sizeof(mpi),
        )
    except (OSError, AttributeError, ValueError) as exc:
        log.debug("boost_responsiveness: SetProcessInformation failed: %s", exc)


def deterministic_hotkey_id(hotkey_str: str) -> int:
    """Return a deterministic int ID for RegisterHotKey from a hotkey string.

    Uses CRC32 to map the string to the valid range [1, 0xBFFF].
    Used by mining_loadout_app.py for Win32 RegisterHotKey/UnregisterHotKey.
    """
    return (zlib.crc32(hotkey_str.encode()) & 0xBFFF) + 1

