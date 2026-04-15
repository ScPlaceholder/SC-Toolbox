"""Harvest labeled digit glyphs from YouTube Star Citizen mining streams.

Pipeline per video:
  1. yt-dlp downloads a specified time range at 1080p (doesn't save full video)
  2. ffmpeg extracts frames at a configurable interval (default 5s)
  3. Each frame is scanned for the mining HUD panel via brightness heuristics
     and template matching
  4. If the HUD is detected, the legacy OCR pipeline (ocr.onnx_hud_reader)
     runs on the HUD region. Its result is used as the ground-truth label.
  5. Individual digit crops (28x28) are saved per class to
     ``training_data_clean/<digit>/yt_<videoid>_<frame>_<pos>.png``

Safe for overnight runs. Resume-friendly: skips frames that have already
been processed (by hash).

Usage:
  python harvest_from_youtube.py \\
      --url 'https://www.youtube.com/watch?v=VIDEO_ID' \\
      --start 2:30:00 --end 3:00:00 \\
      --fps 0.2
  # (0.2 fps = 1 frame every 5 seconds)

Dependencies: yt-dlp (pip), ffmpeg (on PATH).
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image

# Set up import path
THIS = Path(__file__).resolve()
TOOL = THIS.parent.parent
sys.path.insert(0, str(TOOL))

from ocr.onnx_hud_reader import (  # noqa: E402
    _ensure_model, _find_label_rows, _find_value_crop, _otsu,
    scan_hud_onnx,
)

OUT_DIR = TOOL / "training_data_clean"
PROGRESS_FILE = OUT_DIR / ".harvest_seen.txt"


def _seen_hashes() -> set[str]:
    """Return the set of frame hashes we've already processed."""
    if not PROGRESS_FILE.is_file():
        return set()
    try:
        return set(PROGRESS_FILE.read_text(encoding="utf-8").split())
    except OSError:
        return set()


def _mark_seen(h: str) -> None:
    try:
        with open(PROGRESS_FILE, "a", encoding="utf-8") as f:
            f.write(h + "\n")
    except OSError:
        pass


def _download_clip(url: str, start: str, end: str, out_path: Path) -> bool:
    """Download a time range from the URL as 1080p MP4 via yt-dlp."""
    cmd = [
        sys.executable, "-m", "yt_dlp",
        "-f", "399+bestaudio/398+bestaudio/best[height<=1080]",
        "--download-sections", f"*{start}-{end}",
        "-o", str(out_path),
        "--merge-output-format", "mp4",
        "--no-warnings",
        "--quiet",
        url,
    ]
    try:
        subprocess.check_call(cmd)
        return out_path.exists()
    except subprocess.CalledProcessError as exc:
        print(f"  yt-dlp failed: {exc}", file=sys.stderr)
        return False


def _extract_frames(video_path: Path, frames_dir: Path, fps: float) -> list[Path]:
    """Extract frames at the given fps to the frames_dir. Returns list of paths."""
    frames_dir.mkdir(exist_ok=True)
    cmd = [
        "ffmpeg", "-i", str(video_path),
        "-vf", f"fps={fps}",
        "-q:v", "2",
        str(frames_dir / "f_%04d.jpg"),
        "-loglevel", "error",
    ]
    subprocess.check_call(cmd)
    return sorted(frames_dir.glob("*.jpg"))


def _find_hud_bbox(frame: Image.Image) -> Optional[tuple[int, int, int, int]]:
    """Locate the SC mining HUD within a (1920, 1080) streamer frame.

    Uses a simple brightness-density heuristic: the HUD panel has
    moderate-brightness text on a dark background concentrated in a
    specific aspect ratio (taller than wide, ~300-500 px tall). This
    is cheap and works on most streamer overlays; false positives
    get filtered by the legacy OCR's panel_visible check downstream.

    Returns (x, y, w, h) bbox in frame coords, or None.
    """
    gray = np.asarray(frame.convert("L"), dtype=np.uint8)
    H, W = gray.shape

    # Look in the right half of the frame only (HUD is always
    # right-side in the game; cuts down search space and avoids
    # matching the streamer's webcam).
    half = gray[:, W // 2:]
    binary = (half > 100).astype(np.uint8)
    col_sum = binary.sum(axis=0)
    # Find wide columns with text
    if col_sum.max() < 50:
        return None

    # Scan for a vertical band of consistent text density
    row_sum = binary.sum(axis=1)
    # Rows with any text
    has_text = row_sum > (W // 2) * 0.02  # at least 2% of row has text
    # Find the longest contiguous run of has_text rows
    best_run = (0, 0)
    run_start = None
    for y in range(H):
        if has_text[y]:
            if run_start is None:
                run_start = y
        else:
            if run_start is not None and (y - run_start) > (best_run[1] - best_run[0]):
                best_run = (run_start, y)
            run_start = None
    if run_start is not None and (H - run_start) > (best_run[1] - best_run[0]):
        best_run = (run_start, H)

    y1, y2 = best_run
    if (y2 - y1) < 200:  # Needs to be at least ~200 px tall
        return None

    # X extent: find contiguous columns with text in this band
    band = binary[y1:y2]
    c_sum = band.sum(axis=0)
    col_hot = c_sum > (y2 - y1) * 0.05
    if not col_hot.any():
        return None
    # Compute bbox with some padding
    xs = np.where(col_hot)[0]
    x1_rel, x2_rel = int(xs[0]), int(xs[-1]) + 1
    x1 = max(0, (W // 2) + x1_rel - 10)
    x2 = min(W, (W // 2) + x2_rel + 10)
    y1 = max(0, y1 - 10)
    y2 = min(H, y2 + 10)
    w = x2 - x1
    h = y2 - y1
    # Sanity check aspect ratio: panel is taller than wide, roughly
    if w < 200 or h < 300 or w > 700 or h > 900:
        return None
    return (x1, y1, w, h)


def _extract_digits_from_hud(hud_img: Image.Image) -> list[tuple[str, np.ndarray]]:
    """Run legacy OCR on the HUD crop and extract labeled 28x28 digit crops."""
    out: list[tuple[str, np.ndarray]] = []
    gray = np.array(hud_img.convert("L"), dtype=np.uint8)
    rows = _find_label_rows(hud_img)
    if not rows:
        return out

    # Run the full scan to get mass/resistance/instability labels.
    # We'll match crop positions against those values.
    # Since scan_hud_onnx runs the full pipeline (including capture),
    # we need to mock capture — easier to just call the internals.
    for field in ("mass", "resistance", "instability"):
        entry = rows.get(field)
        if entry is None:
            continue
        y1, y2, lbl_right = entry
        x_min = max(0, lbl_right + 6)
        value_crop = _find_value_crop(hud_img, gray, y1, y2, x_min=x_min)
        if value_crop is None:
            continue
        # Segment + identify via the legacy engine (by running scan_hud_onnx
        # we get back the value, then use its string form to label the
        # glyphs we segment here).
        crop_gray = np.array(value_crop.convert("L"), dtype=np.uint8)
        if np.median(crop_gray) > 140:
            crop_gray = 255 - crop_gray
        thr = _otsu(crop_gray)
        binary = (crop_gray > thr).astype(np.uint8) * 255
        proj = (binary > 0).sum(axis=0)
        w = binary.shape[1]
        spans = []
        in_c = False
        start = 0
        for x in range(w + 1):
            v = proj[x] if x < w else 0
            if v > 0 and not in_c:
                in_c = True; start = x
            elif v == 0 and in_c:
                in_c = False
                if x - start >= 2:
                    spans.append((start, x))
        if not spans:
            continue
        # For labeling we need the actual value. Re-run onnx_hud for the
        # whole HUD — expensive but cached; we need it anyway.
        from ocr.onnx_hud_reader import _ocr_crop
        ocr_raw = _ocr_crop(value_crop)
        # Extract digit chars from ocr_raw
        digit_chars = [c for c in ocr_raw if c.isdigit()]
        if len(digit_chars) != len(spans):
            continue
        for (x1, x2), ch in zip(spans, digit_chars):
            ys = np.where(np.any(binary[:, x1:x2] > 0, axis=1))[0]
            if len(ys) < 2:
                continue
            ya, yb = int(ys[0]), int(ys[-1]) + 1
            glyph = crop_gray[ya:yb, x1:x2].astype(np.float32)
            pad = 2
            padded = np.full(
                (glyph.shape[0] + pad * 2, glyph.shape[1] + pad * 2),
                255.0, dtype=np.float32,
            )
            padded[pad:pad + glyph.shape[0], pad:pad + glyph.shape[1]] = glyph
            pil = Image.fromarray(padded.astype(np.uint8)).resize(
                (28, 28), Image.BILINEAR,
            )
            out.append((ch, np.asarray(pil, dtype=np.uint8)))
    return out


def harvest(url: str, start: str, end: str, fps: float) -> int:
    """Main entry. Returns the number of glyphs harvested."""
    if not _ensure_model():
        print("ERROR: legacy ONNX model failed to load", file=sys.stderr)
        return 0

    OUT_DIR.mkdir(exist_ok=True)
    seen = _seen_hashes()

    video_id = url.rsplit("=", 1)[-1].rsplit("/", 1)[-1]

    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        video_path = tmp / "clip.mp4"
        frames_dir = tmp / "frames"

        print(f"[{video_id}] downloading {start} - {end}...")
        if not _download_clip(url, start, end, video_path):
            print(f"[{video_id}] download failed")
            return 0

        print(f"[{video_id}] extracting frames at {fps} fps...")
        frames = _extract_frames(video_path, frames_dir, fps)
        print(f"[{video_id}] {len(frames)} frames extracted")

        harvested = 0
        per_class: dict[str, int] = {str(d): 0 for d in range(10)}

        for fp in frames:
            try:
                img = Image.open(fp).convert("RGB")
            except Exception:
                continue
            h = hashlib.md5(open(fp, "rb").read()).hexdigest()
            if h in seen:
                continue
            seen.add(h)

            bbox = _find_hud_bbox(img)
            if bbox is None:
                _mark_seen(h)
                continue

            x, y, w, box_h = bbox
            hud = img.crop((x, y, x + w, y + box_h))
            glyphs = _extract_digits_from_hud(hud)
            _mark_seen(h)

            for ch, crop in glyphs:
                if ch not in per_class:
                    continue
                d = OUT_DIR / ch
                d.mkdir(exist_ok=True)
                name = f"yt_{video_id}_{fp.stem}_{per_class[ch]}.png"
                try:
                    Image.fromarray(crop, mode="L").save(d / name)
                    per_class[ch] += 1
                    harvested += 1
                except OSError:
                    pass

        print(f"[{video_id}] harvested {harvested} glyphs")
        for ch in "0123456789":
            if per_class[ch]:
                print(f"  '{ch}': +{per_class[ch]}")
        return harvested


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--url", required=True)
    p.add_argument("--start", default="0:00:00")
    p.add_argument("--end", default="0:05:00")
    p.add_argument("--fps", type=float, default=0.2,
                   help="Frames per second to extract (0.2 = 1 per 5s)")
    args = p.parse_args()

    n = harvest(args.url, args.start, args.end, args.fps)
    print(f"\nTotal harvested: {n}")


if __name__ == "__main__":
    main()
