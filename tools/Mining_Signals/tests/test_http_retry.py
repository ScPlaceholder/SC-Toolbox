"""Unit tests for services.http_retry — retry logic without real network."""

from __future__ import annotations

import sys
import os
import unittest
from unittest.mock import patch, MagicMock
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.http_retry import (
    urlopen_with_retry,
    DEFAULT_RETRIES,
    _RETRYABLE_HTTP_CODES,
)


class TestRetrySuccess(unittest.TestCase):

    @patch("services.http_retry.urllib.request.urlopen")
    def test_succeeds_first_try(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_urlopen.return_value = mock_resp
        result = urlopen_with_retry("https://example.com", timeout=5)
        self.assertEqual(result, mock_resp)
        self.assertEqual(mock_urlopen.call_count, 1)

    @patch("services.http_retry.time.sleep")
    @patch("services.http_retry.urllib.request.urlopen")
    def test_succeeds_after_retry(self, mock_urlopen, mock_sleep):
        mock_resp = MagicMock()
        mock_urlopen.side_effect = [
            urllib.error.URLError("timeout"),
            mock_resp,
        ]
        result = urlopen_with_retry("https://example.com", timeout=5, retries=2)
        self.assertEqual(result, mock_resp)
        self.assertEqual(mock_urlopen.call_count, 2)
        self.assertEqual(mock_sleep.call_count, 1)


class TestRetryExhausted(unittest.TestCase):

    @patch("services.http_retry.time.sleep")
    @patch("services.http_retry.urllib.request.urlopen")
    def test_raises_after_all_retries(self, mock_urlopen, mock_sleep):
        mock_urlopen.side_effect = urllib.error.URLError("timeout")
        with self.assertRaises(urllib.error.URLError):
            urlopen_with_retry("https://example.com", timeout=5, retries=2)
        # 1 initial + 2 retries = 3 total
        self.assertEqual(mock_urlopen.call_count, 3)
        self.assertEqual(mock_sleep.call_count, 2)


class TestNonRetryableErrors(unittest.TestCase):

    @patch("services.http_retry.urllib.request.urlopen")
    def test_404_not_retried(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.HTTPError(
            "https://example.com", 404, "Not Found", {}, None
        )
        with self.assertRaises(urllib.error.HTTPError):
            urlopen_with_retry("https://example.com", timeout=5, retries=3)
        # Should NOT retry on 404
        self.assertEqual(mock_urlopen.call_count, 1)

    @patch("services.http_retry.time.sleep")
    @patch("services.http_retry.urllib.request.urlopen")
    def test_503_is_retried(self, mock_urlopen, mock_sleep):
        mock_resp = MagicMock()
        mock_urlopen.side_effect = [
            urllib.error.HTTPError(
                "https://example.com", 503, "Service Unavailable", {}, None
            ),
            mock_resp,
        ]
        result = urlopen_with_retry("https://example.com", timeout=5, retries=2)
        self.assertEqual(result, mock_resp)
        self.assertEqual(mock_urlopen.call_count, 2)

    @patch("services.http_retry.time.sleep")
    @patch("services.http_retry.urllib.request.urlopen")
    def test_429_is_retried(self, mock_urlopen, mock_sleep):
        mock_resp = MagicMock()
        mock_urlopen.side_effect = [
            urllib.error.HTTPError(
                "https://example.com", 429, "Too Many Requests", {}, None
            ),
            mock_resp,
        ]
        result = urlopen_with_retry("https://example.com", timeout=5, retries=2)
        self.assertEqual(result, mock_resp)


class TestBackoffTiming(unittest.TestCase):

    @patch("services.http_retry.time.sleep")
    @patch("services.http_retry.urllib.request.urlopen")
    def test_exponential_backoff(self, mock_urlopen, mock_sleep):
        mock_urlopen.side_effect = urllib.error.URLError("timeout")
        with self.assertRaises(urllib.error.URLError):
            urlopen_with_retry(
                "https://example.com",
                timeout=5,
                retries=3,
                backoff_base=1.0,
                backoff_max=100.0,
            )
        # Delays: 1.0, 2.0, 4.0
        calls = [c[0][0] for c in mock_sleep.call_args_list]
        self.assertAlmostEqual(calls[0], 1.0)
        self.assertAlmostEqual(calls[1], 2.0)
        self.assertAlmostEqual(calls[2], 4.0)

    @patch("services.http_retry.time.sleep")
    @patch("services.http_retry.urllib.request.urlopen")
    def test_backoff_capped(self, mock_urlopen, mock_sleep):
        mock_urlopen.side_effect = urllib.error.URLError("timeout")
        with self.assertRaises(urllib.error.URLError):
            urlopen_with_retry(
                "https://example.com",
                timeout=5,
                retries=5,
                backoff_base=5.0,
                backoff_max=10.0,
            )
        calls = [c[0][0] for c in mock_sleep.call_args_list]
        for delay in calls:
            self.assertLessEqual(delay, 10.0)


class TestRetryableCodes(unittest.TestCase):

    def test_expected_codes(self):
        self.assertIn(429, _RETRYABLE_HTTP_CODES)
        self.assertIn(500, _RETRYABLE_HTTP_CODES)
        self.assertIn(502, _RETRYABLE_HTTP_CODES)
        self.assertIn(503, _RETRYABLE_HTTP_CODES)
        self.assertIn(504, _RETRYABLE_HTTP_CODES)
        self.assertNotIn(400, _RETRYABLE_HTTP_CODES)
        self.assertNotIn(401, _RETRYABLE_HTTP_CODES)
        self.assertNotIn(404, _RETRYABLE_HTTP_CODES)


if __name__ == "__main__":
    unittest.main()
