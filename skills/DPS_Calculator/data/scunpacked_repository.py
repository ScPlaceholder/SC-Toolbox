"""ScunpackedRepository — loads and indexes scunpacked-data items.

Runs the full fetch + parse pipeline in a background thread and exposes
the same find_* / *_for_size interface as ComponentRepository so callers
can use both interchangeably.

Cache layout (`.scunpacked_cache.json`):
    {
      "version": CACHE_VERSION,
      "ts": <unix timestamp>,
      "data": {
        "item_list": ["aegs_avenger_thruster_main.json", ...],
        "items":     [{"filename": "...", "data": {...}}, ...]
      }
    }

Items are re-fetched when:
    • No cache file exists
    • Cache version mismatch
    • Cache is older than CACHE_TTL
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Optional

from data.cache import DiskCache
from data.scunpacked_client import ScunpackedClient
from services.scunpacked_stats import FETCH_PATTERNS, parse_item

_log = logging.getLogger(__name__)

_DATA_DIR      = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
_CACHE_FILE    = os.path.join(_DATA_DIR, ".scunpacked_cache.json")
_CACHE_TTL     = 7 * 24 * 3600   # 7 days — scunpacked data is infrequently updated
_CACHE_VER     = 1
_CML_SUPP_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cml_supplement.json")


def _make_key(stats: dict) -> str:
    """Canonical dict key: local_name (unique) or name_lower + "_" + size."""
    ln = stats.get("local_name", "")
    return f"{ln or stats['name'].lower()}_{stats['size']}"


class ScunpackedRepository:
    """Thread-safe repository for scunpacked-sourced component data.

    Attributes (all dicts keyed by canonical name_size string)
    ----------------------------------------------------------
    thrusters_by_name   : dict
    thrusters_by_ref    : dict  (UUID → stats)
    cmls_by_name        : dict  (countermeasure launchers)
    cmls_by_ref         : dict
    modules_by_name     : dict  (ship modules, e.g. Retaliator torpedo/cargo)
    modules_by_ref      : dict

    Local-name fast lookups (item className → stats)
    -------------------------------------------------
    thrusters_by_local_name : dict
    cmls_by_local_name      : dict
    modules_by_local_name   : dict

    Status
    ------
    loaded  : bool
    loading : bool
    error   : str | None
    """

    def __init__(self) -> None:
        self.thrusters_by_name:       dict = {}
        self.thrusters_by_ref:        dict = {}
        self.thrusters_by_local_name: dict = {}
        self.cmls_by_name:            dict = {}
        self.cmls_by_ref:             dict = {}
        self.cmls_by_local_name:      dict = {}
        self.modules_by_name:         dict = {}
        self.modules_by_ref:          dict = {}
        self.modules_by_local_name:   dict = {}

        self.loaded  = False
        self.loading = False
        self.error: Optional[str] = None

        self._lock   = threading.Lock()
        self._cancel = threading.Event()
        self._cache  = DiskCache(_CACHE_FILE, _CACHE_TTL, _CACHE_VER)
        self._client = ScunpackedClient()

    # ── Public API ────────────────────────────────────────────────────────

    def load(self,
             on_done:    callable | None = None,
             on_stage:   callable | None = None,
             stale_ok:   bool = False) -> None:
        """Start background load.  Safe to call multiple times.

        Parameters
        ----------
        on_done:
            Called (no args) when loading completes (success or failure).
        on_stage:
            Called (stage_name: str, stage_num: int, total: int) between
            stages so the caller can show progress.
        stale_ok:
            If True, use expired cache data rather than triggering a network
            fetch.  Useful during startup to populate the UI immediately.
        """
        with self._lock:
            if self.loading:
                return
            self.loading = True
        self._cancel = threading.Event()
        threading.Thread(target=self._run,
                         args=(on_done, on_stage, stale_ok),
                         daemon=True).start()

    def cancel_load(self) -> None:
        self._cancel.set()

    def invalidate_and_reload(self, on_done: callable | None = None) -> None:
        self._cache.invalidate()
        with self._lock:
            self.loaded  = False
            self.loading = False
            self.error   = None
        self.load(on_done=on_done)

    # ── Find helpers (mirroring ComponentRepository._find) ───────────────

    def _find(self, by_ref: dict, by_name: dict, by_local: dict,
              query: str, max_size: int | None = None) -> dict | None:
        if not query:
            return None
        q = query.strip()

        def size_ok(v: dict) -> bool:
            return max_size is None or v.get("size", 1) <= max_size

        # 1. UUID / ref
        if q in by_ref:
            s = by_ref[q]
            if size_ok(s):
                return s

        ql = q.lower()

        # 2. local_name (className)
        s = by_local.get(ql)
        if s and size_ok(s):
            return s

        # 3. Exact / prefix / substring name match
        candidates: list[dict] = []
        for v in by_name.values():
            if v["name"].lower() == ql and size_ok(v):
                candidates.append(v)
        if not candidates:
            for v in by_name.values():
                if v["name"].lower().startswith(ql) and size_ok(v):
                    candidates.append(v)
        if not candidates:
            for v in by_name.values():
                if ql in v["name"].lower() and size_ok(v):
                    candidates.append(v)

        return max(candidates, key=lambda x: x.get("size", 1)) if candidates else None

    def find_thruster(self, q: str, max_size: int | None = None) -> dict | None:
        return self._find(self.thrusters_by_ref, self.thrusters_by_name,
                          self.thrusters_by_local_name, q, max_size)

    def find_cml(self, q: str, max_size: int | None = None) -> dict | None:
        return self._find(self.cmls_by_ref, self.cmls_by_name,
                          self.cmls_by_local_name, q, max_size)

    def find_module(self, q: str, max_size: int | None = None) -> dict | None:
        return self._find(self.modules_by_ref, self.modules_by_name,
                          self.modules_by_local_name, q, max_size)

    def thrusters_for_size(self, sz: int) -> list:
        return sorted(
            [v for v in self.thrusters_by_name.values() if v.get("size", 1) <= sz],
            key=lambda x: (-x.get("size", 1), x["name"]),
        )

    def cmls_for_size(self, sz: int) -> list:
        return sorted(
            [v for v in self.cmls_by_name.values() if v.get("size", 1) <= sz],
            key=lambda x: (-x.get("size", 1), x["name"]),
        )

    def modules_for_size(self, sz: int) -> list:
        return sorted(
            [v for v in self.modules_by_name.values() if v.get("size", 1) <= sz],
            key=lambda x: (-x.get("size", 1), x["name"]),
        )

    # ── Background load thread ────────────────────────────────────────────

    def _emit(self, on_stage, name: str, num: int, total: int) -> None:
        _log.info("  [scunpacked stage %d/%d] %s", num, total, name)
        if on_stage:
            try:
                on_stage(name, num, total)
            except Exception:
                pass

    def _run(self,
             on_done:  callable | None,
             on_stage: callable | None,
             stale_ok: bool) -> None:
        TOTAL = 4
        try:
            # ── Stage 1: Try cache ─────────────────────────────────────
            self._emit(on_stage, "Loading scunpacked cache", 1, TOTAL)
            cached = self._cache.load(stale_ok=stale_ok)

            if self._cancel.is_set():
                return

            if cached:
                _log.info("  scunpacked: using cached data (%d items)",
                          len(cached.get("items", [])))
                self._index_raw(cached.get("items", []))
                with self._lock:
                    self.loaded  = True
                    self.loading = False
                _log.info("  scunpacked: loaded from cache — %d thrusters, %d CMLs, %d modules",
                          len(self.thrusters_by_ref), len(self.cmls_by_ref),
                          len(self.modules_by_ref))
                if on_done:
                    on_done()
                return

            # ── Stage 2: Enumerate item list ──────────────────────────
            self._emit(on_stage, "Fetching scunpacked item list", 2, TOTAL)
            item_list = self._client.fetch_item_list()
            if not item_list:
                raise RuntimeError("scunpacked item list is empty — network issue?")

            if self._cancel.is_set():
                return

            # ── Stage 3: Fetch matching items ─────────────────────────
            self._emit(on_stage, "Fetching scunpacked components", 3, TOTAL)

            fetched_items: list[dict] = self._client.fetch_items_by_pattern(
                patterns=FETCH_PATTERNS,
                filenames=item_list,
                cancel_event=self._cancel,
            )

            if self._cancel.is_set():
                return

            # ── Stage 4: Parse + index ────────────────────────────────
            self._emit(on_stage, "Indexing scunpacked components", 4, TOTAL)
            self._index_raw(fetched_items)

            # Persist to cache (include item_list so next run skips enumeration
            # if the list hasn't changed — we just check TTL for simplicity)
            self._cache.save(
                {"item_list": item_list, "items": fetched_items},
                game_version="",
            )

            with self._lock:
                self.loaded  = True
                self.loading = False

            _log.info("  scunpacked: indexed %d thrusters, %d CMLs, %d modules",
                      len(self.thrusters_by_ref), len(self.cmls_by_ref),
                      len(self.modules_by_ref))

        except Exception as exc:
            _log.error("ScunpackedRepository load FAILED: %s", exc, exc_info=True)
            with self._lock:
                self.error   = str(exc)
                self.loading = False
        finally:
            with self._lock:
                self.loading = False
            if on_done:
                try:
                    on_done()
                except Exception:
                    pass

    def _index_raw(self, fetched_items: list[dict]) -> None:
        """Parse and index a list of ``{"filename": ..., "data": ...}`` dicts."""
        thr_name:   dict = {}
        thr_ref:    dict = {}
        thr_local:  dict = {}
        cml_name:   dict = {}
        cml_ref:    dict = {}
        cml_local:  dict = {}
        mod_name:   dict = {}
        mod_ref:    dict = {}
        mod_local:  dict = {}

        for entry in fetched_items:
            filename = entry.get("filename", "")
            doc      = entry.get("data")
            if not doc:
                continue
            try:
                stats = parse_item(doc, filename)
            except Exception as exc:
                _log.debug("parse_item failed for %s: %s", filename, exc)
                continue
            if not stats:
                continue

            key      = _make_key(stats)
            ref      = stats.get("ref", "")
            local_nm = stats.get("local_name", "")
            typ      = stats.get("type", "")

            if "Thruster" in typ or "thruster" in typ.lower():
                thr_name[key]  = stats
                if ref:
                    thr_ref[ref] = stats
                if local_nm:
                    thr_local[local_nm] = stats
            elif typ == "Module":
                mod_name[key]  = stats
                if ref:
                    mod_ref[ref] = stats
                if local_nm:
                    mod_local[local_nm] = stats
            else:
                # CML / WeaponDefensive
                cml_name[key]  = stats
                if ref:
                    cml_ref[ref] = stats
                if local_nm:
                    cml_local[local_nm] = stats

        # ── Merge local CML supplement (covers manufacturers absent from scunpacked) ──
        try:
            with open(_CML_SUPP_FILE, encoding="utf-8") as _sf:
                for _entry in json.load(_sf):
                    _ln  = _entry.get("local_name", "")
                    _ref = _entry.get("ref", "")
                    if not _ln or _ln in cml_local:
                        continue   # skip blank / already known
                    _key = _make_key(_entry)
                    cml_name.setdefault(_key, _entry)
                    if _ref:
                        cml_ref.setdefault(_ref, _entry)
                    cml_local[_ln] = _entry
            _log.debug("cml_supplement: loaded extra CML entries")
        except (FileNotFoundError, json.JSONDecodeError) as _exc:
            _log.warning("cml_supplement load failed: %s", _exc)

        # Atomic swap — no lock needed on reads after this assignment
        self.thrusters_by_name       = thr_name
        self.thrusters_by_ref        = thr_ref
        self.thrusters_by_local_name = thr_local
        self.cmls_by_name            = cml_name
        self.cmls_by_ref             = cml_ref
        self.cmls_by_local_name      = cml_local
        self.modules_by_name         = mod_name
        self.modules_by_ref          = mod_ref
        self.modules_by_local_name   = mod_local
