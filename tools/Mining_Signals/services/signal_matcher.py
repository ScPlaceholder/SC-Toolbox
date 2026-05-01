"""Match a signal value to a mining resource and rock count.

Builds a reverse lookup index from the signal table so a scanned
number can be instantly mapped back to the resource it belongs to.
Supports multiple resources sharing the same signal value.
"""

from __future__ import annotations

import bisect
from collections import defaultdict
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class SignalMatch:
    """Result of matching a signal value."""

    name: str
    rarity: str
    rock_count: int
    expected_value: int
    delta: int  # how far the scanned value is from the expected value


class SignalMatcher:
    """Reverse-lookup engine: signal value -> resource + rock count."""

    def __init__(self, rows: list[dict]) -> None:
        # _index: maps signal value to list of (name, rarity, rocks)
        self._index: dict[int, list[tuple[str, str, int]]] = {}
        # _all_values: sorted list of all known signal values
        self._all_values: list[int] = []
        self._rebuild(rows)

    def _rebuild(self, rows: list[dict]) -> None:
        index: dict[int, list[tuple[str, str, int]]] = defaultdict(list)
        for row in rows:
            name = row.get("name", "")
            rarity = row.get("rarity", "")
            if not name:
                continue
            for rocks in range(1, 21):  # support up to 20 signatures
                val = row.get(str(rocks), 0)
                if val:
                    index[val].append((name, rarity, rocks))
        self._index = dict(index)
        self._all_values = sorted(self._index.keys())

    def update(self, rows: list[dict]) -> None:
        """Rebuild the index with new data."""
        self._rebuild(rows)

    def find_all_exact(self, value: int) -> list[SignalMatch]:
        """Return all exact matches for a value."""
        hits = self._index.get(value, [])
        return [
            SignalMatch(name=h[0], rarity=h[1], rock_count=h[2],
                        expected_value=value, delta=0)
            for h in hits
        ]

    def find_exact(self, value: int) -> Optional[SignalMatch]:
        """Return the first exact match, or None."""
        matches = self.find_all_exact(value)
        return matches[0] if matches else None

    def find_closest(self, value: int, tolerance: int = 100) -> list[SignalMatch]:
        """Return all matches within *tolerance* of *value*, sorted by delta."""
        if not self._all_values:
            return []

        # Use bisect to skip the linear scan of _all_values. Walk
        # outward from the insertion point, gathering only neighbors
        # whose absolute delta is <= tolerance, then stop once the
        # next neighbor is out of range.
        vals = self._all_values
        idx = bisect.bisect_left(vals, value)
        results: list[SignalMatch] = []

        # Walk right (>= value)
        i = idx
        while i < len(vals):
            known = vals[i]
            delta = known - value  # known >= value, so >= 0
            if delta > tolerance:
                break
            for name, rarity, rocks in self._index[known]:
                results.append(SignalMatch(
                    name=name, rarity=rarity, rock_count=rocks,
                    expected_value=known, delta=delta,
                ))
            i += 1

        # Walk left (< value)
        i = idx - 1
        while i >= 0:
            known = vals[i]
            delta = value - known  # known < value, so > 0
            if delta > tolerance:
                break
            for name, rarity, rocks in self._index[known]:
                results.append(SignalMatch(
                    name=name, rarity=rarity, rock_count=rocks,
                    expected_value=known, delta=delta,
                ))
            i -= 1

        results.sort(key=lambda m: (m.delta, m.name))
        return results

    def match(self, value: int, tolerance: int = 100) -> Optional[SignalMatch]:
        """Best-effort single match: exact first, then closest within tolerance."""
        exact = self.find_exact(value)
        if exact:
            return exact
        closest = self.find_closest(value, tolerance)
        return closest[0] if closest else None

    def match_all(self, value: int, tolerance: int = 200) -> list[SignalMatch]:
        """Return all matches for a value — exact matches first, then closest."""
        exact = self.find_all_exact(value)
        if exact:
            return exact
        return self.find_closest(value, tolerance)
