import os
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Tuple

import cv2
import numpy as np

from clipper import get_ffmpeg_path


CROP_W = 1080
CROP_H = 1920
_COLS = 32  # number of vertical strips for saliency analysis


def detect_subject_center_x(frame_bgr: np.ndarray, src_w: int, src_h: int) -> Optional[int]:
    """
    Estimates the horizontal center of the main visual subject using
    per-column saliency (variance of pixel values). Works with any OpenCV build.
    Returns the source-pixel X coordinate of the most salient column center,
    or None if the frame is uniform/empty.
    """
    if src_w <= 0 or src_h <= 0:
        return None

    # Downsample for speed
    small_w, small_h = min(src_w, 320), min(src_h, 180)
    small = cv2.resize(frame_bgr, (small_w, small_h))

    # Convert to float, compute per-column variance across rows
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).astype(np.float32)

    col_width = max(1, small_w // _COLS)
    saliency_scores = np.zeros(_COLS, dtype=np.float32)
    for i in range(_COLS):
        x0 = i * col_width
        x1 = min(x0 + col_width, small_w)
        strip = gray[:, x0:x1]
        if strip.size > 0:
            saliency_scores[i] = float(np.var(strip))

    # Gaussian-weight center bias slightly to avoid edges
    center_bias = np.exp(-0.5 * ((np.arange(_COLS) - _COLS / 2) / (_COLS * 0.45)) ** 2)
    weighted = saliency_scores * center_bias

    if weighted.max() < 10.0:  # uniform frame — no useful subject
        return None

    best_col = int(np.argmax(weighted))
    # Convert strip column → source pixel X
    strip_center_small = (best_col + 0.5) * col_width
    scale = src_w / small_w
    cx = int(strip_center_small * scale)
    return cx


def _smooth(values: List[float], alpha: float = 0.25) -> List[float]:
    """Exponential moving average smoothing."""
    if not values:
        return values
    smoothed = [values[0]]
    for v in values[1:]:
        smoothed.append(alpha * v + (1 - alpha) * smoothed[-1])
    return smoothed


def analyze_smart_crop(
    video_path: str,
    sample_interval: float = 0.5,
    start_time: float = 0.0,
    duration: float = 0.0,
    progress_cb=None,
) -> Tuple[List[Tuple[float, int]], int, int]:
    """
    Analyzes video frames every `sample_interval` seconds and returns:
      - keyframes: List of (timestamp_relative, crop_x_left) pairs
      - src_w, src_h: original video dimensions
    crop_x_left is the left-edge pixel of the 1080-wide crop window in source pixels.
    """
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {video_path}")

    src_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    src_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    vid_duration = total_frames / fps if fps > 0 else 0.0

    if duration <= 0:
        duration = vid_duration - start_time

    cap.release()

    if src_w == 0 or src_h == 0:
        raise RuntimeError("Cannot read video dimensions")

    if src_w <= CROP_W:
        default_x = max(0, (src_w - CROP_W) // 2)
        return [(0.0, default_x)], src_w, src_h

    center_x = src_w // 2
    timestamps = []
    t = 0.0
    while t <= duration:
        timestamps.append(t)
        t += sample_interval

    cap2 = cv2.VideoCapture(video_path)
    raw_cx: List[Optional[int]] = []
    total = len(timestamps)
    for i, ts in enumerate(timestamps):
        cap2.set(cv2.CAP_PROP_POS_MSEC, (start_time + ts) * 1000)
        ret, frame = cap2.read()
        if ret:
            cx = detect_subject_center_x(frame, src_w, src_h)
            raw_cx.append(cx)
        else:
            raw_cx.append(None)

        if progress_cb and total > 0:
            progress_cb(i / total)

    cap2.release()

    last = center_x
    filled: List[int] = []
    for v in raw_cx:
        last = v if v is not None else last
        filled.append(last)
    last = filled[-1]
    for i in reversed(range(len(filled))):
        if raw_cx[i] is None:
            filled[i] = last
        else:
            last = filled[i]

    smoothed_cx = _smooth([float(x) for x in filled], alpha=0.2)

    half = CROP_W // 2
    keyframes: List[Tuple[float, int]] = []
    for ts, cx in zip(timestamps, smoothed_cx):
        left = int(round(cx)) - half
        left = max(0, min(src_w - CROP_W, left))
        keyframes.append((ts, left))

    return keyframes, src_w, src_h


def build_crop_x_expr(keyframes: List[Tuple[float, int]]) -> str:
    """
    Builds a nested FFmpeg if(lt(t,...), x, ...) expression from keyframes.
    Piecewise constant (step function) crop X per timestamp.
    """
    if not keyframes:
        return "0"
    expr = str(keyframes[-1][1])
    for ts, x in reversed(keyframes[:-1]):
        expr = f"if(lt(t,{ts:.3f}),{x},{expr})"
    return expr


def render_smart_crop_clip(
    input_path: str,
    output_path: str,
    keyframes: List[Tuple[float, int]],
    src_w: int,
    src_h: int,
    start_time: float = 0.0,
    duration: float = 0.0,
    watermark_text: Optional[str] = None,
    hook_header_text: Optional[str] = None,
    subtitles_text: Optional[str] = None,
    bg_music_path: Optional[str] = None,
    bg_music_volume: float = 0.30,
    dialog_volume: float = 0.80,
    speed_factor: float = 1.03,
    preset: str = "veryfast",
    crf: int = 21,
    ffmpeg_binary: Optional[str] = None,
    font_path: Optional[str] = None,
) -> str:
    """
    Renders a 1080x1920 clip using per-frame smart crop that follows
    the detected character/subject's horizontal position.
    """
    from clipper import _escape_drawtext, _escape_filter_path

    input_p = Path(input_path).resolve()
    out_p = Path(output_path).resolve()
    out_p.parent.mkdir(parents=True, exist_ok=True)

    if not input_p.exists():
        raise FileNotFoundError(f"Input not found: {input_path}")

    ffmpeg_bin = ffmpeg_binary or get_ffmpeg_path()

    if not font_path:
        default_win_font = Path("C:/Windows/Fonts/arial.ttf")
        if default_win_font.exists():
            font_path = str(default_win_font)

    font_args = []
    if font_path and Path(font_path).exists():
        escaped_font = _escape_filter_path(font_path)
        font_args.append(f"fontfile='{escaped_font}'")
    else:
        font_args.append("font=Arial")
    font_str = ":".join(font_args)

    crop_x_expr = build_crop_x_expr(keyframes)

    if src_h < CROP_H:
        base_filter = f"[0:v]scale=-1:{CROP_H},crop={CROP_W}:{CROP_H}:({crop_x_expr}):0,setsar=1[v_base]"
    else:
        base_filter = f"[0:v]crop={CROP_W}:{CROP_H}:({crop_x_expr}):0,scale={CROP_W}:{CROP_H},setsar=1[v_base]"

    filter_chains = [base_filter]
    current_v = "[v_base]"

    if hook_header_text:
        esc = _escape_drawtext(hook_header_text)
        filter_chains.append(
            f"{current_v}drawtext={font_str}:text='{esc}':"
            f"fontsize=44:fontcolor=white:box=1:boxcolor=black@0.75:boxborderw=16:"
            f"x=(w-text_w)/2:y=220[v_hook]"
        )
        current_v = "[v_hook]"

    if subtitles_text:
        esc = _escape_drawtext(subtitles_text)
        filter_chains.append(
            f"{current_v}drawtext={font_str}:text='{esc}':"
            f"fontsize=46:fontcolor=yellow:borderw=4:bordercolor=black:"
            f"box=1:boxcolor=black@0.4:boxborderw=8:"
            f"x=(w-text_w)/2:y=h-360[v_sub]"
        )
        current_v = "[v_sub]"

    if watermark_text:
        esc = _escape_drawtext(watermark_text)
        filter_chains.append(
            f"{current_v}drawtext={font_str}:text='{esc}':"
            f"fontsize=36:fontcolor=white:box=1:boxcolor=black@0.65:boxborderw=10:"
            f"x=(w-text_w)/2:y=h-th-160[v_text]"
        )
        current_v = "[v_text]"

    filter_chains.append(f"{current_v}setpts=PTS/{speed_factor}[v_out]")

    has_music = bool(bg_music_path and Path(bg_music_path).exists())
    dur_arg = duration if duration > 0 else None
    if has_music:
        music_p = Path(bg_music_path).resolve()
        dur_str = f",atrim=0:{dur_arg:.3f}" if dur_arg else ""
        filter_chains.append(
            f"[0:a]atempo={speed_factor},volume={dialog_volume:.2f}[main_a];"
            f"[1:a]aloop=loop=-1:size=2e+09{dur_str},atempo={speed_factor},"
            f"volume={bg_music_volume:.2f}[music_a];"
            f"[main_a][music_a]amix=inputs=2:duration=first:dropout_transition=2[a_out]"
        )
    else:
        filter_chains.append(f"[0:a]volume={dialog_volume:.2f},atempo={speed_factor}[a_out]")

    filter_complex_str = ";".join(filter_chains)

    cmd = [ffmpeg_bin, "-y"]
    if start_time > 0:
        cmd += ["-ss", f"{start_time:.3f}"]
    if dur_arg:
        cmd += ["-t", f"{dur_arg:.3f}"]
    cmd += ["-i", str(input_p)]
    if has_music:
        cmd += ["-i", str(Path(bg_music_path).resolve())]
    cmd += [
        "-filter_complex", filter_complex_str,
        "-map", "[v_out]",
        "-map", "[a_out]",
        "-c:v", "libx264",
        "-preset", preset,
        "-crf", str(crf),
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "192k",
        "-movflags", "+faststart",
        str(out_p)
    ]

    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        subprocess.run(
            cmd, check=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace",
            creationflags=creationflags
        )
    except subprocess.CalledProcessError as err:
        raise RuntimeError(
            f"Smart crop render failed:\n{err.stderr[-600:] if err.stderr else str(err)}"
        ) from err

    return str(out_p)
