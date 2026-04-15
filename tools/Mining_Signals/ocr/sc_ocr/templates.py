"""Template pack loading, caching, and EMA updates.

A "pack" is a .npz file with:
    chars   : (N,) uint32 — codepoint per template
    images  : (N, H, W) float32 — pre-normalized (zero-mean unit-L2)
    weights : (N,) int32 — EMA sample count per class
    meta    : json string (version, source, built_at, ...)

Shipped packs live in ``ocr/sc_templates/*.npz``.
User packs (learned from confirmed reads) live in
``%LOCALAPPDATA%/SC_Toolbox/sc_ocr/templates/*.npz``.

At load time a user pack shadows and merges with the shipped pack
of the same name — user-trained classes replace shipped defaults,
unknown classes (user added a new glyph the shipped pack didn't
have) are appended.

At scan time ``TemplatePack.at_height(h)`` returns an (N, h, h')
array resized to match the detected glyph height, cached in an
LRU for subsequent scans at the same scale.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from functools import lru_cache
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image

from .config import (
    AUTO_LEARN_CONF_THRESHOLD,
    CANON_TEMPLATE_H,
    CANON_TEMPLATE_W,
    EMA_SATURATION,
    TEMPLATE_WRITE_DEBOUNCE_S,
    TEMPLATES_DIR,
    USER_TEMPLATES_DIR,
    ensure_user_dirs,
)

log = logging.getLogger(__name__)


def _normalize(img: np.ndarray) -> np.ndarray:
    """Zero-mean, unit-L2 normalize. Degenerates to zero if blank."""
    t = img - img.mean()
    norm = np.sqrt((t * t).sum())
    if norm < 1e-6:
        return np.zeros_like(t)
    return t / norm


class TemplatePack:
    """Loaded template pack with learning + caching."""

    def __init__(self, name: str) -> None:
        """``name`` is the pack filename stem (e.g. ``'digits'``)."""
        self.name = name
        self._lock = threading.Lock()
        self._write_pending = False
        self._last_write_t = 0.0

        self._chars: np.ndarray    # (N,) uint32
        self._images: np.ndarray   # (N, H, W) float32 normalized
        self._weights: np.ndarray  # (N,) int32
        self._meta: dict

        self._load()

    # ── public API ─────────────────────────────────────────

    @property
    def chars(self) -> np.ndarray:
        return self._chars

    @property
    def chars_str(self) -> str:
        return "".join(chr(c) for c in self._chars)

    def at_height(self, h: int) -> np.ndarray:
        """Return templates resized to target height.

        LRU-cached per (self._load_counter, h) so repeated calls
        at the same height are free.
        """
        return self._at_height_cached(h, self._load_counter)

    def update_ema(self, char: str, new_normalized_crop: np.ndarray) -> bool:
        """Update a single template toward new_normalized_crop.

        ``new_normalized_crop`` must be zero-mean unit-L2 and have
        shape (H, W) matching the canonical template size.

        Returns True if the update was applied (even if the user
        template doesn't exist yet — it'll be created).
        """
        cp = ord(char)
        with self._lock:
            # Find existing slot
            idx = np.where(self._chars == cp)[0]
            if len(idx) == 0:
                # New class — append
                self._chars = np.concatenate(
                    [self._chars, np.asarray([cp], dtype=np.uint32)]
                )
                self._images = np.concatenate(
                    [self._images, new_normalized_crop[None, ...].astype(np.float32)],
                    axis=0,
                )
                self._weights = np.concatenate(
                    [self._weights, np.asarray([1], dtype=np.int32)]
                )
                self._bump_load_counter()
                self._schedule_write()
                return True

            i = int(idx[0])
            w = int(self._weights[i])
            alpha = 1.0 / min(w + 1, EMA_SATURATION)
            old = self._images[i]
            updated = (1.0 - alpha) * old + alpha * new_normalized_crop
            # Re-normalize after the EMA blend so NCC scores remain
            # comparable across classes.
            self._images[i] = _normalize(updated)
            self._weights[i] = w + 1
            self._bump_load_counter()
            self._schedule_write()
            return True

    def reset_user_overrides(self) -> None:
        """Delete the user pack and reload from shipped only."""
        user_path = USER_TEMPLATES_DIR / f"{self.name}.npz"
        try:
            if user_path.is_file():
                user_path.unlink()
        except OSError as exc:
            log.warning("sc_ocr: could not delete user pack %s: %s", user_path, exc)
        self._load()
        log.info("sc_ocr: reset user overrides for pack '%s'", self.name)

    # ── internals ──────────────────────────────────────────

    def _load(self) -> None:
        shipped_path = TEMPLATES_DIR / f"{self.name}.npz"
        user_path = USER_TEMPLATES_DIR / f"{self.name}.npz"

        if not shipped_path.is_file():
            raise FileNotFoundError(
                f"sc_ocr template pack '{self.name}' not found at {shipped_path}"
            )

        shipped = np.load(shipped_path, allow_pickle=False)
        chars = shipped["chars"].copy()
        images = shipped["images"].copy().astype(np.float32)
        weights = shipped["weights"].copy().astype(np.int32)
        meta_raw = shipped["meta"].item() if "meta" in shipped.files else "{}"
        try:
            meta = json.loads(meta_raw) if isinstance(meta_raw, str) else {}
        except Exception:
            meta = {}

        # Merge user pack if present: user entries OVERRIDE shipped,
        # new user entries (classes the shipped pack lacked) are
        # appended.
        if user_path.is_file():
            try:
                user = np.load(user_path, allow_pickle=False)
                u_chars = user["chars"]
                u_images = user["images"].astype(np.float32)
                u_weights = user["weights"].astype(np.int32)
                for i, cp in enumerate(u_chars):
                    mask = chars == cp
                    if mask.any():
                        j = int(np.argmax(mask))
                        images[j] = u_images[i]
                        weights[j] = u_weights[i]
                    else:
                        chars = np.concatenate([chars, np.asarray([cp], dtype=np.uint32)])
                        images = np.concatenate([images, u_images[i:i + 1]], axis=0)
                        weights = np.concatenate([weights, u_weights[i:i + 1]], axis=0)
                log.info("sc_ocr: merged user pack '%s' (%d classes)",
                         self.name, len(u_chars))
            except Exception as exc:
                log.warning("sc_ocr: user pack '%s' failed to load: %s",
                            self.name, exc)

        self._chars = chars
        self._images = images
        self._weights = weights
        self._meta = meta
        self._bump_load_counter()

    def _bump_load_counter(self) -> None:
        """Invalidate the LRU cache tied to (pack_name, height)."""
        # Use time_ns — guaranteed unique across invocations.
        self._load_counter = time.monotonic_ns()

    @lru_cache(maxsize=16)
    def _at_height_cached(self, h: int, counter: int) -> np.ndarray:
        """Resize the canonical templates to (h, h * CANON_W / CANON_H)."""
        if h == CANON_TEMPLATE_H:
            return self._images
        # Preserve aspect ratio
        w = max(1, round(h * CANON_TEMPLATE_W / CANON_TEMPLATE_H))
        resized = np.empty((self._images.shape[0], h, w), dtype=np.float32)
        for i, tpl in enumerate(self._images):
            # PIL resize expects uint8 range, but since templates are
            # zero-mean-unit-L2 the range is arbitrary. Rescale to
            # [0, 255] for a stable resize, then re-normalize after.
            mn, mx = tpl.min(), tpl.max()
            if mx - mn < 1e-6:
                resized[i] = 0
                continue
            rescaled = ((tpl - mn) / (mx - mn) * 255).astype(np.uint8)
            pil = Image.fromarray(rescaled, mode="L").resize((w, h), Image.LANCZOS)
            arr = np.asarray(pil, dtype=np.float32) / 255.0
            resized[i] = _normalize(arr)
        return resized

    def _schedule_write(self) -> None:
        """Debounced atomic write of user pack."""
        now = time.monotonic()
        # If we wrote recently, skip; a later update will flush.
        if (now - self._last_write_t) < TEMPLATE_WRITE_DEBOUNCE_S:
            self._write_pending = True
            return
        self._write_user_pack()

    def _write_user_pack(self) -> None:
        ensure_user_dirs()
        tmp = USER_TEMPLATES_DIR / f"{self.name}.npz.tmp"
        dst = USER_TEMPLATES_DIR / f"{self.name}.npz"
        try:
            np.savez_compressed(
                tmp,
                chars=self._chars,
                images=self._images,
                weights=self._weights,
                meta=json.dumps({**self._meta, "source": "user_ema"}),
            )
            tmp.replace(dst)
            self._last_write_t = time.monotonic()
            self._write_pending = False
        except OSError as exc:
            log.warning("sc_ocr: user pack write failed: %s", exc)


# ── Module-level cache of loaded packs ─────────────────────────────
_packs: dict[str, TemplatePack] = {}
_packs_lock = threading.Lock()


def get_pack(name: str) -> TemplatePack:
    """Return the singleton pack for ``name``, loading if needed."""
    with _packs_lock:
        pack = _packs.get(name)
        if pack is None:
            pack = TemplatePack(name)
            _packs[name] = pack
        return pack


def normalize_crop(crop_uint8: np.ndarray) -> np.ndarray:
    """Convert a uint8 glyph crop to the template normalization space.

    Output is float32 zero-mean unit-L2 at the same shape as input.
    """
    arr = crop_uint8.astype(np.float32) / 255.0
    return _normalize(arr)
