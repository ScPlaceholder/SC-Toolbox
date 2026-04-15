"""User-specific template learning.

Submit confirmed-correct (char, crop) pairs. Templates update via
exponential moving average weighted by historical sample count.

Auto-learn is gated on the AUTO_LEARN_CONF_THRESHOLD: a scan must
produce NCC score > 0.92 AND pass the domain's validator before
its crops are used to update templates. This avoids reinforcing
wrong reads.

User-confirmed reads (via UI correction) bypass the confidence
gate but still require validator pass.
"""
from __future__ import annotations

import logging
import threading
from typing import Iterable

import numpy as np

from .config import AUTO_LEARN_CONF_THRESHOLD
from .templates import TemplatePack, get_pack, normalize_crop

log = logging.getLogger(__name__)

# Submission queue handled synchronously — each update is ~0.2 ms,
# the savings of going async aren't worth the thread complexity.


def submit_confirmed(
    pack_name: str,
    updates: Iterable[tuple[str, np.ndarray]],
) -> int:
    """Apply EMA updates for a batch of confirmed (char, crop_uint8) pairs.

    Crops should be the SAME shape as the template's canonical size
    (the segmenter already resized them to that shape).

    Returns the count of templates actually updated.
    """
    pack = get_pack(pack_name)
    count = 0
    for ch, crop_uint8 in updates:
        if len(ch) != 1:
            continue
        if not isinstance(crop_uint8, np.ndarray):
            continue
        try:
            normalized = normalize_crop(crop_uint8)
            pack.update_ema(ch, normalized)
            count += 1
        except Exception as exc:
            log.debug("sc_ocr.learn: update failed for '%s': %s", ch, exc)
    if count:
        log.debug("sc_ocr.learn: applied %d template updates to pack '%s'",
                  count, pack_name)
    return count


def should_auto_learn(confidence: float) -> bool:
    """True if a scan's confidence is high enough for auto-learn."""
    return confidence >= AUTO_LEARN_CONF_THRESHOLD
