#!/usr/bin/env python3
"""Standalone Erkul cache refresher — no UI, no app required.

Fetches fresh data from erkul.games and writes .erkul_cache.json.
Safe to run while the DPS Calculator app is also running (writes
atomically via temp file then rename).

Usage:
    python refresh_erkul_cache.py            # fetch + save
    python refresh_erkul_cache.py --force    # delete old cache first
    python refresh_erkul_cache.py --status   # print cache age and exit

Scheduling (run once daily via Windows Task Scheduler):
    Program:   python.exe (full path)
    Arguments: refresh_erkul_cache.py
    Start in:  DPS_Calculator folder
"""

import os
import sys
import json
import time
import argparse
import tempfile

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.normpath(os.path.join(SCRIPT_DIR, '..', '..')))
from shared.app_bootstrap import bootstrap_skill  # noqa: E402
bootstrap_skill(__file__)

from shared.api_config import (
    ERKUL_BASE_URL, ERKUL_HEADERS, CACHE_TTL_ERKUL,
)
from data.api_client import ErkulApiClient
from data.cache import DiskCache

CACHE_FILE    = os.path.join(SCRIPT_DIR, ".erkul_cache.json")
CACHE_VERSION = 5  # must match repository.py CACHE_VERSION

# Endpoints to fetch  (cache_key → api_path)
_ENDPOINTS = [
    ("/live/weapons",       "/live/weapons"),
    ("/live/shields",       "/live/shields"),
    ("/live/coolers",       "/live/coolers"),
    ("/live/missiles",      "/live/missiles"),
    ("/live/radars",        "/live/radars"),
    ("/live/powerplants",   "/live/power-plants"),
    ("/live/quantumdrives", "/live/qdrives"),
    ("/live/thrusters",     "/live/thrusters"),
    ("/live/paints",        "/live/paints"),
]


def _fmt_age(seconds: float) -> str:
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds / 60)}m"
    return f"{seconds / 3600:.1f}h"


def _cache_status():
    if not os.path.isfile(CACHE_FILE):
        print("Cache file: NOT FOUND")
        return
    try:
        with open(CACHE_FILE, "rb") as f:
            obj = json.loads(f.read())
    except Exception as e:
        print(f"Cache file: CORRUPT ({e})")
        return
    ts = obj.get("ts", 0)
    version = obj.get("version", "?")
    game_ver = obj.get("game_version", "unknown")
    age = time.time() - ts
    size_mb = os.path.getsize(CACHE_FILE) / 1048576
    data = obj.get("data", {})
    print(f"Cache file : {CACHE_FILE}")
    print(f"Size       : {size_mb:.1f} MB")
    print(f"Version    : {version} (current: {CACHE_VERSION})")
    print(f"Game ver   : {game_ver}")
    print(f"Age        : {_fmt_age(age)} (TTL: {_fmt_age(CACHE_TTL_ERKUL)})")
    print(f"Fresh      : {'YES' if age < CACHE_TTL_ERKUL else 'NO — would refresh on next app launch'}")
    print()
    print("Endpoints cached:")
    for key in sorted(data.keys()):
        entries = data[key]
        count = len(entries) if isinstance(entries, list) else "?"
        print(f"  {key:30s}  {count:>4} entries")


def _fetch_and_save(force: bool = False):
    disk = DiskCache(CACHE_FILE, CACHE_TTL_ERKUL, CACHE_VERSION)

    if not force:
        existing = disk.load()
        if existing:
            age = time.time() - disk._last_ts
            print(f"Cache is still fresh ({_fmt_age(age)} old, TTL {_fmt_age(CACHE_TTL_ERKUL)}) — skipping fetch.")
            print("Use --force to refresh anyway.")
            return False

    client = ErkulApiClient(ERKUL_BASE_URL, ERKUL_HEADERS, timeout=30, retries=3)

    print(f"Fetching from {ERKUL_BASE_URL} ...")
    raw = {}
    total_start = time.time()

    for cache_key, api_path in _ENDPOINTS:
        t0 = time.time()
        try:
            result = client.fetch_safe(api_path)
            elapsed = time.time() - t0
            count = len(result) if isinstance(result, list) else 0
            print(f"  {api_path:30s}  {count:>4} items  ({elapsed:.1f}s)")
            raw[cache_key] = result
        except Exception as e:
            print(f"  {api_path:30s}  ERROR: {e}")
            raw[cache_key] = []

    print(f"  {'ships (all variants)':30s}  ...", end="", flush=True)
    t0 = time.time()
    ships = client.fetch_all_ships()
    elapsed = time.time() - t0
    raw["/live/ships"] = ships
    print(f"  {len(ships):>4} ships  ({elapsed:.1f}s)")

    total_elapsed = time.time() - total_start

    # Atomic write: write to temp file then rename so the app never
    # reads a half-written cache file.
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=os.path.dirname(CACHE_FILE), suffix=".tmp"
    )
    try:
        payload = json.dumps({
            "ts": time.time(),
            "version": CACHE_VERSION,
            "game_version": "",
            "data": raw,
        }).encode("utf-8")
        with os.fdopen(tmp_fd, "wb") as f:
            f.write(payload)
        os.replace(tmp_path, CACHE_FILE)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    size_mb = os.path.getsize(CACHE_FILE) / 1048576
    print()
    print(f"Cache saved: {CACHE_FILE} ({size_mb:.1f} MB, {total_elapsed:.1f}s total)")
    return True


def main():
    parser = argparse.ArgumentParser(description="Refresh the Erkul DPS cache.")
    parser.add_argument("--force",  action="store_true",
                        help="Fetch even if the cache is still fresh")
    parser.add_argument("--status", action="store_true",
                        help="Print cache status and exit without fetching")
    args = parser.parse_args()

    if args.status:
        _cache_status()
        return

    _fetch_and_save(force=args.force)


if __name__ == "__main__":
    main()
