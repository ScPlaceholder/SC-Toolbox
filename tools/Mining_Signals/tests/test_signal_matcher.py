"""Unit tests for services.signal_matcher — reverse signal lookup.

Covers exact match, closest match with tolerance, multi-rock signals,
empty index, and edge cases.
"""

from __future__ import annotations

import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.signal_matcher import SignalMatcher, SignalMatch


def _make_rows() -> list[dict]:
    """Create a minimal signal table for testing."""
    return [
        {"name": "Quantainium", "rarity": "Very Rare",
         "1": 5350, "2": 10700, "3": 16050},
        {"name": "Bexalite",    "rarity": "Rare",
         "1": 4300, "2": 8600,  "3": 12900},
        {"name": "Agricium",    "rarity": "Uncommon",
         "1": 3200, "2": 6400,  "3": 9600},
        {"name": "Laranite",    "rarity": "Uncommon",
         "1": 3200, "2": 6400,  "3": 9600},  # same values as Agricium
    ]


class TestExactMatch(unittest.TestCase):

    def setUp(self):
        self.matcher = SignalMatcher(_make_rows())

    def test_single_exact(self):
        m = self.matcher.find_exact(5350)
        self.assertIsNotNone(m)
        self.assertEqual(m.name, "Quantainium")
        self.assertEqual(m.rock_count, 1)
        self.assertEqual(m.delta, 0)

    def test_multi_rock_exact(self):
        m = self.matcher.find_exact(10700)
        self.assertIsNotNone(m)
        self.assertEqual(m.name, "Quantainium")
        self.assertEqual(m.rock_count, 2)

    def test_shared_value_returns_all(self):
        # Agricium and Laranite share 3200
        matches = self.matcher.find_all_exact(3200)
        self.assertEqual(len(matches), 2)
        names = {m.name for m in matches}
        self.assertIn("Agricium", names)
        self.assertIn("Laranite", names)

    def test_no_match(self):
        m = self.matcher.find_exact(9999)
        self.assertIsNone(m)


class TestClosestMatch(unittest.TestCase):

    def setUp(self):
        self.matcher = SignalMatcher(_make_rows())

    def test_within_tolerance(self):
        # 5360 is 10 away from 5350
        matches = self.matcher.find_closest(5360, tolerance=50)
        self.assertGreater(len(matches), 0)
        self.assertEqual(matches[0].name, "Quantainium")
        self.assertEqual(matches[0].delta, 10)

    def test_outside_tolerance(self):
        matches = self.matcher.find_closest(9999, tolerance=10)
        self.assertEqual(len(matches), 0)

    def test_sorted_by_delta(self):
        # Create scenario with multiple nearby values
        matches = self.matcher.find_closest(5350, tolerance=1000)
        deltas = [m.delta for m in matches]
        self.assertEqual(deltas, sorted(deltas))

    def test_zero_tolerance(self):
        # Zero tolerance = exact match only
        matches = self.matcher.find_closest(5350, tolerance=0)
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].delta, 0)


class TestMatchMethod(unittest.TestCase):

    def setUp(self):
        self.matcher = SignalMatcher(_make_rows())

    def test_exact_preferred(self):
        m = self.matcher.match(5350)
        self.assertIsNotNone(m)
        self.assertEqual(m.delta, 0)

    def test_falls_back_to_closest(self):
        m = self.matcher.match(5360, tolerance=50)
        self.assertIsNotNone(m)
        self.assertEqual(m.name, "Quantainium")
        self.assertGreater(m.delta, 0)

    def test_none_when_too_far(self):
        m = self.matcher.match(99999, tolerance=100)
        self.assertIsNone(m)


class TestMatchAll(unittest.TestCase):

    def setUp(self):
        self.matcher = SignalMatcher(_make_rows())

    def test_exact_returns_all_at_value(self):
        matches = self.matcher.match_all(3200)
        self.assertEqual(len(matches), 2)

    def test_fallback_to_closest(self):
        matches = self.matcher.match_all(5360, tolerance=200)
        self.assertGreater(len(matches), 0)


class TestUpdate(unittest.TestCase):

    def test_rebuild_clears_old(self):
        matcher = SignalMatcher(_make_rows())
        self.assertIsNotNone(matcher.find_exact(5350))
        # Update with empty data
        matcher.update([])
        self.assertIsNone(matcher.find_exact(5350))

    def test_rebuild_with_new_data(self):
        matcher = SignalMatcher([])
        self.assertIsNone(matcher.find_exact(1234))
        matcher.update([{"name": "Test", "rarity": "Common", "1": 1234}])
        self.assertIsNotNone(matcher.find_exact(1234))


class TestEmptyIndex(unittest.TestCase):

    def test_empty_matcher(self):
        matcher = SignalMatcher([])
        self.assertIsNone(matcher.find_exact(5000))
        self.assertIsNone(matcher.match(5000))
        self.assertEqual(matcher.find_closest(5000), [])
        self.assertEqual(matcher.match_all(5000), [])


class TestSignalMatchDataclass(unittest.TestCase):

    def test_frozen(self):
        m = SignalMatch(name="X", rarity="R", rock_count=1,
                        expected_value=100, delta=0)
        with self.assertRaises(AttributeError):
            m.name = "Y"

    def test_fields(self):
        m = SignalMatch(name="Q", rarity="VR", rock_count=3,
                        expected_value=16050, delta=5)
        self.assertEqual(m.name, "Q")
        self.assertEqual(m.rock_count, 3)
        self.assertEqual(m.delta, 5)


if __name__ == "__main__":
    unittest.main()
