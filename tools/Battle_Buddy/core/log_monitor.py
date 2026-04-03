"""
Tails Star Citizen's Game.log in real-time.
Calls registered line callbacks for each new line.
Handles file rotation/truncation gracefully.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Callable

logger = logging.getLogger(__name__)

_POLL_INTERVAL = 0.25   # seconds between read attempts
_REOPEN_DELAY  = 2.0    # seconds to wait before reopening after rotation


class LogMonitor:
    def __init__(self, log_path: str) -> None:
        self._path = log_path
        self._callbacks: list[Callable[[str], None]] = []
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._pos: int = 0

    def subscribe(self, callback: Callable[[str], None]) -> None:
        self._callbacks.append(callback)

    def start(self) -> None:
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="LogMonitor")
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=3.0)

    def _run(self) -> None:
        # Seek to end of existing file so we only see new lines
        self._pos = self._get_file_size()
        fh = None
        while not self._stop_event.is_set():
            try:
                if fh is None:
                    if not os.path.exists(self._path):
                        time.sleep(_REOPEN_DELAY)
                        continue
                    fh = open(self._path, "r", encoding="utf-8", errors="replace")
                    fh.seek(self._pos)

                # Detect truncation (new game session rotated the log)
                current_size = self._get_file_size()
                if current_size < self._pos:
                    logger.info("LogMonitor: log rotated, seeking to 0")
                    self._pos = 0
                    fh.seek(0)

                line = fh.readline()
                if line:
                    self._pos = fh.tell()
                    self._dispatch(line.rstrip("\n"))
                else:
                    time.sleep(_POLL_INTERVAL)

            except OSError as exc:
                logger.warning("LogMonitor read error: %s — retrying", exc)
                if fh:
                    try:
                        fh.close()
                    except OSError:
                        pass
                    fh = None
                time.sleep(_REOPEN_DELAY)

        if fh:
            try:
                fh.close()
            except OSError:
                pass

    def _get_file_size(self) -> int:
        try:
            return os.path.getsize(self._path)
        except OSError:
            return 0

    def _dispatch(self, line: str) -> None:
        for cb in self._callbacks:
            try:
                cb(line)
            except Exception:
                logger.exception("LogMonitor callback error")
