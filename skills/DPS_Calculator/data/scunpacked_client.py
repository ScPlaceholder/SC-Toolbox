"""HTTP client for StarCitizenWiki/scunpacked-data on GitHub.

Enumerates the items/ directory via the GitHub Contents API and fetches
individual item JSON files from raw.githubusercontent.com in parallel.

Item files are named after their in-game className (lower-cased), e.g.
``aegs_avenger_thruster_main.json``.

Usage::

    client = ScunpackedClient()
    filenames = client.fetch_item_list()          # all *.json names in items/
    items     = client.fetch_items_by_pattern(    # parallel fetch + filter
        ["thruster", "_cml_"]
    )
"""

from __future__ import annotations

import logging
import time
import urllib.error
import urllib.request
import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

_log = logging.getLogger(__name__)

_GITHUB_API  = "https://api.github.com/repos/StarCitizenWiki/scunpacked-data/contents/items"
_RAW_BASE    = "https://raw.githubusercontent.com/StarCitizenWiki/scunpacked-data/master/items/"
_TIMEOUT     = 20          # seconds per request
_BACKOFF     = 1.0         # seconds base for exponential backoff
_MAX_RETRIES = 3
_MAX_WORKERS = 12          # parallel item fetches

# GitHub API returns at most 100 entries per page; iterate until empty.
_API_PER_PAGE = 100


def _get_json(url: str, headers: dict | None = None) -> dict | list | None:
    """GET *url* with retries. Returns parsed JSON or None on failure."""
    hdrs = {"Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            **(headers or {})}
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers=hdrs)
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            _log.warning("HTTP %d fetching %s (attempt %d)", exc.code, url, attempt)
            if 400 <= exc.code < 500 and exc.code != 429:
                return None
        except (urllib.error.URLError, OSError, TimeoutError, json.JSONDecodeError) as exc:
            _log.warning("Error fetching %s (attempt %d): %s", url, attempt, exc)
        if attempt < _MAX_RETRIES:
            time.sleep(_BACKOFF * (2 ** (attempt - 1)))
    return None


class ScunpackedClient:
    """Fetches item data from the StarCitizenWiki/scunpacked-data repository."""

    def fetch_item_list(self,
                        on_progress: Callable[[int], None] | None = None) -> list[str]:
        """Return list of all *.json filenames in the items/ directory.

        Paginates through the GitHub Contents API.  May return a cached
        result if called multiple times in the same process; call is
        thread-safe (read-only list returned by value).

        Parameters
        ----------
        on_progress:
            Called with (page_number) after each page is fetched.
        """
        filenames: list[str] = []
        page = 1
        while True:
            url = f"{_GITHUB_API}?per_page={_API_PER_PAGE}&page={page}"
            _log.debug("Fetching item list page %d …", page)
            data = _get_json(url)
            if not data or not isinstance(data, list):
                break
            for entry in data:
                name = entry.get("name", "")
                if name.endswith(".json"):
                    filenames.append(name)
            if on_progress:
                try:
                    on_progress(page)
                except Exception:
                    pass
            if len(data) < _API_PER_PAGE:
                break           # last page
            page += 1
        _log.info("Item list: %d files found across %d page(s)", len(filenames), page)
        return filenames

    def fetch_item(self, filename: str) -> dict | None:
        """Fetch and parse a single item JSON file by its filename.

        Returns the parsed dict or None on failure.
        """
        url = f"{_RAW_BASE}{filename}"
        return _get_json(url)

    def fetch_items_by_pattern(
        self,
        patterns: list[str],
        filenames: list[str] | None = None,
        on_progress: Callable[[int, int], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> list[dict]:
        """Fetch all items whose filename contains any of *patterns*.

        Parameters
        ----------
        patterns:
            Substrings to match against filenames (case-insensitive).
            E.g. ``["thruster", "_cml_"]``.
        filenames:
            Pre-fetched item list; if None, ``fetch_item_list()`` is called.
        on_progress:
            Called with (fetched_count, total_count) after each item.
        cancel_event:
            If set, fetching stops early.

        Returns
        -------
        List of dicts ``{"filename": str, "data": dict}`` for each item
        whose file was successfully downloaded and parsed.
        """
        if filenames is None:
            filenames = self.fetch_item_list()

        lower_patterns = [p.lower() for p in patterns]
        candidates = [
            fn for fn in filenames
            if any(p in fn.lower() for p in lower_patterns)
        ]
        _log.info("Pattern match: %d candidates from %d files (patterns=%s)",
                  len(candidates), len(filenames), patterns)

        results: list[dict] = []
        lock = threading.Lock()
        fetched = [0]
        total = len(candidates)

        def _fetch_one(fn: str):
            if cancel_event and cancel_event.is_set():
                return
            item_data = self.fetch_item(fn)
            if item_data:
                with lock:
                    results.append({"filename": fn, "data": item_data})
                    fetched[0] += 1
                    if on_progress:
                        try:
                            on_progress(fetched[0], total)
                        except Exception:
                            pass

        with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
            futures = [pool.submit(_fetch_one, fn) for fn in candidates]
            for f in as_completed(futures):
                if cancel_event and cancel_event.is_set():
                    break
                try:
                    f.result()
                except Exception as exc:
                    _log.warning("Item fetch task failed: %s", exc)

        _log.info("Fetched %d / %d items successfully", len(results), total)
        return results
