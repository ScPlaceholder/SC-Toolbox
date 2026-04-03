"""Match a signal value to a mining resource and rock count.

Builds a reverse lookup index from the signal table so a scanned
number can be instantly mapped back to the resource it belongs to.
"""

from __future__ import annotations

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
        # _index: maps every known signal value to (name, rarity, rocks)
        self._index: dict[int, tuple[str, str, int]] = {}
        # _all_values: sorted list of all known signal values for closest-match
        self._all_values: list[int] = []
        self._rebuild(rows)

    def _rebuild(self, rows: list[dict]) -> None:
        self._index.clear()
        for row in rows:
            name = row["name"]
            rarity = row["rarity"]
            for rocks in range(1, 7):
                val = row.get(str(rocks), 0)
                if val:
                    self._index[val] = (name, rarity, rocks)
        self._all_values = sorted(self._index.keys())

    def update(self, rows: list[dict]) -> None:
        """Rebuild the index with new data."""
        self._rebuild(rows)

    def find_exact(self, value: int) -> Optional[SignalMatch]:
        """Return an exact match, or None."""
        hit = self._index.get(value)
        if hit:
            return SignalMatch(
                name=hit[0], rarity=hit[1], rock_count=hit[2],
                expected_value=value, delta=0,
            )
        return None

    def find_closest(self, value: int, tolerance: int = 100) -> list[SignalMatch]:
        """Return matches within *tolerance* of *value*, sorted by delta.

        Returns up to 5 closest matches.
        """
        if not self._all_values:
            return []

        results: list[SignalMatch] = []
        for known in self._all_values:
            delta = abs(known - value)
            if delta <= tolerance:
                name, rarity, rocks = self._index[known]
                results.append(SignalMatch(
                    name=name, rarity=rarity, rock_count=rocks,
                    expected_value=known, delta=delta,
                ))

        results.sort(key=lambda m: m.delta)
        return results[:5]

    def match(self, value: int, tolerance: int = 100) -> Optional[SignalMatch]:
        """Best-effort match: exact first, then closest within tolerance."""
        exact = self.find_exact(value)
        if exact:
            return exact
        closest = self.find_closest(value, tolerance)
        return closest[0] if closest else None
