"""
Battle Buddy — WingmanAI skill module.
Real-time loadout & consumable HUD overlay for Star Citizen.
Launches hud_app.py as a subprocess; communicates via JSONL IPC file.
"""
from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
import tempfile
import threading
from typing import TYPE_CHECKING, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core.ipc import ipc_write

from api.enums import LogType
from api.interface import SettingsConfig, SkillConfig, WingmanInitializationError
from services.printr import Printr
from skills.skill_base import Skill, tool

if TYPE_CHECKING:
    from wingmen.open_ai_wingman import OpenAiWingman

logger = logging.getLogger(__name__)
printr = Printr()

_SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
_HUD_SCRIPT = os.path.join(_SKILL_DIR, "hud_app.py")

_INSTANCES: dict = {}
_INSTANCES_LOCK = threading.RLock()


def _find_python() -> Optional[str]:
    """Locate a Python executable with PySide6 available."""
    import shutil
    candidates = [sys.executable]
    for name in ("python", "python3", "python3.11", "python3.12"):
        found = shutil.which(name)
        if found and found not in candidates:
            candidates.append(found)
    for exe in candidates:
        try:
            result = subprocess.run(
                [exe, "-c", "import PySide6"],
                capture_output=True, timeout=5,
            )
            if result.returncode == 0:
                return exe
        except (OSError, subprocess.TimeoutExpired):
            continue
    return sys.executable  # fallback


class BattleBuddy(Skill):
    """WingmanAI skill: real-time loadout HUD overlay for Star Citizen."""

    def __init__(
        self,
        config: SkillConfig,
        settings: SettingsConfig,
        wingman: "OpenAiWingman",
    ) -> None:
        super().__init__(config=config, settings=settings, wingman=wingman)
        self._proc: Optional[subprocess.Popen] = None
        self._python_exe: Optional[str] = None
        self._cmd_file: Optional[str] = None
        self._log_handle = None

    @property
    def _key(self) -> str:
        return f"battle_buddy_{self.wingman.name}"

    def _prop(self, key: str, default):
        val = self.retrieve_custom_property_value(key, [])
        return val if val is not None else default

    def _int_prop(self, key: str, default: int) -> int:
        try:
            return int(self._prop(key, default))
        except (ValueError, TypeError):
            return default

    def _float_prop(self, key: str, default: float) -> float:
        try:
            return float(self._prop(key, default))
        except (ValueError, TypeError):
            return default

    async def validate(self) -> list[WingmanInitializationError]:
        errors = await super().validate()
        if not os.path.isfile(_HUD_SCRIPT):
            from api.enums import WingmanInitializationErrorType
            errors.append(WingmanInitializationError(
                wingman_name=self.wingman.name,
                error_type=WingmanInitializationErrorType.INVALID_CONFIG,
                message=f"[BattleBuddy] hud_app.py not found at: {_HUD_SCRIPT}",
            ))
        return errors

    async def prepare(self) -> None:
        await super().prepare()

        # Tear down any stale instance
        with _INSTANCES_LOCK:
            old = _INSTANCES.get(self._key)
            if old:
                _stop_proc(old.get("proc"), old.get("cmd_file"))
                _INSTANCES.pop(self._key, None)

        self._python_exe = _find_python()
        await printr.print_async(
            f"[BattleBuddy] Using Python: {self._python_exe}",
            color=LogType.INFO, server_only=True,
        )

        if self._prop("launch_at_startup", True):
            with _INSTANCES_LOCK:
                self._launch_proc()
            await asyncio.sleep(1.0)
            if self._proc:
                await printr.print_async(
                    f"[BattleBuddy] HUD started (PID {self._proc.pid})",
                    color=LogType.INFO, server_only=True,
                )

    def _launch_proc(self) -> None:
        if not self._python_exe:
            return

        # Clean up previous cmd file
        if self._cmd_file:
            for p in (self._cmd_file, self._cmd_file + ".lock"):
                try:
                    os.remove(p)
                except OSError:
                    pass
        if self._log_handle:
            try:
                self._log_handle.close()
            except OSError:
                pass
            self._log_handle = None

        safe_key = self._key.replace(os.sep, "_").replace(" ", "_")
        self._cmd_file = os.path.join(
            tempfile.gettempdir(), f"battle_buddy_{safe_key}_{os.getpid()}.jsonl"
        )
        with open(self._cmd_file, "w"):
            pass

        log_path = os.path.join(_SKILL_DIR, "logs", "battle_buddy.log")
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        try:
            self._log_handle = open(log_path, "a", encoding="utf-8")
        except OSError:
            self._log_handle = None

        args = [
            self._python_exe, _HUD_SCRIPT,
            "--x", str(self._int_prop("window_x", 60)),
            "--y", str(self._int_prop("window_y", 880)),
            "--opacity", str(self._float_prop("opacity", 0.92)),
            "--log-path", self._prop("log_path", "C:/StarCitizen/LIVE/Game.log"),
            "--orientation", self._prop("orientation", "horizontal"),
            "--auto-show", "1" if self._prop("auto_show_on_join", True) else "0",
            "--cmd-file", self._cmd_file,
        ]

        self._proc = subprocess.Popen(
            args,
            stdin=subprocess.DEVNULL,
            stdout=self._log_handle or subprocess.DEVNULL,
            stderr=self._log_handle or subprocess.DEVNULL,
            cwd=_SKILL_DIR,
        )
        _INSTANCES[self._key] = {"proc": self._proc, "cmd_file": self._cmd_file}

    async def _ensure_running(self) -> bool:
        with _INSTANCES_LOCK:
            if self._proc and self._proc.poll() is None:
                return True
            if not self._python_exe:
                return False
            try:
                self._launch_proc()
            except (OSError, subprocess.SubprocessError) as exc:
                logger.error("Failed to launch HUD: %s", exc)
                return False
        await asyncio.sleep(0.2)
        return self._proc is not None and self._proc.poll() is None

    def _send(self, cmd: dict) -> None:
        with _INSTANCES_LOCK:
            f, p = self._cmd_file, self._proc
        if f and p and p.poll() is None:
            ipc_write(f, cmd)

    async def unload(self) -> None:
        self._send({"type": "quit"})
        _stop_proc(self._proc, self._cmd_file)
        self._proc = None
        if self._log_handle:
            try:
                self._log_handle.close()
            except OSError:
                pass
            self._log_handle = None
        with _INSTANCES_LOCK:
            _INSTANCES.pop(self._key, None)
        await super().unload()

    # ── Voice tools ──────────────────────────────────────────────────────

    @tool()
    async def show_battle_buddy(self) -> str:
        """
        Shows the Battle Buddy HUD overlay.
        Call this when the user asks to show the HUD, loadout display,
        ammo tracker, or Battle Buddy.
        """
        if not await self._ensure_running():
            return "Battle Buddy failed to start. Ensure PySide6 is installed."
        self._send({"type": "show"})
        return "Battle Buddy HUD is now visible."

    @tool()
    def hide_battle_buddy(self) -> str:
        """
        Hides the Battle Buddy HUD overlay.
        Call this when the user wants to hide or close the HUD.
        """
        self._send({"type": "hide"})
        return "Battle Buddy HUD hidden."

    @tool()
    async def toggle_battle_buddy(self) -> str:
        """
        Toggles the Battle Buddy HUD overlay visibility.
        Call this when the user says 'toggle HUD' or 'toggle Battle Buddy'.
        """
        if not await self._ensure_running():
            return "Battle Buddy failed to start."
        self._send({"type": "toggle"})
        return "Battle Buddy HUD toggled."

    @tool()
    async def report_loadout(self) -> str:
        """
        Reports the player's current detected loadout and consumable counts verbally.
        Call this when the user asks what weapons they have, how many mags are left,
        or how many medpens or oxypens remain.
        """
        if not await self._ensure_running():
            return "Battle Buddy is not running."
        self._send({"type": "report"})
        return (
            "Checking your loadout now. "
            "Battle Buddy will read out your current weapons and consumable counts."
        )


def _stop_proc(proc, cmd_file) -> None:
    if proc and proc.poll() is None:
        try:
            if cmd_file:
                ipc_write(cmd_file, {"type": "quit"})
            proc.wait(timeout=2.0)
        except (subprocess.TimeoutExpired, OSError):
            proc.terminate()
    if cmd_file:
        for p in (cmd_file, cmd_file + ".lock"):
            try:
                os.remove(p)
            except OSError:
                pass
