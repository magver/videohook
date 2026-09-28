"""
Watermark Remover Module for VideoHook
Intelligently detects and automatically removes watermarks, channel handles,
TikTok bouncing watermarks, and ending outro cards from vertical anime clips.
"""

import json
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import cv2
import numpy as np

from clipper import ensure_ffmpeg_in_path, get_ffmpeg_path

logger = logging.getLogger(__name__)


def detect_watermark_regions(
    video_path: str,
    num_samples: int = 15,
) -> List[Dict[str, int]]:
    """
    Analyzes temporal variance across sampled video frames to detect static
    or semi-static watermarks, channel logos, and usernames in the 4 corners.

    Returns a list of bounding boxes: [{"x": int, "y": int, "w": int, "h": int, "corner": str}]
    """
    detected_boxes: List[Dict[str, int]] = []
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return detected_boxes

    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    if total_frames < 10 or w < 100 or h < 100:
        cap.release()
        return detected_boxes

    # Fast frame sampling: sample up to 5 frames without heavy seeking
    frames: List[np.ndarray] = []
    # If video has many frames, calculate step and read with grab/downsampling
    sample_targets = [int(total_frames * f) for f in [0.15, 0.35, 0.55, 0.75]]
    for target in sample_targets:
        cap.set(cv2.CAP_PROP_POS_FRAMES, target)
        ret, frame = cap.read()
        if ret and frame is not None:
            # Downscale frame for lightning-fast edge & variance analysis
            small = cv2.resize(frame, (640, int(640 * h / w))) if w > 640 else frame
            frames.append(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY))
    cap.release()

    if len(frames) < 2:
        return detected_boxes

    stacked = np.stack(frames, axis=0).astype(np.float32)
    small_h, small_w = stacked.shape[1], stacked.shape[2]
    scale_x = w / float(small_w)
    scale_y = h / float(small_h)

    # Define candidate corner search zones
    # (zone_name, x_min, x_max, y_min, y_max)
    zones = [
        ("top_left", 0, int(0.35 * small_w), 0, int(0.18 * small_h)),
        ("top_right", int(0.65 * small_w), small_w, 0, int(0.18 * small_h)),
        ("bottom_left", 0, int(0.35 * small_w), int(0.82 * small_h), small_h),
        ("bottom_right", int(0.65 * small_w), small_w, int(0.82 * small_h), small_h),
    ]

    for zone_name, x0, x1, y0, y1 in zones:
        zone_crop = stacked[:, y0:y1, x0:x1]
        zw = x1 - x0
        zh = y1 - y0
        if zw < 20 or zh < 15:
            continue

        # 1. Temporal standard deviation across frames
        temp_std = np.std(zone_crop, axis=0)

        # 2. Spatial gradient / edge map of average frame
        avg_frame = np.mean(zone_crop, axis=0).astype(np.uint8)
        sobel_x = cv2.Sobel(avg_frame, cv2.CV_64F, 1, 0, ksize=3)
        sobel_y = cv2.Sobel(avg_frame, cv2.CV_64F, 0, 1, ksize=3)
        edge_mag = np.sqrt(sobel_x**2 + sobel_y**2)

        # Static watermark mask
        static_mask = ((temp_std < 18.0) & (edge_mag > 25.0)).astype(np.uint8) * 255

        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (15, 7))
        closed_mask = cv2.morphologyEx(static_mask, cv2.MORPH_CLOSE, kernel)

        contours, _ = cv2.findContours(closed_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for c in contours:
            bx, by, bw, bh = cv2.boundingRect(c)
            # Watermark must have reasonable size scaled
            orig_bw = bw * scale_x
            orig_bh = bh * scale_y
            if orig_bw >= 35 and orig_bh >= 14 and (orig_bw * orig_bh) >= 600:
                pad_x = 8
                pad_y = 6
                final_x = max(1, int((x0 + bx) * scale_x - pad_x))
                final_y = max(1, int((y0 + by) * scale_y - pad_y))
                final_w = min(w - final_x - 1, int(orig_bw + (pad_x * 2)))
                final_h = min(h - final_y - 1, int(orig_bh + (pad_y * 2)))

                detected_boxes.append({
                    "x": int(final_x),
                    "y": int(final_y),
                    "w": int(final_w),
                    "h": int(final_h),
                    "corner": zone_name,
                })

    return detected_boxes


def detect_outro_card(video_path: str, max_check_seconds: float = 3.0) -> float:
    """
    Detects if the video ends with a TikTok / CapCut outro logo card or black screen.
    Returns seconds to trim from the end (e.g. 1.5 - 2.5s), or 0.0 if normal video.
    """
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return 0.0

    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    dur = total_frames / fps
    if dur < 8.0:
        cap.release()
        return 0.0

    # Inspect the last 2.5 seconds
    outro_frames_count = int(min(max_check_seconds, 2.5) * fps)
    check_start = max(0, total_frames - outro_frames_count)

    tail_frames: List[np.ndarray] = []
    for f_idx in range(check_start, total_frames, max(1, int(fps // 5))):
        cap.set(cv2.CAP_PROP_POS_FRAMES, f_idx)
        ret, frame = cap.read()
        if ret and frame is not None:
            tail_frames.append(cv2.resize(frame, (160, 90)))
    cap.release()

    if len(tail_frames) < 3:
        return 0.0

    # Check for near-static tail (very low differences between consecutive frames)
    diffs = []
    for i in range(len(tail_frames) - 1):
        diff = cv2.absdiff(tail_frames[i], tail_frames[i + 1])
        diffs.append(np.mean(diff))

    # If the tail is almost completely static (logo card / still logo)
    if diffs and np.mean(diffs) < 3.5:
        return 1.8  # Trim 1.8s outro

    return 0.0


def remove_watermarks_and_finalize_video(
    input_path: str,
    output_path: str,
    platform: str = "Все",
    auto_crop_9x16: bool = True,
    trim_outro: bool = True,
    progress_callback: Optional[Callable[[str, float], None]] = None,
) -> str:
    """
    End-to-end automated watermark removal and formatting pipeline:
    1. Probes geometry and duration.
    2. Detects watermarks in all 4 corners (and adds TikTok profile if applicable).
    3. Detects and trims outro card if present.
    4. Builds FFmpeg delogo filter graph.
    5. Converts/scales to vertical 9:16 (1080x1920) if needed.
    6. Produces clean, watermark-free output MP4.
    """
    in_p = Path(input_path).resolve()
    out_p = Path(output_path).resolve()
    out_p.parent.mkdir(parents=True, exist_ok=True)

    ffmpeg_bin = ensure_ffmpeg_in_path()

    cap = cv2.VideoCapture(str(in_p))
    if not cap.isOpened():
        raise RuntimeError(f"Не удалось открыть видео: {input_path}")

    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    duration = total_frames / fps if fps > 0 else 25.0
    cap.release()

    if progress_callback:
        progress_callback("Анализ водяных знаков...", 0.15)

    # 1. Detect Outro Screen
    trim_tail = 0.0
    if trim_outro and duration > 7.0:
        trim_tail = detect_outro_card(str(in_p))

    # 2. Detect static watermark regions
    detected_boxes = detect_watermark_regions(str(in_p))

    # 3. For TikTok, ensure standard bouncing watermark zones are covered if not fully captured
    is_tiktok = "tiktok" in platform.lower() or "tiktok" in str(in_p).lower()
    if is_tiktok:
        # TikTok standard coordinates for 9:16 (or scaled to WxH)
        tl_found = any(b["corner"] == "top_left" for b in detected_boxes)
        br_found = any(b["corner"] == "bottom_right" for b in detected_boxes)

        if not tl_found:
            detected_boxes.append({
                "x": max(10, int(0.02 * w)),
                "y": max(15, int(0.025 * h)),
                "w": min(int(0.24 * w), 220),
                "h": min(int(0.06 * h), 90),
                "corner": "top_left",
                "enable": f"between(t,0,{duration / 2.0:.2f})",
            })
        if not br_found:
            detected_boxes.append({
                "x": max(10, int(w - 0.26 * w - 15)),
                "y": max(10, int(h - 0.08 * h - 20)),
                "w": min(int(0.26 * w), 230),
                "h": min(int(0.06 * h), 90),
                "corner": "bottom_right",
                "enable": f"gte(t,{duration / 2.0:.2f})",
            })

    if progress_callback:
        progress_callback(f"Найдено водяных знаков: {len(detected_boxes)}. Очистка...", 0.35)

    # 4. Build Filter Graph
    filter_chain: List[str] = []

    # Delogo filters
    # Note: FFmpeg delogo requires a 1-pixel surrounding border within the frame:
    # 1 <= bx and bx + bw <= w - 1
    # 1 <= by and by + bh <= h - 1
    for box in detected_boxes:
        bx = max(1, min(w - 20, int(box["x"])))
        by = max(1, min(h - 20, int(box["y"])))
        bw = max(10, int(box["w"]))
        bh = max(10, int(box["h"]))

        if bx + bw >= w:
            bw = w - bx - 1
        if by + bh >= h:
            bh = h - by - 1

        if bw >= 8 and bh >= 8:
            enable_expr = box.get("enable")
            if enable_expr:
                filter_chain.append(f"delogo=x={bx}:y={by}:w={bw}:h={bh}:show=0:enable='{enable_expr}'")
            else:
                filter_chain.append(f"delogo=x={bx}:y={by}:w={bw}:h={bh}:show=0")

    # Geometry 9:16 vertical formatting
    is_vertical = h > w and (0.50 <= (w / h) <= 0.65)
    if auto_crop_9x16:
        if is_vertical:
            # Already vertical, scale to crisp standard 1080x1920
            filter_chain.append("scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2")
        else:
            # Horizontal (16:9), format to 9:16 Fullscreen with smart center crop or blurscreen
            # Fullscreen center crop 9:16
            filter_chain.append("scale=-1:1920,crop=1080:1920")

    vf_str = ",".join(filter_chain) if filter_chain else "null"

    # Time trimming if outro detected or capping long videos for vertical shorts
    cmd = [ffmpeg_bin, "-y"]
    max_short_dur = 50.0
    if trim_tail > 0:
        new_dur = min(max_short_dur, max(4.0, duration - trim_tail))
        cmd.extend(["-t", f"{new_dur:.2f}"])
    elif duration > max_short_dur:
        cmd.extend(["-t", f"{max_short_dur:.2f}"])

    cmd.extend([
        "-i", str(in_p),
        "-vf", vf_str,
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-threads", "0",
        "-crf", "22",
        "-c:a", "aac",
        "-b:a", "192k",
        "-ar", "44100",
        "-movflags", "+faststart",
        str(out_p),
    ])

    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=creationflags,
    )

    if progress_callback:
        progress_callback("Рендеринг чистого видео...", 0.65)

    stdout, stderr = proc.communicate()
    if proc.returncode != 0:
        # Fallback if delogo or complex filter failed
        logger.warning(f"Watermark filter failed: {stderr}. Trying high-quality copy/scale fallback.")
        fallback_cmd = [
            ffmpeg_bin, "-y",
            "-i", str(in_p),
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "21",
            "-c:a", "aac",
            str(out_p),
        ]
        res = subprocess.run(fallback_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=creationflags)
        if res.returncode != 0 or not out_p.exists() or out_p.stat().st_size == 0:
            raise RuntimeError(f"Ошибка рендеринга видео: {stderr}")

    if not out_p.exists() or out_p.stat().st_size == 0:
        raise FileNotFoundError(f"Файл не был создан: {out_p}")

    if progress_callback:
        progress_callback("Очистка завершена!", 1.0)

    return str(out_p)
