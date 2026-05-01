"""PII sanitization for log records destined for files.

Wraps a base ``logging.Formatter`` so that anything written to disk is
scrubbed of home-directory paths, the local username, the machine
hostname, IP/MAC addresses, and common auth-token shapes. Console
handlers are intentionally left alone — local debug output is useful
to the user on their own machine.

Goal: when a user shares a log or crash file, zero personal information
should leave their PC.

Design
------
* All scrubbing happens at format time via ``PIISanitizingFormatter``.
* Patterns are compiled once at import.
* Sanitization never crashes the logging path — failures fall through
  to the raw formatted text.
* Sanitization is best-effort. The patterns target common PII shapes
  but cannot guarantee scrubbing of arbitrary free-form content;
  pair this filter with disciplined logging at the call sites.
"""
from __future__ import annotations

import getpass
import logging
import os
import re
import socket
from typing import Callable, List, Match, Pattern, Tuple, Union

USER_TOKEN = "<USER>"
HOST_TOKEN = "<HOST>"
EMAIL_TOKEN = "<EMAIL>"
IP_TOKEN = "<IP>"
MAC_TOKEN = "<MAC>"
SECRET_TOKEN = "<REDACTED>"

# Replacement may be a literal string or a callable (for IP allowlisting).
_Replacement = Union[str, Callable[[Match[str]], str]]
_PatternList = List[Tuple[Pattern[str], _Replacement]]


def _current_username() -> str:
    for env in ("USERNAME", "USER", "LOGNAME"):
        val = os.environ.get(env)
        if val:
            return val
    try:
        return getpass.getuser() or ""
    except Exception:
        return ""


def _current_hostname() -> str:
    try:
        return socket.gethostname() or ""
    except Exception:
        return ""


def _ip_replacement(m: Match[str]) -> str:
    ip = m.group(0)
    # Loopback and unspecified are not PII; preserve for debugging clarity.
    if ip.startswith("127.") or ip == "0.0.0.0":
        return ip
    return IP_TOKEN


def _build_patterns() -> _PatternList:
    patterns: _PatternList = []

    # Windows-style home: drive letter optional, accepts \, \\, or /.
    # Captures the username segment; replaces only that segment so
    # the surrounding path stays useful for debugging.
    patterns.append((
        re.compile(
            r"(?P<prefix>(?:[A-Za-z]:)?[\\/]+Users[\\/]+)"
            r"(?P<user>[^\\/:*?\"<>|\r\n]+)",
            re.IGNORECASE,
        ),
        rf"\g<prefix>{USER_TOKEN}",
    ))

    # WSL form: /mnt/c/Users/<name>/...
    patterns.append((
        re.compile(
            r"(?P<prefix>/mnt/[a-z]/Users/)(?P<user>[^/\s\"']+)",
            re.IGNORECASE,
        ),
        rf"\g<prefix>{USER_TOKEN}",
    ))

    # macOS: /Users/<name>/...   (negative lookbehind to avoid double-match
    # of the Windows pattern above when the path starts with a drive letter)
    patterns.append((
        re.compile(r"(?<![A-Za-z]:)(?<![A-Za-z]:[\\/])(?P<prefix>/Users/)"
                   r"(?P<user>[^/\s\"']+)"),
        rf"\g<prefix>{USER_TOKEN}",
    ))

    # Linux: /home/<name>/...
    patterns.append((
        re.compile(r"(?P<prefix>/home/)(?P<user>[^/\s\"']+)"),
        rf"\g<prefix>{USER_TOKEN}",
    ))

    # Literal username (catches stragglers — e.g. logged env vars,
    # bare-word references). 3-char minimum to limit false positives
    # against very short names.
    username = _current_username()
    if username and len(username) >= 3:
        patterns.append((
            re.compile(rf"\b{re.escape(username)}\b", re.IGNORECASE),
            USER_TOKEN,
        ))

    # Machine hostname.
    hostname = _current_hostname()
    if hostname and len(hostname) >= 3:
        patterns.append((
            re.compile(rf"\b{re.escape(hostname)}\b", re.IGNORECASE),
            HOST_TOKEN,
        ))
        # Often hostnames also appear in FQDN form; the bare label match
        # above covers the leading label, which is enough for redaction.

    # Email addresses.
    patterns.append((
        re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"),
        EMAIL_TOKEN,
    ))

    # IPv4 — preserve loopback / 0.0.0.0 via callable replacement.
    patterns.append((
        re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
        _ip_replacement,
    ))

    # MAC addresses (xx:xx:xx:xx:xx:xx or xx-xx-...).
    patterns.append((
        re.compile(r"\b(?:[0-9A-Fa-f]{2}[:\-]){5}[0-9A-Fa-f]{2}\b"),
        MAC_TOKEN,
    ))

    # Authorization-style tokens: Bearer/Basic/Token <value>
    patterns.append((
        re.compile(
            r"(?P<key>(?:Bearer|Basic|Token)\s+)[A-Za-z0-9._\-+/=]{8,}",
            re.IGNORECASE,
        ),
        rf"\g<key>{SECRET_TOKEN}",
    ))

    # key=value style secrets in URLs / query strings / config dumps.
    patterns.append((
        re.compile(
            r"(?P<key>(?:api[_-]?key|access[_-]?token|auth[_-]?token|"
            r"client[_-]?secret|refresh[_-]?token|"
            r"token|password|secret|passwd)"
            r"\s*[:=]\s*[\"']?)"
            r"(?P<val>[^\"'&\s,}\]]{4,})",
            re.IGNORECASE,
        ),
        rf"\g<key>{SECRET_TOKEN}",
    ))

    return patterns


_PATTERNS: _PatternList = _build_patterns()


def sanitize(text: str) -> str:
    """Apply every PII pattern to *text* and return the redacted result.

    Never raises — on internal failure the original string is returned.
    """
    if not text:
        return text
    try:
        for pattern, repl in _PATTERNS:
            text = pattern.sub(repl, text)
    except Exception:
        return text
    return text


class PIISanitizingFormatter(logging.Formatter):
    """Formatter wrapper that scrubs PII from final log output.

    Attach to file handlers only. Console handlers should keep their
    raw output so the user can debug their own machine without
    redactions.
    """

    def __init__(self, inner: logging.Formatter):
        # We bypass super().__init__() format/datefmt fields because we
        # delegate everything to *inner* and then redact the result.
        super().__init__()
        self._inner = inner

    def format(self, record: logging.LogRecord) -> str:
        try:
            text = self._inner.format(record)
        except Exception:
            text = super().format(record)
        return sanitize(text)

    def formatException(self, ei) -> str:  # type: ignore[override]
        return sanitize(self._inner.formatException(ei))

    def formatStack(self, stack_info: str) -> str:  # type: ignore[override]
        return sanitize(self._inner.formatStack(stack_info))


def wrap(inner: logging.Formatter) -> PIISanitizingFormatter:
    """Convenience: wrap *inner* in a PIISanitizingFormatter."""
    return PIISanitizingFormatter(inner)
