"""
Lightweight JSONL file-based IPC.
Writer: ipc_write() — appends one JSON line, msvcrt-locked on Windows.
Reader: ipc_read_and_clear() — reads all lines then truncates.
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

logger = logging.getLogger(__name__)

_LOCK_TIMEOUT = 2.0   # seconds to wait for lock
_LOCK_STALE   = 10.0  # seconds before a lock file is considered stale


def _lock_path(cmd_file: str) -> str:
    return cmd_file + ".lock"


def _acquire(lock_file: str) -> bool:
    deadline = time.monotonic() + _LOCK_TIMEOUT
    while time.monotonic() < deadline:
        # Stale lock cleanup
        if os.path.exists(lock_file):
            try:
                age = time.time() - os.path.getmtime(lock_file)
                if age > _LOCK_STALE:
                    os.remove(lock_file)
            except OSError:
                pass
        try:
            fd = os.open(lock_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            return True
        except FileExistsError:
            time.sleep(0.02)
    return False


def _release(lock_file: str) -> None:
    try:
        os.remove(lock_file)
    except OSError:
        pass


def ipc_write(cmd_file: str, payload: dict[str, Any]) -> bool:
    lock_file = _lock_path(cmd_file)
    if not _acquire(lock_file):
        logger.warning("IPC: could not acquire lock for %s", cmd_file)
        return False
    try:
        with open(cmd_file, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload) + "\n")
        return True
    except OSError as exc:
        logger.error("IPC write error: %s", exc)
        return False
    finally:
        _release(lock_file)


def ipc_read_and_clear(cmd_file: str) -> list[dict[str, Any]]:
    lock_file = _lock_path(cmd_file)
    if not _acquire(lock_file):
        return []
    try:
        if not os.path.exists(cmd_file):
            return []
        with open(cmd_file, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
        with open(cmd_file, "w", encoding="utf-8"):  # truncate
            pass
        results = []
        for line in lines:
            line = line.strip()
            if line:
                try:
                    results.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        return results
    except OSError as exc:
        logger.error("IPC read error: %s", exc)
        return []
    finally:
        _release(lock_file)
