"""Local inventory persistence — tracks which blueprints the user owns."""

from __future__ import annotations

import json
import logging
import os
import threading

log = logging.getLogger(__name__)

_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".craft_cache")
_INV_PATH = os.path.join(_CACHE_DIR, "inventory.json")


class InventoryService:
    """Thread-safe store of owned blueprint data, persisted as JSON."""

    def __init__(self, path: str = _INV_PATH) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._owned: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._owned = data.get("owned", {})
            log.debug("Inventory loaded: %d blueprints", len(self._owned))
        except FileNotFoundError:
            self._owned = {}
        except json.JSONDecodeError:
            log.warning("Inventory file corrupted, starting fresh: %s", self._path)
            self._owned = {}

    def _save(self, snapshot: dict) -> None:
        """Persist inventory snapshot to disk."""
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump({"owned": snapshot}, f, indent=2)

    def is_owned(self, blueprint_id: str) -> bool:
        with self._lock:
            return blueprint_id in self._owned

    def owned_ids(self) -> set[str]:
        with self._lock:
            return set(self._owned.keys())

    def owned_count(self) -> int:
        with self._lock:
            return len(self._owned)

    def add(self, blueprint_id: str, blueprint_dict: dict) -> None:
        with self._lock:
            self._owned[blueprint_id] = blueprint_dict
            snapshot = dict(self._owned)
        self._save(snapshot)

    def remove(self, blueprint_id: str) -> None:
        with self._lock:
            self._owned.pop(blueprint_id, None)
            snapshot = dict(self._owned)
        self._save(snapshot)

    def get_all(self) -> list[dict]:
        with self._lock:
            return list(self._owned.values())
