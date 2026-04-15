"""Unit tests for services.log_scanner — regex parsing and dedup logic.

Covers the refinery completion regex, location regex, dedup ID
generation, and edge cases (multi-count, missing groups, malformed).
"""

from __future__ import annotations

import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.log_scanner import (
    _REFINERY_RE,
    _LOCATION_RE,
    _parse_refinery_line,
    _make_id,
    _resolve_location,
)


class TestRefineryRegex(unittest.TestCase):
    """Verify _REFINERY_RE matches real game log lines."""

    SINGLE_ORDER = (
        '<2025-12-15T14:32:01.123Z> [Notice] <SHUDEvent_OnNotification> '
        'event="Generic" subtitle="A Refinery Work Order has been Completed at HUR-L2: Stanton"'
    )
    MULTI_ORDER = (
        '<2025-12-15T14:32:01.123Z> [Notice] <SHUDEvent_OnNotification> '
        'event="Generic" subtitle="3 Refinery Work Orders have been Completed at CRU-L1: Stanton"'
    )
    NO_MATCH = (
        '<2025-12-15T14:32:01.123Z> [Notice] Something completely different'
    )
    TWO_ORDERS = (
        '<2025-12-15T14:32:01.123Z> [Notice] <SHUDEvent_OnNotification> '
        'event="Generic" subtitle="2 Refinery Work Orders have been Completed at ARC-L3: Stanton"'
    )

    def test_single_order_match(self):
        m = _REFINERY_RE.search(self.SINGLE_ORDER)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "2025-12-15T14:32:01.123")
        self.assertIsNone(m.group(2))  # no count prefix for "A"
        self.assertEqual(m.group(3), "HUR-L2")

    def test_multi_order_match(self):
        m = _REFINERY_RE.search(self.MULTI_ORDER)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(2), "3")
        self.assertEqual(m.group(3), "CRU-L1")

    def test_two_orders(self):
        m = _REFINERY_RE.search(self.TWO_ORDERS)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(2), "2")
        self.assertEqual(m.group(3), "ARC-L3")

    def test_no_match(self):
        m = _REFINERY_RE.search(self.NO_MATCH)
        self.assertIsNone(m)

    def test_empty_string(self):
        m = _REFINERY_RE.search("")
        self.assertIsNone(m)


class TestParseRefineryLine(unittest.TestCase):
    """Verify _parse_refinery_line returns correct dicts."""

    def test_single_order(self):
        line = (
            '<2026-01-10T08:15:30.500Z> [Notice] <SHUDEvent_OnNotification> '
            'event="Generic" subtitle="A Refinery Work Order has been Completed at MIC-L4: Stanton"'
        )
        result = _parse_refinery_line(line)
        self.assertIsNotNone(result)
        self.assertEqual(result["timestamp"], "2026-01-10T08:15:30.500")
        self.assertEqual(result["location"], "MIC-L4")
        self.assertEqual(result["count"], 1)
        self.assertIn("id", result)
        self.assertEqual(len(result["id"]), 12)  # md5[:12]

    def test_multi_order(self):
        line = (
            '<2026-01-10T08:15:30.500Z> [Notice] <SHUDEvent_OnNotification> '
            'event="Generic" subtitle="5 Refinery Work Orders have been Completed at HUR-L1: Stanton"'
        )
        result = _parse_refinery_line(line)
        self.assertIsNotNone(result)
        self.assertEqual(result["count"], 5)
        self.assertEqual(result["location"], "HUR-L1")

    def test_no_match_returns_none(self):
        self.assertIsNone(_parse_refinery_line("random log line"))

    def test_empty_returns_none(self):
        self.assertIsNone(_parse_refinery_line(""))

    def test_location_with_colon_stops_at_colon(self):
        line = (
            '<2026-02-01T12:00:00.000Z> [Notice] <SHUDEvent_OnNotification> '
            'event="Generic" subtitle="A Refinery Work Order has been Completed at CRU-L5: Something"'
        )
        result = _parse_refinery_line(line)
        self.assertIsNotNone(result)
        self.assertEqual(result["location"], "CRU-L5")


class TestMakeId(unittest.TestCase):
    """Dedup ID generation."""

    def test_deterministic(self):
        id1 = _make_id("2026-01-01T00:00:00", "HUR-L1")
        id2 = _make_id("2026-01-01T00:00:00", "HUR-L1")
        self.assertEqual(id1, id2)

    def test_different_inputs(self):
        id1 = _make_id("2026-01-01T00:00:00", "HUR-L1")
        id2 = _make_id("2026-01-01T00:00:01", "HUR-L1")
        self.assertNotEqual(id1, id2)

    def test_length(self):
        self.assertEqual(len(_make_id("ts", "loc")), 12)


class TestLocationRegex(unittest.TestCase):
    """Verify _LOCATION_RE matches location log lines."""

    def test_basic_match(self):
        line = "RequestLocationInventory Location[RR_HUR_L2]"
        m = _LOCATION_RE.search(line)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "RR_HUR_L2")

    def test_no_match(self):
        line = "Some other log line"
        m = _LOCATION_RE.search(line)
        self.assertIsNone(m)


class TestResolveLocation(unittest.TestCase):
    """Location code to human name resolution."""

    def test_exact_match(self):
        self.assertEqual(_resolve_location("RR_HUR_L1"), "HUR-L1")
        self.assertEqual(_resolve_location("RR_CRU_L2"), "CRU-L2")

    def test_named_stations(self):
        self.assertEqual(_resolve_location("RR_HUR_LEO"), "Everus Harbor")
        self.assertEqual(_resolve_location("RR_ARC_LEO"), "Baijini Point")

    def test_unknown_returns_raw(self):
        self.assertEqual(_resolve_location("UNKNOWN_CODE"), "UNKNOWN_CODE")

    def test_partial_match(self):
        # Codes with suffixes should still resolve via startswith
        result = _resolve_location("RR_HUR_L1_extra_suffix")
        self.assertEqual(result, "HUR-L1")


if __name__ == "__main__":
    unittest.main()
