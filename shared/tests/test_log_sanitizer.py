"""Tests for shared.log_sanitizer — PII redaction in log output."""

import os, sys
sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..')))
import shared.path_setup
shared.path_setup.ensure_path(os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')))

import io
import logging
from unittest import mock

import pytest


# Lock USERNAME / hostname *before* importing the module so its patterns
# include known sentinel values and tests stay deterministic across machines.
@pytest.fixture(scope="module", autouse=True)
def _sanitizer():
    with mock.patch.dict(os.environ, {"USERNAME": "alice", "USER": "alice"}, clear=False):
        with mock.patch("socket.gethostname", return_value="test-rig"):
            # Force module reimport so _build_patterns() picks up our values
            import importlib
            import shared.log_sanitizer as ls
            importlib.reload(ls)
            yield ls
            importlib.reload(ls)  # restore original-machine patterns


# ── Path redaction ────────────────────────────────────────────────────


class TestWindowsHomePaths:
    def test_drive_letter_backslash(self, _sanitizer):
        out = _sanitizer.sanitize(r"C:\Users\alice\Documents\foo.json")
        assert out == r"C:\Users\<USER>\Documents\foo.json"

    def test_drive_letter_double_backslash_repr(self, _sanitizer):
        out = _sanitizer.sanitize(r"'C:\\Users\\alice\\Documents\\foo.json'")
        assert "alice" not in out
        assert "<USER>" in out

    def test_drive_letter_forward_slash(self, _sanitizer):
        out = _sanitizer.sanitize("C:/Users/alice/Documents/foo.json")
        assert out == "C:/Users/<USER>/Documents/foo.json"

    def test_lowercase_drive(self, _sanitizer):
        out = _sanitizer.sanitize(r"c:\users\alice\foo")
        assert "alice" not in out
        assert "<USER>" in out

    def test_unc_with_users(self, _sanitizer):
        out = _sanitizer.sanitize(r"\\server\Users\alice\share")
        assert "alice" not in out


class TestPosixHomePaths:
    def test_macos_users(self, _sanitizer):
        out = _sanitizer.sanitize("/Users/alice/Documents/foo")
        assert out == "/Users/<USER>/Documents/foo"

    def test_linux_home(self, _sanitizer):
        out = _sanitizer.sanitize("/home/alice/.config/foo")
        assert out == "/home/<USER>/.config/foo"

    def test_wsl_users(self, _sanitizer):
        out = _sanitizer.sanitize("/mnt/c/Users/alice/AppData")
        assert "alice" not in out
        assert "<USER>" in out


class TestUsernameLeak:
    def test_bare_username(self, _sanitizer):
        out = _sanitizer.sanitize("hello alice goodbye")
        assert out == "hello <USER> goodbye"

    def test_username_in_argv(self, _sanitizer):
        # crash_logger banner: log.info("argv:  %s", sys.argv)
        out = _sanitizer.sanitize(
            "argv:  ['app.py', 'C:\\\\Users\\\\alice\\\\Temp\\\\x.jsonl']"
        )
        assert "alice" not in out

    def test_username_substring_left_alone(self, _sanitizer):
        # \b word boundary should leave alphanumeric-adjacent text alone
        out = _sanitizer.sanitize("aliceland")
        assert out == "aliceland"


class TestHostname:
    def test_bare_hostname(self, _sanitizer):
        out = _sanitizer.sanitize("connecting to test-rig:8080")
        assert "test-rig" not in out
        assert "<HOST>" in out


# ── Network identifiers ──────────────────────────────────────────────


class TestIPAddress:
    def test_public_ipv4_redacted(self, _sanitizer):
        out = _sanitizer.sanitize("client 8.8.8.8 connected")
        assert "8.8.8.8" not in out
        assert "<IP>" in out

    def test_loopback_preserved(self, _sanitizer):
        # Loopback is not PII — useful for debugging local issues
        out = _sanitizer.sanitize("listening on 127.0.0.1:5000")
        assert "127.0.0.1" in out

    def test_unspecified_preserved(self, _sanitizer):
        out = _sanitizer.sanitize("bind 0.0.0.0:80")
        assert "0.0.0.0" in out

    def test_version_string_not_matched(self, _sanitizer):
        # Version strings have only 3 parts and must not match an IP
        out = _sanitizer.sanitize("Python 3.14.2")
        assert out == "Python 3.14.2"


class TestMacAddress:
    def test_colon_form(self, _sanitizer):
        out = _sanitizer.sanitize("nic 0a:1b:2c:3d:4e:5f up")
        assert "0a:1b:2c:3d:4e:5f" not in out
        assert "<MAC>" in out

    def test_dash_form(self, _sanitizer):
        out = _sanitizer.sanitize("mac 0A-1B-2C-3D-4E-5F")
        assert "<MAC>" in out


class TestEmail:
    def test_redacted(self, _sanitizer):
        out = _sanitizer.sanitize("user@example.com filed a bug")
        assert "user@example.com" not in out
        assert "<EMAIL>" in out


# ── Secrets ──────────────────────────────────────────────────────────


class TestSecrets:
    def test_bearer_token(self, _sanitizer):
        out = _sanitizer.sanitize("Authorization: Bearer abcdef1234567890")
        assert "abcdef1234567890" not in out
        assert "<REDACTED>" in out

    def test_api_key_query(self, _sanitizer):
        out = _sanitizer.sanitize(
            "GET https://api.example/data?api_key=sk_live_abc123xyz"
        )
        assert "sk_live_abc123xyz" not in out
        assert "<REDACTED>" in out

    def test_password_assignment(self, _sanitizer):
        out = _sanitizer.sanitize('config: password="hunter22"')
        assert "hunter22" not in out

    def test_secret_assignment(self, _sanitizer):
        out = _sanitizer.sanitize("client_secret=verylongsecret123")
        assert "verylongsecret123" not in out


# ── Formatter integration ────────────────────────────────────────────


class TestFormatter:
    def test_wraps_inner_format(self, _sanitizer):
        inner = logging.Formatter("%(levelname)s %(message)s")
        f = _sanitizer.PIISanitizingFormatter(inner)
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg=r"reading C:\Users\alice\foo", args=(), exc_info=None,
        )
        out = f.format(record)
        assert "alice" not in out
        assert "<USER>" in out

    def test_exception_traceback_redacted(self, _sanitizer):
        inner = logging.Formatter("%(message)s")
        f = _sanitizer.PIISanitizingFormatter(inner)
        try:
            raise RuntimeError(r"boom from C:\Users\alice\code")
        except RuntimeError:
            tb_str = f.formatException(sys.exc_info())
        assert "alice" not in tb_str
        assert "<USER>" in tb_str

    def test_full_pipeline_via_handler(self, _sanitizer):
        """End-to-end: write through a real handler, confirm file content is clean."""
        buf = io.StringIO()
        handler = logging.StreamHandler(buf)
        handler.setFormatter(_sanitizer.PIISanitizingFormatter(
            logging.Formatter("%(message)s")
        ))
        logger = logging.getLogger("test_pii_handler")
        logger.setLevel(logging.DEBUG)
        logger.handlers.clear()
        logger.addHandler(handler)
        logger.propagate = False

        logger.info(r"opened C:\Users\alice\AppData\Roaming\foo")
        logger.warning("connecting to test-rig from 8.8.8.8")
        logger.error("Authorization: Bearer ABCDEFGHIJKL123456")

        text = buf.getvalue()
        assert "alice" not in text
        assert "test-rig" not in text
        assert "8.8.8.8" not in text
        assert "ABCDEFGHIJKL123456" not in text
        assert "<USER>" in text
        assert "<HOST>" in text
        assert "<IP>" in text
        assert "<REDACTED>" in text


# ── Robustness ───────────────────────────────────────────────────────


class TestRobustness:
    def test_empty_string(self, _sanitizer):
        assert _sanitizer.sanitize("") == ""

    def test_no_pii_passthrough(self, _sanitizer):
        text = "Mining Loadout: 17 lasers, 26 modules, 6 gadgets loaded OK"
        assert _sanitizer.sanitize(text) == text

    def test_in_game_data_preserved(self, _sanitizer):
        # Star Citizen system / location names are public game data,
        # not PII — must survive sanitization.
        text = "DEBUG buy_system values: ['Nyx', 'Pyro', 'Stanton']"
        assert _sanitizer.sanitize(text) == text
