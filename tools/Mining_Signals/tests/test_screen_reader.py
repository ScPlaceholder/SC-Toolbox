"""Unit tests for ocr.screen_reader — candidate extraction and validation.

Tests the pure logic (candidate picking, signal range validation)
without requiring actual OCR engines or screen capture.
"""

from __future__ import annotations

import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ocr.screen_reader import (
    MIN_SIGNAL,
    MAX_SIGNAL,
    _best_candidate,
    _BRIGHT_BG_THRESHOLD,
    _BRIGHT_BG_FALLBACK,
    _CHANNEL_THRESH_BRIGHT,
    _CHANNEL_THRESH_INV,
    _DARK_TEXT_THRESH,
    _DIFF_THRESH_CYAN,
    _DIFF_THRESH_ORANGE,
)


class TestBestCandidate(unittest.TestCase):
    """_best_candidate picks the best OCR result from candidate pools."""

    def test_empty(self):
        self.assertIsNone(_best_candidate([]))

    def test_single(self):
        self.assertEqual(_best_candidate([5350]), 5350)

    def test_frequency_wins(self):
        # 5350 appears 3 times, 5360 appears 1 time
        self.assertEqual(_best_candidate([5350, 5350, 5350, 5360]), 5350)

    def test_tie_broken_by_length(self):
        # Both appear once, 12345 has more digits than 5350
        self.assertEqual(_best_candidate([5350, 12345]), 12345)

    def test_multiple_same_frequency(self):
        # Two candidates each appear twice -> longer one wins
        result = _best_candidate([1000, 10000, 1000, 10000])
        self.assertEqual(result, 10000)


class TestSignalBounds(unittest.TestCase):
    """Verify signal range constants are sensible."""

    def test_min_less_than_max(self):
        self.assertLess(MIN_SIGNAL, MAX_SIGNAL)

    def test_min_positive(self):
        self.assertGreater(MIN_SIGNAL, 0)

    def test_typical_values_in_range(self):
        # Common signal values from the game
        for val in [3200, 4300, 5350, 8600, 10700, 16050, 24000]:
            self.assertTrue(MIN_SIGNAL <= val <= MAX_SIGNAL,
                            f"{val} outside [{MIN_SIGNAL}, {MAX_SIGNAL}]")


class TestOCRConstants(unittest.TestCase):
    """Verify OCR preprocessing constants are within valid pixel ranges."""

    def test_threshold_ranges(self):
        for name, val in [
            ("_BRIGHT_BG_THRESHOLD", _BRIGHT_BG_THRESHOLD),
            ("_BRIGHT_BG_FALLBACK", _BRIGHT_BG_FALLBACK),
            ("_CHANNEL_THRESH_BRIGHT", _CHANNEL_THRESH_BRIGHT),
            ("_CHANNEL_THRESH_INV", _CHANNEL_THRESH_INV),
            ("_DARK_TEXT_THRESH", _DARK_TEXT_THRESH),
        ]:
            self.assertGreaterEqual(val, 0, f"{name} below 0")
            self.assertLessEqual(val, 255, f"{name} above 255")

    def test_diff_thresholds(self):
        for name, val in [
            ("_DIFF_THRESH_CYAN", _DIFF_THRESH_CYAN),
            ("_DIFF_THRESH_ORANGE", _DIFF_THRESH_ORANGE),
        ]:
            self.assertGreaterEqual(val, 0, f"{name} below 0")
            self.assertLessEqual(val, 255, f"{name} above 255")


if __name__ == "__main__":
    unittest.main()
