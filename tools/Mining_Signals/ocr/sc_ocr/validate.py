"""Per-domain format / range / dictionary validation.

Post-classification layer that filters out segmentation errors and
OCR hallucinations before they reach the UI. Each domain has its
own set of validators; failed reads fall through to ONNX fallback
in ``api.py`` before being returned as None.

Refinery validators reuse the existing fuzzy-match infrastructure
in ``ocr/refinery_reader.py`` — no reinvention.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

log = logging.getLogger(__name__)


# ── Pre-compiled regex patterns (module scope) ─────────────────────
_RE_NON_DIGIT = re.compile(r"[^0-9]")
_RE_NON_DIGIT_DOT = re.compile(r"[^0-9.]")
_RE_MULTI_DOT = re.compile(r"\.+")
_RE_COST_NUMBER = re.compile(r"[\d,]+(?:\.\d{1,2})?")


# ── Signal scanner ─────────────────────────────────────────────────

SIGNAL_MIN = 1000
SIGNAL_MAX = 35000


def validate_signal(raw: str) -> Optional[int]:
    """Parse a digit-only string as a signal number in [1000, 35000]."""
    digits = _RE_NON_DIGIT.sub("", raw)
    if not digits:
        return None
    try:
        val = int(digits)
    except ValueError:
        return None
    if SIGNAL_MIN <= val <= SIGNAL_MAX:
        return val
    # Strip leading digit icons (HUD sometimes has a decorative
    # digit-like icon glued to the value). Try dropping one leading
    # char.
    if len(digits) >= 4:
        try:
            val2 = int(digits[1:])
            if SIGNAL_MIN <= val2 <= SIGNAL_MAX:
                return val2
        except ValueError:
            pass
    return None


# ── Mining HUD ─────────────────────────────────────────────────────

MASS_MAX = 10_000_000.0  # kg — large asteroids can exceed a million


def validate_mass(raw: str) -> Optional[float]:
    """Parse a mass read as a float in [0.1, MASS_MAX]."""
    cleaned = _RE_NON_DIGIT_DOT.sub("", raw)
    if not cleaned:
        return None
    # Collapse accidental double dots
    cleaned = _RE_MULTI_DOT.sub(".", cleaned).strip(".")
    if not cleaned:
        return None
    try:
        val = float(cleaned)
    except ValueError:
        return None
    if 0.1 <= val <= MASS_MAX:
        return val
    return None


def validate_pct(raw: str) -> Optional[float]:
    """Parse a percentage read as a float in [0, 100]."""
    # Strip trailing % and inner whitespace; keep digits + dot
    cleaned = _RE_NON_DIGIT_DOT.sub("", raw.replace("%", ""))
    if not cleaned:
        return None
    cleaned = _RE_MULTI_DOT.sub(".", cleaned).strip(".")
    if not cleaned:
        return None
    try:
        val = float(cleaned)
    except ValueError:
        return None
    if 0.0 <= val <= 100.0:
        return val
    # Sometimes trailing digit is a misread '%' — try dropping
    # progressively from the right.
    for end in range(len(cleaned) - 1, 0, -1):
        try:
            v = float(cleaned[:end])
            if 0.0 <= v <= 100.0:
                return v
        except ValueError:
            continue
    return None


def validate_instability(
    raw: str,
    confidences: list[float] | None = None,
) -> Optional[float]:
    """Parse an instability read as a float.

    In-game instability values for mineable asteroids practically
    always live in [0.0, 200.0]. A raw read like '12318' that passes
    numeric validation but exceeds that band almost certainly dropped
    the decimal point. We always probe for a missed decimal when:
      * The raw string has no dot AND
      * There's a low-confidence character (< 0.55) that could be
        a misclassified dot AND
      * Inserting a dot at that position yields a value in the
        plausible [0.0, 200.0] band
    """
    cleaned = _RE_NON_DIGIT_DOT.sub("", raw)
    if not cleaned:
        return None
    cleaned = _RE_MULTI_DOT.sub(".", cleaned).strip(".")
    if not cleaned:
        return None
    try:
        val = float(cleaned)
    except ValueError:
        return None

    # Try decimal recovery if the raw string has no dot, confidences
    # are provided, and there's a low-confidence char that might be
    # a misread decimal. This probe runs BEFORE accepting the no-dot
    # value so we don't lock in '12318' for a 12.10 truth.
    def _try_recover() -> Optional[float]:
        if "." in cleaned or not confidences:
            return None
        if len(confidences) != len(raw):
            return None
        # Rank chars by ascending confidence — try the lowest first
        order = sorted(range(len(confidences)), key=lambda i: confidences[i])
        for idx in order[:2]:  # try the 2 least-confident positions
            if confidences[idx] > 0.60:
                break  # remaining are all reasonably confident
            attempt = raw[:idx] + "." + raw[idx + 1:]
            attempt_clean = _RE_NON_DIGIT_DOT.sub("", attempt)
            attempt_clean = _RE_MULTI_DOT.sub(".", attempt_clean).strip(".")
            try:
                v2 = float(attempt_clean)
            except ValueError:
                continue
            # Prefer recovery when result falls in typical instability
            # range (0-200). If it's totally implausible (> 10000),
            # keep looking.
            if 0.0 <= v2 <= 200.0:
                return v2
        return None

    recovered = _try_recover()
    if recovered is not None:
        return recovered

    # No recovery needed or possible — accept raw value if in broad range.
    if 0.0 <= val <= 100000.0:
        return val
    return None


# ── Refinery ───────────────────────────────────────────────────────
# Delegated to ocr/refinery_reader.py's existing fuzzy matchers.
# They operate on the raw OCR text (after glyph joins) and return
# the canonical form. We re-import lazily to avoid a hard circular
# dependency.

def validate_refinery_method(raw: str) -> Optional[str]:
    try:
        from .. import refinery_reader
    except Exception:
        return raw.strip() or None
    # refinery_reader has _fuzzy_method which does the matching.
    matcher = getattr(refinery_reader, "_fuzzy_method", None)
    if matcher is None:
        return raw.strip() or None
    try:
        return matcher(raw) or None
    except Exception:
        return raw.strip() or None


def validate_refinery_commodity(raw: str) -> Optional[str]:
    try:
        from .. import refinery_reader
    except Exception:
        return raw.strip() or None
    matcher = getattr(refinery_reader, "_fuzzy_mineral", None)
    if matcher is None:
        return raw.strip() or None
    try:
        return matcher(raw) or None
    except Exception:
        return raw.strip() or None


def validate_refinery_time(raw: str) -> Optional[int]:
    try:
        from .. import refinery_reader
    except Exception:
        return None
    parser = getattr(refinery_reader, "_parse_time_to_seconds", None)
    if parser is None:
        return None
    try:
        secs = parser(raw)
        if secs and secs > 0:
            return int(secs)
    except Exception:
        pass
    return None


def validate_refinery_cost(raw: str) -> Optional[float]:
    # Allow digits, commas, dots, keep the first number-looking thing
    match = _RE_COST_NUMBER.search(raw)
    if not match:
        return None
    s = match.group(0).replace(",", "")
    try:
        val = float(s)
    except ValueError:
        return None
    if 1.0 <= val <= 1_000_000_000.0:
        return val
    return None
