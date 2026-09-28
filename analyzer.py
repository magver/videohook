import array
import math
import os
import re
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple

from clipper import get_ffmpeg_path


def get_video_duration(video_path: str, ffmpeg_bin: Optional[str] = None) -> float:
    """
    Extracts video duration in seconds via FFmpeg banner.
    """
    bin_path = ffmpeg_bin or get_ffmpeg_path()
    cmd = [
        bin_path,
        "-i", str(Path(video_path).resolve()),
        "-hide_banner"
    ]
    res = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace"
    )

    # Search for "Duration: HH:MM:SS.xx"
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", res.stderr)
    if match:
        hours = int(match.group(1))
        minutes = int(match.group(2))
        seconds = float(match.group(3))
        return hours * 3600 + minutes * 60 + seconds

    # Fallback to 0.0 if not found
    return 0.0


def detect_audio_peaks(
    video_path: str,
    clip_duration: float = 15.0,
    top_k: int = 3,
    skip_start: float = 90.0,
    skip_end: float = 90.0,
    min_distance: float = 45.0,
    ffmpeg_bin: Optional[str] = None,
) -> List[float]:
    """
    Fast, lightweight audio energy detector using downsampled 8kHz mono PCM
    and Python's native array module (zero heavy dependencies).

    Calculates RMS (Root Mean Square) energy over sliding windows,
    excludes opening (skip_start) and ending (skip_end), and returns the
    top_k peak moments using non-maximum suppression to prevent overlap.
    """
    bin_path = ffmpeg_bin or get_ffmpeg_path()
    total_duration = get_video_duration(video_path, bin_path)

    # If video is too short, return fallback intervals
    effective_duration = total_duration - skip_start - skip_end
    if effective_duration <= clip_duration:
        start = max(0.0, (total_duration - clip_duration) / 2)
        return [round(start, 2)]

    sample_rate = 8000  # 8kHz is plenty for RMS loudness envelope
    cmd = [
        bin_path,
        "-ss", str(skip_start),
        "-t", str(effective_duration),
        "-i", str(Path(video_path).resolve()),
        "-vn",
        "-ac", "1",
        "-ar", str(sample_rate),
        "-f", "s16le",
        "-"
    ]

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL
    )
    raw_data, _ = proc.communicate()

    if not raw_data:
        # Fallback to evenly spaced timestamps
        step = effective_duration / (top_k + 1)
        return [round(skip_start + step * (i + 1), 2) for i in range(top_k)]

    samples = array.array("h")
    samples.frombytes(raw_data)
    num_samples = len(samples)

    if num_samples == 0:
        step = effective_duration / (top_k + 1)
        return [round(skip_start + step * (i + 1), 2) for i in range(top_k)]

    window_samples = int(clip_duration * sample_rate)
    stride_samples = int(2.0 * sample_rate)  # evaluate every 2 seconds

    candidates: List[Tuple[float, float]] = []  # (rms, timestamp)

    # Slide window across samples
    for start_idx in range(0, num_samples - window_samples, stride_samples):
        window = samples[start_idx : start_idx + window_samples]
        # Sum of squares
        sum_sq = 0
        for s in window:
            sum_sq += s * s
        rms = math.sqrt(sum_sq / len(window))

        timestamp = skip_start + (start_idx / sample_rate)
        candidates.append((rms, timestamp))

    # Sort descending by RMS
    candidates.sort(key=lambda x: x[0], reverse=True)

    # Non-maximum suppression (ensure picked timestamps aren't clustered)
    selected_timestamps: List[float] = []
    for rms, ts in candidates:
        if len(selected_timestamps) >= top_k:
            break
        # Check distance from previously selected timestamps
        if all(abs(ts - existing) >= min_distance for existing in selected_timestamps):
            selected_timestamps.append(round(ts, 2))

    # Fallback if NMS pruned too aggressively
    if len(selected_timestamps) < top_k:
        for rms, ts in candidates:
            if len(selected_timestamps) >= top_k:
                break
            if all(abs(ts - existing) >= clip_duration for existing in selected_timestamps):
                selected_timestamps.append(round(ts, 2))

    selected_timestamps.sort()
    return selected_timestamps
