"""Per-scan diagnostic overlay for the panel finder.

The OCR pipeline pushes telemetry (HUD lines detected, mineral band,
row positions, value crops, lock state) into a module-level dict as
the scan progresses, then ``write()`` renders a single annotated PNG
showing every decision the panel finder made.

A live viewer (``scripts/live_panel_finder_viewer.py``) polls the
PNG every 400 ms so the user can watch the panel finder in real
time and see exactly where each crop came from.
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Any, Optional

from PIL import Image, ImageDraw

log = logging.getLogger(__name__)

# Compute an absolute output path so the overlay always lands in
# tools/Mining_Signals/ regardless of the toolbox's CWD. THIS file
# is at tools/Mining_Signals/ocr/sc_ocr/debug_overlay.py — two
# parent dirs gets us to tools/Mining_Signals/.
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_TOOL_DIR = os.path.normpath(os.path.join(_THIS_DIR, "..", ".."))
OUT_PATH = os.path.join(_TOOL_DIR, "debug_panel_overlay.png")

# ── Diagnostic-write gate ────────────────────────────────────────────────
# The OCR pipeline writes ~50 files per scan to populate live diagnostic
# viewers (Glyph Reader, Calibration Dialog, Panel Finder, Signature
# Finder). Most of the time NO viewer is open, but the writes still
# happen — pure waste. This heartbeat scheme lets each viewer announce
# "I'm watching" by touching a file every poll tick; the OCR pipeline
# then no-ops every diagnostic dump when the heartbeat is stale.
#
# Cross-process safe: viewers in scripts/ run as separate Python procs
# (launched via .bat files) and the heartbeat file is the only shared
# state. Crash-safe: if a viewer dies without removing the file, the
# mtime check naturally goes stale within HEARTBEAT_TTL_SEC.
_HEARTBEAT_FILE = os.path.join(_TOOL_DIR, "debug_glyphs", ".viewer_heartbeat")
HEARTBEAT_TTL_SEC = 5.0          # heartbeat older than this = no viewer
# Cache TTL had to come down: at 1.0 s, opening a viewer mid-scan could
# leave the gate's cached "False" stuck for a whole second after the
# viewer started touching the heartbeat — long enough to skip an entire
# overlay frame and surface as "no selection boxes". 0.2 s is short
# enough that a fresh heartbeat reaches the next dump call but long
# enough that the ~30 dump calls within a single scan still share one
# stat, so total per-scan stat overhead stays around 100 µs.
_DIAG_CACHE_TTL_SEC = 0.2
_diag_last_check: float = 0.0
_diag_last_result: bool = False


def viewer_heartbeat() -> None:
    """Called by every viewer's poll tick. Marks a heartbeat file with
    the current mtime so the OCR pipeline knows at least one viewer is
    watching and should write its diagnostic dumps.

    Also invalidates the in-process diagnostics_active() cache so a
    subsequent dump call this same scan picks up the fresh heartbeat
    instead of returning a stale False. Critical for the same-process
    viewers (calibration dialog, panel finder popout): without the
    invalidation they'd race against the cache for the first
    _DIAG_CACHE_TTL_SEC after open and miss an overlay frame.
    Cross-process viewers (scripts/) need the short TTL above instead.
    """
    global _diag_last_check, _diag_last_result
    try:
        os.makedirs(os.path.dirname(_HEARTBEAT_FILE), exist_ok=True)
        # Touching is enough — we only care about mtime. Use os.utime
        # against an empty file to avoid Windows-specific quirks of
        # Path.touch() on network drives.
        with open(_HEARTBEAT_FILE, "ab"):
            pass
        os.utime(_HEARTBEAT_FILE, None)
        # Force the next diagnostics_active() to re-stat. Same-process
        # viewers share this module's globals with the OCR pipeline, so
        # the next dump call sees True instantly.
        _diag_last_check = 0.0
        _diag_last_result = True
    except Exception:
        # Heartbeat is best-effort; failure just means dumps stay off.
        pass


def diagnostics_active() -> bool:
    """True if any viewer touched the heartbeat file within
    HEARTBEAT_TTL_SEC. Cached for _DIAG_CACHE_TTL_SEC so the ~30 dump
    calls per scan share a single os.stat."""
    global _diag_last_check, _diag_last_result
    import time as _time
    now = _time.monotonic()
    if (now - _diag_last_check) < _DIAG_CACHE_TTL_SEC:
        return _diag_last_result
    try:
        mtime = os.path.getmtime(_HEARTBEAT_FILE)
        result = (_time.time() - mtime) < HEARTBEAT_TTL_SEC
    except OSError:
        result = False
    _diag_last_check = now
    # Surface flips at INFO so the user can see in the log when the
    # gate opens / closes; helps diagnose "no boxes" symptoms.
    if result != _diag_last_result:
        log.info(
            "diagnostics_active flipped %s → %s (heartbeat=%s)",
            _diag_last_result, result, _HEARTBEAT_FILE,
        )
    _diag_last_result = result
    return result


# ── Per-tag heartbeat + capture-counter ──────────────────────────────────
# The legacy `viewer_heartbeat()` / `diagnostics_active()` pair is binary:
# any viewer alive = full ~50-files-per-scan dump. The tag-aware API
# below lets viewers declare WHICH dumps they actually need:
#
#   "crops"   — per-scan value crops (calibration dialog, live_crop_viewer)
#   "glyphs"  — per-glyph PNGs + voter JSON (glyph_reader_viewer)
#   "overlay" — annotated panel overlay PNG (panel finder viewers)
#
# Activation is OR over: legacy heartbeat (back-compat: legacy = all
# tags), per-tag heartbeat, and the force-capture counter. Cross-process
# scripts that haven't migrated keep working unchanged.
_capture_counter: int = 0
_capture_lock = threading.Lock()
_tag_cache: dict[str, tuple[float, bool]] = {}


def _tag_heartbeat_path(tag: str) -> str:
    return os.path.join(
        os.path.dirname(_HEARTBEAT_FILE), f".viewer_heartbeat_{tag}",
    )


def viewer_heartbeat_tag(tag: str) -> None:
    """Like viewer_heartbeat() but activates ONLY the given tag.

    Use this when only one diagnostic stream is needed instead of the
    full ~50-file-per-scan dump. Cheap to call every poll tick.
    """
    if not tag:
        return
    try:
        path = _tag_heartbeat_path(tag)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "ab"):
            pass
        os.utime(path, None)
        _tag_cache.pop(tag, None)
    except Exception:
        pass


def is_tag_active(tag: str) -> bool:
    """True if anyone is watching the given tag.

    OR over: force-capture counter, legacy heartbeat (= all tags), the
    per-tag heartbeat for ``tag``. The per-tag check is cached for
    _DIAG_CACHE_TTL_SEC so the dozens of save sites in a single scan
    share one os.stat per tag.
    """
    if _capture_active():
        return True
    if diagnostics_active():
        return True
    import time as _time
    now = _time.monotonic()
    cached = _tag_cache.get(tag)
    if cached is not None and (now - cached[0]) < _DIAG_CACHE_TTL_SEC:
        return cached[1]
    try:
        mtime = os.path.getmtime(_tag_heartbeat_path(tag))
        result = (_time.time() - mtime) < HEARTBEAT_TTL_SEC
    except OSError:
        result = False
    _tag_cache[tag] = (now, result)
    return result


def force_capture_next(n: int = 1) -> None:
    """Force the next N scans to write the FULL diagnostic dump,
    regardless of whether any viewer is registered.

    Powers the dialog's "📼 Record Next Scan" button. Each scan
    consumes one count via consume_capture_for_scan() at end of scan.
    Safe to call repeatedly — counter saturates at the requested value.
    """
    global _capture_counter
    with _capture_lock:
        _capture_counter = max(_capture_counter, int(n))


def consume_capture_for_scan() -> None:
    """Decrement the capture counter at the end of a scan.

    Called once per scan by the OCR pipeline after every save site has
    had a chance to fire. No-op when the counter is already 0.
    """
    global _capture_counter
    with _capture_lock:
        if _capture_counter > 0:
            _capture_counter -= 1


def _capture_active() -> bool:
    """True if the force-capture counter is currently positive."""
    with _capture_lock:
        return _capture_counter > 0


_state: dict[str, Any] = {}


def reset() -> None:
    _state.clear()


def set_image(img: Image.Image) -> None:
    _state["image"] = img.copy() if img is not None else None
    # Ping file: lets us tell from disk whether set_image() is being
    # called even when write() never produces an overlay PNG.
    try:
        with open(os.path.join(_TOOL_DIR, "debug_overlay_ping.txt"), "w") as f:
            import time as _time
            f.write(f"set_image called at {_time.time()}\n")
            f.write(f"image_size={img.size if img is not None else None}\n")
    except Exception:
        pass


def set_hud_lines(lines: list[tuple[int, int, int]]) -> None:
    _state["hud_lines"] = list(lines or [])


def set_panel_finder(
    top_y: Optional[int] = None,
    mineral_y_top: Optional[int] = None,
    mineral_y_bot: Optional[int] = None,
    mineral_center: Optional[int] = None,
    pitch: Optional[int] = None,
    bot_line_y: Optional[int] = None,
    source: str = "",
    title_box: Optional[tuple[int, int, int, int]] = None,
) -> None:
    """Push panel-finder telemetry for the debug overlay.

    ``title_box`` is the SCAN RESULTS title bounding rectangle
    ``(x, y, w, h)`` produced by the NCC anchor — drawing it makes
    row-mapping bugs immediately diagnosable: if the box is missing
    or in the wrong place, the off-by-one rows below it are an
    anchor-detection failure rather than an offset-math bug.
    """
    _state["panel_finder"] = {
        "top_line_y": top_y,
        "mineral_y_top": mineral_y_top,
        "mineral_y_bot": mineral_y_bot,
        "mineral_center": mineral_center,
        "pitch": pitch,
        "bot_line_y": bot_line_y,
        "source": source,  # "by_position" or "tesseract_fallback"
        "title_box": (
            tuple(int(v) for v in title_box) if title_box else None
        ),
    }


def set_label_rows(rows: dict[str, tuple[int, int, int]]) -> None:
    _state["label_rows"] = {
        k: {"y1": int(y1), "y2": int(y2), "label_right": int(lr)}
        for k, (y1, y2, lr) in (rows or {}).items()
    }


def set_value_crop(field: str, box: tuple[int, int, int, int]) -> None:
    _state.setdefault("value_crops", {})[field] = tuple(int(v) for v in box)


def set_lock(field: str, value: Optional[float], invalidated: bool = False) -> None:
    locks = _state.setdefault("locks", {})
    locks[field] = {"value": value, "invalidated": invalidated}


def set_ocr_text(field: str, text: str, confs: list[float]) -> None:
    _state.setdefault("ocr_text", {})[field] = {
        "text": text or "",
        "min_conf": min(confs) if confs else 0.0,
        "mean_conf": (sum(confs) / len(confs)) if confs else 0.0,
    }


def write() -> None:
    """Render and atomically save the annotated overlay PNG.

    Gated on the ``"overlay"`` tag specifically — no-ops when no
    viewer has pinged that tag (or the legacy heartbeat) recently.
    ``is_tag_active("overlay")`` ORs in ``diagnostics_active()`` so
    legacy callers of ``viewer_heartbeat()`` (cross-process scripts
    in ``scripts/``) continue to trigger writes.

    The annotated overlay PNG is the most expensive diagnostic write
    in the pipeline (large RGB image + multiple draw calls), so
    skipping it when nothing's watching is a major lag reduction.
    """
    if not is_tag_active("overlay"):
        return
    # Ping the "wrote" file unconditionally so we can tell from disk
    # whether write() is being reached even if image is None.
    try:
        with open(os.path.join(_TOOL_DIR, "debug_overlay_wrote.txt"), "w") as f:
            import time as _time
            f.write(f"write() called at {_time.time()}\n")
            f.write(f"state_keys={list(_state.keys())}\n")
            f.write(f"image_set={_state.get('image') is not None}\n")
    except Exception:
        pass
    img = _state.get("image")
    if img is None:
        return
    try:
        overlay = img.convert("RGB").copy()
        draw = ImageDraw.Draw(overlay)
        W, H = overlay.size

        # ── HUD separator lines (yellow) ──
        for line in _state.get("hud_lines", []):
            try:
                y, xl, xr = line
            except (TypeError, ValueError):
                continue
            draw.line([(xl, y), (xr, y)], fill=(255, 220, 0), width=2)
            draw.text((xr + 4, y - 6), "HUD", fill=(255, 220, 0))

        pf = _state.get("panel_finder", {})
        # ── SCAN RESULTS title box (gold, thick) ──
        # Drawn first so the row markers below render on top of its
        # outline, not behind it. Gold (255, 200, 0) was picked to
        # stand apart from the orange top/bot lines, the green mineral
        # band, and the cyan row bands — at a glance the user can
        # tell anchor-found-here from row-bands-here.
        title_box = pf.get("title_box")
        if title_box is not None:
            tx, ty, tw, th = title_box
            tx2 = min(W - 1, tx + tw)
            ty2 = min(H - 1, ty + th)
            draw.rectangle(
                [(tx, ty), (tx2, ty2)],
                outline=(255, 200, 0), width=3,
            )
            draw.text(
                (tx, max(0, ty - 12)),
                "SCAN RESULTS", fill=(255, 200, 0),
            )

        # ── Top line marker (orange) ──
        if pf.get("top_line_y") is not None:
            ty = pf["top_line_y"]
            draw.line([(0, ty), (W - 1, ty)], fill=(255, 140, 0), width=1)
            draw.text((4, ty + 1), "TOP_LINE", fill=(255, 140, 0))

        # ── Mineral name band (green) + OCR'd name ──
        if pf.get("mineral_y_top") is not None and pf.get("mineral_y_bot") is not None:
            mt, mb = pf["mineral_y_top"], pf["mineral_y_bot"]
            draw.rectangle([(0, mt), (W - 1, mb)], outline=(0, 230, 100), width=1)
            draw.text((4, mt - 11), "MINERAL", fill=(0, 230, 100))
            # If OCR has produced a mineral-name read, surface it on
            # the overlay so we can verify the read against the panel.
            _mineral_ocr = _state.get("ocr_text", {}).get("mineral")
            if _mineral_ocr:
                _label = f"→ {_mineral_ocr['text']}"
                draw.text((W // 2 - 60, mt - 11), _label, fill=(0, 230, 100))

        # ── Bottom line marker (orange) ──
        if pf.get("bot_line_y") is not None:
            by = pf["bot_line_y"]
            draw.line([(0, by), (W - 1, by)], fill=(255, 140, 0), width=1)
            draw.text((4, by - 11), "BOT_LINE", fill=(255, 140, 0))

        # ── Pitch annotation ──
        pitch = pf.get("pitch")
        source = pf.get("source", "")
        info = []
        if source:
            info.append(f"finder={source}")
        if pitch is not None:
            info.append(f"pitch={pitch}")
        if info:
            draw.text((4, 4), " | ".join(info), fill=(255, 255, 255))

        # ── Row bands (cyan) + value crops (magenta) + lock state ──
        rows = _state.get("label_rows", {})
        crops = _state.get("value_crops", {})
        locks = _state.get("locks", {})
        ocrs = _state.get("ocr_text", {})
        for field in ("mass", "resistance", "instability"):
            row = rows.get(field)
            if row is not None:
                y1, y2, lr = row["y1"], row["y2"], row["label_right"]
                draw.rectangle(
                    [(0, y1), (W - 1, y2)],
                    outline=(0, 200, 255), width=1,
                )
                draw.text((4, y1 + 1), field.upper(), fill=(0, 200, 255))
                # Mark the shared label_right (value-column-left anchor)
                draw.line([(lr, y1), (lr, y2)], fill=(255, 100, 100), width=1)

            crop = crops.get(field)
            if crop is not None:
                x1, vy1, x2, vy2 = crop
                draw.rectangle(
                    [(x1, vy1), (x2, vy2)],
                    outline=(255, 0, 255), width=2,
                )

            # Status string for this field
            status_parts = []
            ocr = ocrs.get(field)
            if ocr is not None:
                status_parts.append(
                    f"text={ocr['text']!r} mc={ocr['min_conf']:.2f}"
                )
            lock = locks.get(field)
            if lock is not None:
                if lock.get("invalidated"):
                    status_parts.append("LOCK_INVALIDATED")
                elif lock.get("value") is not None:
                    status_parts.append(f"LOCKED={lock['value']}")
            if status_parts and row is not None:
                ty = max(0, row["y1"] - 11)
                draw.text((W // 2 - 80, ty), " ".join(status_parts), fill=(255, 200, 0))

        # Atomic write: tmp + rename so the viewer never reads a half-written file.
        # ``format="PNG"`` is required because PIL infers the format from the
        # file extension — a ``.tmp`` suffix raises ``unknown file extension``
        # and silently leaves the overlay stuck on the import-time placeholder.
        tmp = OUT_PATH + ".tmp"
        overlay.save(tmp, format="PNG")
        try:
            os.replace(tmp, OUT_PATH)
        except OSError:
            # Windows can race; best-effort fallback
            overlay.save(OUT_PATH, format="PNG")
    except Exception as exc:
        # Warning (not debug) so a broken overlay pipeline surfaces in the
        # normal app log instead of needing DEBUG-level logging to diagnose.
        log.warning("debug_overlay.write failed: %s", exc)


def _self_test_write_placeholder() -> None:
    """Write a placeholder PNG on first import so the viewer immediately
    confirms the path + write permissions are working. Subsequent
    real scans overwrite this with actual data."""
    try:
        ph = Image.new("RGB", (400, 80), (30, 35, 45))
        d = ImageDraw.Draw(ph)
        d.text((10, 10), "debug_overlay placeholder", fill=(180, 180, 180))
        d.text((10, 30), f"path: {OUT_PATH}", fill=(120, 200, 120))
        d.text((10, 50), "waiting for first OCR scan...", fill=(120, 120, 120))
        ph.save(OUT_PATH)
    except Exception as exc:
        log.warning("debug_overlay placeholder write failed: %s", exc)


# Fire once at import time
_self_test_write_placeholder()
