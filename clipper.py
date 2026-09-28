import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

# Global silent patch on Windows: prevent any black terminal / console popup
if os.name == "nt":
    _OrigPopen = subprocess.Popen

    class _SilentPopen(_OrigPopen):
        def __init__(self, *args, **kwargs):
            kwargs["creationflags"] = kwargs.get("creationflags", 0) | subprocess.CREATE_NO_WINDOW
            super().__init__(*args, **kwargs)

    subprocess.Popen = _SilentPopen


def get_ffmpeg_path() -> str:
    """
    Locates ffmpeg executable from system PATH, PyInstaller bundle, or imageio_ffmpeg fallback.
    """
    path = shutil.which("ffmpeg")
    if path:
        return path

    # Check PyInstaller bundle directory if packaged as exe
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        base_dir = Path(sys._MEIPASS)
        for cand in base_dir.glob("**/ffmpeg*.exe"):
            if cand.is_file():
                return str(cand)

    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        raise RuntimeError(
            "FFmpeg executable not found. Install ffmpeg on your system or run 'pip install imageio-ffmpeg'."
        )


def ensure_ffmpeg_in_path() -> str:
    """
    Ensures an executable named 'ffmpeg.exe' is discoverable in PATH
    for external tools like yt-dlp, and returns its absolute path.
    """
    existing = shutil.which("ffmpeg")
    if existing:
        return existing

    bin_str = get_ffmpeg_path()
    bin_p = Path(bin_str)

    if bin_p.name.lower() != "ffmpeg.exe":
        target_dir = bin_p.parent
        ffmpeg_exe = target_dir / "ffmpeg.exe"
        try:
            if not ffmpeg_exe.exists():
                shutil.copy2(bin_p, ffmpeg_exe)
            os.environ["PATH"] = str(target_dir) + os.path.pathsep + os.environ.get("PATH", "")
            return str(ffmpeg_exe)
        except Exception:
            cache_dir = Path("cache_clips").resolve()
            cache_dir.mkdir(parents=True, exist_ok=True)
            local_exe = cache_dir / "ffmpeg.exe"
            if not local_exe.exists():
                shutil.copy2(bin_p, local_exe)
            os.environ["PATH"] = str(cache_dir) + os.path.pathsep + os.environ.get("PATH", "")
            return str(local_exe)

    target_dir = bin_p.parent
    os.environ["PATH"] = str(target_dir) + os.path.pathsep + os.environ.get("PATH", "")
    return str(bin_p)


def _escape_filter_path(path: str) -> str:
    """
    Escapes file paths for use in FFmpeg filter strings, especially on Windows.
    Colons and backslashes must be escaped.
    """
    path_str = str(Path(path).resolve()).replace("\\", "/")
    return path_str.replace(":", "\\:")


def _escape_drawtext(text: str) -> str:
    """
    Escapes special characters in text strings for FFmpeg drawtext filter.
    """
    chars_to_escape = ["\\", "'", ":", "%", "[", "]"]
    escaped = text
    for ch in chars_to_escape:
        escaped = escaped.replace(ch, f"\\{ch}")
ASPECT_RATIOS: Dict[str, Tuple[int, int]] = {
    "9:16": (1080, 1920),
    "4:5": (1080, 1350),
    "1:1": (1080, 1080),
}


def get_aspect_dims(aspect_ratio: str = "9:16") -> Tuple[int, int]:
    """Returns (target_w, target_h) for the given aspect ratio string."""
    ar = str(aspect_ratio).lower().replace("х", ":").replace("x", ":").strip()
    for key, dims in ASPECT_RATIOS.items():
        if key in ar:
            return dims
    return 1080, 1920


def build_viral_video_filters(
    input_tag: str,
    viral_effects: Optional[Dict[str, bool]] = None,
    target_w: int = 1080,
    target_h: int = 1920,
) -> Tuple[List[str], str]:
    """
    Constructs viral video filters (TikTok/Reels/Instagram style):
    - zoom_punch: Attention-grabbing dynamic zoom in the first 1.5s
    - white_flash: Impactful white flash at start
    - vibrant_cc: Pop anime color grading (saturation 1.28, contrast 1.12)
    - vignette: Cinematic radial focus vignette
    - sharpen: Crisp HD edge enhancement
    Returns (filter_chains_list, final_output_tag).
    """
    chains: List[str] = []
    curr = input_tag
    if not viral_effects:
        return chains, curr

    tag_base = input_tag.strip("[]")

    # 1. Vibrant Color Grade (Anime Pop)
    if viral_effects.get("vibrant_cc"):
        nxt = f"[{tag_base}_vcc]"
        chains.append(f"{curr}eq=saturation=1.28:contrast=1.12:brightness=0.02{nxt}")
        curr = nxt

    # 2. Cinematic Vignette
    if viral_effects.get("vignette"):
        nxt = f"[{tag_base}_vig]"
        chains.append(f"{curr}vignette=angle=PI/4{nxt}")
        curr = nxt

    # 3. HD Sharpening
    if viral_effects.get("sharpen"):
        nxt = f"[{tag_base}_shp]"
        chains.append(f"{curr}unsharp=5:5:0.8:5:5:0.0{nxt}")
        curr = nxt

    # 4. White Flash
    if viral_effects.get("white_flash"):
        nxt = f"[{tag_base}_fls]"
        chains.append(f"{curr}fade=t=in:st=0:d=0.25:color=white{nxt}")
        curr = nxt

    # 5. Dynamic Zoom-Punch (Hook Zoom)
    if viral_effects.get("zoom_punch"):
        nxt = f"[{tag_base}_zmp]"
        # Smooth ease-in zoom from 1.14 to 1.0 in first 45 frames (1.5s at 30fps)
        chains.append(
            f"{curr}zoompan=z='if(lte(on,45),1.0+0.14*(1-on/45),1.0)':d=1:"
            f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={target_w}x{target_h}:fps=30{nxt}"
        )
        curr = nxt

    return chains, curr


def get_viral_audio_filter(viral_effects: Optional[Dict[str, bool]] = None) -> str:
    """Returns FFmpeg audio filter string for viral audio effects (e.g. Bass Boost)."""
    if viral_effects and viral_effects.get("bass_boost"):
        return "equalizer=f=60:width_type=h:width=60:g=4,equalizer=f=120:width_type=h:width=80:g=3"
    return ""


def render_vertical_clip(
    input_path: str,
    output_path: str,
    start_time: float,
    duration: float = 15.0,
    layout_mode: str = "fullscreen",  # "fullscreen" or "blurscreen"
    aspect_ratio: str = "9:16",       # "9:16", "4:5", "1:1"
    viral_effects: Optional[Dict[str, bool]] = None,
    watermark_text: Optional[str] = "Смотри в приложении",
    hook_header_text: Optional[str] = None,
    subtitles_text: Optional[str] = None,
    bg_music_path: Optional[str] = None,
    bg_music_volume: float = 0.20,
    speed_factor: float = 1.03,
    font_path: Optional[str] = None,
    crf: int = 20,
    preset: str = "veryfast",
    ffmpeg_binary: Optional[str] = None,
) -> str:
    """
    Renders a viral short with selectable aspect ratio (9:16, 4:5, 1:1),
    TikTok/Reels viral effects, fullscreen smart crop (or blurscreen),
    background music mixing, Russian subtitles, top viral hook header, and anti-Content ID speed shift.
    """
    input_p = Path(input_path).resolve()
    if not input_p.exists():
        raise FileNotFoundError(f"Input video file not found: {input_path}")

    out_p = Path(output_path).resolve()
    out_p.parent.mkdir(parents=True, exist_ok=True)

    ffmpeg_bin = ffmpeg_binary or ensure_ffmpeg_in_path()
    target_w, target_h = get_aspect_dims(aspect_ratio)
    quarter_w = ((target_w // 4) // 2) * 2
    quarter_h = ((target_h // 4) // 2) * 2

    # Determine default font on Windows if none provided
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

    filter_chains = []

    # 1. Base Video Layout (Fullscreen vs Blurscreen)
    if layout_mode == "blurscreen":
        bg_filter = (
            f"[0:v]split=2[fg_in][bg_in];"
            f"[bg_in]scale={quarter_w}:{quarter_h}:force_original_aspect_ratio=increase,"
            f"crop={quarter_w}:{quarter_h},"
            f"boxblur=luma_radius=8:luma_power=2,"
            f"scale={target_w}:{target_h}[bg];"
            f"[fg_in]scale={target_w}:-1[fg];"
            f"[bg][fg]overlay=0:(H-h)/2[v_base]"
        )
        filter_chains.append(bg_filter)
    else:
        fs_filter = f"[0:v]scale={target_w}:{target_h}:force_original_aspect_ratio=increase,crop={target_w}:{target_h},setsar=1[v_base]"
        filter_chains.append(fs_filter)

    # Apply viral video filters (TikTok/Reels/Instagram)
    viral_chains, current_v = build_viral_video_filters("[v_base]", viral_effects, target_w, target_h)
    filter_chains.extend(viral_chains)

    # Dynamic positioning and font sizing based on target_h
    hook_y = int(target_h * 0.11)
    sub_y = int(target_h * 0.78)
    wm_y = int(target_h * 0.90)
    hook_fs = max(32, int(44 * (target_h / 1920)))
    sub_fs = max(28, int(46 * (target_h / 1920)))
    wm_fs = max(22, int(36 * (target_h / 1920)))

    # 2. Top Viral Hook Header
    if hook_header_text:
        escaped_hook = _escape_drawtext(hook_header_text)
        hook_filter = (
            f"{current_v}drawtext={font_str}:text='{escaped_hook}':"
            f"fontsize={hook_fs}:fontcolor=white:box=1:boxcolor=black@0.75:boxborderw=16:"
            f"x=(w-text_w)/2:y={hook_y}[v_hook]"
        )
        filter_chains.append(hook_filter)
        current_v = "[v_hook]"

    # 3. Russian Subtitles / Captions (lower-center)
    if subtitles_text:
        escaped_sub = _escape_drawtext(subtitles_text)
        sub_filter = (
            f"{current_v}drawtext={font_str}:text='{escaped_sub}':"
            f"fontsize={sub_fs}:fontcolor=yellow:borderw=4:bordercolor=black:"
            f"box=1:boxcolor=black@0.4:boxborderw=8:"
            f"x=(w-text_w)/2:y={sub_y}[v_sub]"
        )
        filter_chains.append(sub_filter)
        current_v = "[v_sub]"

    # 4. Bottom CTA Watermark
    if watermark_text:
        escaped_text = _escape_drawtext(watermark_text)
        drawtext_filter = (
            f"{current_v}drawtext={font_str}:text='{escaped_text}':"
            f"fontsize={wm_fs}:fontcolor=white:box=1:boxcolor=black@0.65:boxborderw=10:"
            f"x=(w-text_w)/2:y={wm_y}[v_text]"
        )
        filter_chains.append(drawtext_filter)
        current_v = "[v_text]"

    # 5. Video speed modification (1.03x anti-hash)
    v_speed_filter = f"{current_v}setpts=PTS/{speed_factor}[v_out]"
    filter_chains.append(v_speed_filter)

    # 6. Audio stream processing & Background music mixing
    viral_a_filter = get_viral_audio_filter(viral_effects)
    has_music = bg_music_path and Path(bg_music_path).exists()
    if has_music:
        music_p = Path(bg_music_path).resolve()
        a_filter = (
            f"[0:a]atempo={speed_factor},volume=1.0[main_a];"
            f"[1:a]aloop=loop=-1:size=2e+09,atrim=0:{duration},atempo={speed_factor},"
            f"volume={bg_music_volume}[music_a];"
            f"[main_a][music_a]amix=inputs=2:duration=first:dropout_transition=2[mixed_a]"
        )
        if viral_a_filter:
            a_filter += f";[mixed_a]{viral_a_filter}[a_out]"
        else:
            a_filter += f";[mixed_a]anull[a_out]"
        filter_chains.append(a_filter)
    else:
        if viral_a_filter:
            a_speed_filter = f"[0:a]atempo={speed_factor},{viral_a_filter}[a_out]"
        else:
            a_speed_filter = f"[0:a]atempo={speed_factor}[a_out]"
        filter_chains.append(a_speed_filter)

    filter_complex_str = ";".join(filter_chains)

    cmd = [
        ffmpeg_bin,
        "-y",
        "-ss", f"{start_time:.3f}",
        "-t", f"{duration:.3f}",
        "-i", str(input_p),
    ]

    if has_music:
        cmd.extend(["-i", str(music_p)])

    cmd.extend([
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
    ])

    try:
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        result = subprocess.run(
            cmd,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=creationflags
        )
    except subprocess.CalledProcessError as err:
        err_msg = err.stderr if err.stderr else str(err)
        raise RuntimeError(f"FFmpeg rendering failed with exit code {err.returncode}:\n{err_msg}") from err

    return str(out_p)


def render_multi_segment_clip(
    input_path: str,
    output_path: str,
    segments: List[Tuple[float, float]],
    layout_mode: str = "fullscreen",
    aspect_ratio: str = "9:16",
    viral_effects: Optional[Dict[str, bool]] = None,
    watermark_text: Optional[str] = "Смотри в профиле",
    hook_header_text: Optional[str] = None,
    subtitles_text: Optional[str] = None,
    bg_music_path: Optional[str] = None,
    bg_music_volume: float = 0.30,
    speed_factor: float = 1.03,
    preset: str = "veryfast",
    crf: int = 21,
    ffmpeg_binary: Optional[str] = None,
    font_path: Optional[str] = None,
) -> str:
    """
    Renders a clip composed of multiple trimmed segments concatenated together.
    Supports aspect ratios (9:16, 4:5, 1:1) and TikTok/Reels viral effects.
    """
    input_p = Path(input_path).resolve()
    out_p = Path(output_path).resolve()
    out_p.parent.mkdir(parents=True, exist_ok=True)

    if not input_p.exists():
        raise FileNotFoundError(f"Input video file not found: {input_path}")

    # Fallback to single segment if 0 or 1 segment provided
    valid_segments = [(max(0.0, s), max(s + 0.1, e)) for (s, e) in segments if e > s]
    if not valid_segments:
        return render_vertical_clip(
            input_path=input_path, output_path=output_path, start_time=0.0, duration=15.0,
            layout_mode=layout_mode, aspect_ratio=aspect_ratio, viral_effects=viral_effects,
            watermark_text=watermark_text, hook_header_text=hook_header_text,
            subtitles_text=subtitles_text, bg_music_path=bg_music_path, bg_music_volume=bg_music_volume,
            speed_factor=speed_factor, preset=preset, crf=crf, ffmpeg_binary=ffmpeg_binary, font_path=font_path
        )
    if len(valid_segments) == 1:
        s, e = valid_segments[0]
        return render_vertical_clip(
            input_path=input_path, output_path=output_path, start_time=s, duration=e - s,
            layout_mode=layout_mode, aspect_ratio=aspect_ratio, viral_effects=viral_effects,
            watermark_text=watermark_text, hook_header_text=hook_header_text,
            subtitles_text=subtitles_text, bg_music_path=bg_music_path, bg_music_volume=bg_music_volume,
            speed_factor=speed_factor, preset=preset, crf=crf, ffmpeg_binary=ffmpeg_binary, font_path=font_path
        )

    ffmpeg_bin = ffmpeg_binary or get_ffmpeg_path()
    total_duration = sum(e - s for s, e in valid_segments)
    target_w, target_h = get_aspect_dims(aspect_ratio)
    quarter_w = ((target_w // 4) // 2) * 2
    quarter_h = ((target_h // 4) // 2) * 2

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

    filter_chains = []

    # 1. Trim filters for each segment
    for i, (s, e) in enumerate(valid_segments):
        filter_chains.append(f"[0:v]trim=start={s:.3f}:end={e:.3f},setpts=PTS-STARTPTS[v{i}]")
        filter_chains.append(f"[0:a]atrim=start={s:.3f}:end={e:.3f},asetpts=PTS-STARTPTS[a{i}]")

    concat_inputs = "".join(f"[v{i}][a{i}]" for i in range(len(valid_segments)))
    filter_chains.append(f"{concat_inputs}concat=n={len(valid_segments)}:v=1:a=1[v_cat][a_cat]")

    # 2. Geometry layout
    if layout_mode == "blurscreen":
        bg_filter = (
            f"[v_cat]split=2[fg_in][bg_in];"
            f"[bg_in]scale={quarter_w}:{quarter_h}:force_original_aspect_ratio=increase,"
            f"crop={quarter_w}:{quarter_h},"
            f"boxblur=luma_radius=8:luma_power=2,"
            f"scale={target_w}:{target_h}[bg];"
            f"[fg_in]scale={target_w}:-1[fg];"
            f"[bg][fg]overlay=0:(H-h)/2[v_base]"
        )
        filter_chains.append(bg_filter)
    else:
        fs_filter = f"[v_cat]scale={target_w}:{target_h}:force_original_aspect_ratio=increase,crop={target_w}:{target_h},setsar=1[v_base]"
        filter_chains.append(fs_filter)

    # Apply viral video filters (TikTok/Reels/Instagram)
    viral_chains, current_v = build_viral_video_filters("[v_base]", viral_effects, target_w, target_h)
    filter_chains.extend(viral_chains)

    # Dynamic positioning and font sizing based on target_h
    hook_y = int(target_h * 0.11)
    sub_y = int(target_h * 0.78)
    wm_y = int(target_h * 0.90)
    hook_fs = max(32, int(44 * (target_h / 1920)))
    sub_fs = max(28, int(46 * (target_h / 1920)))
    wm_fs = max(22, int(36 * (target_h / 1920)))

    # 3. Top Viral Hook Header
    if hook_header_text:
        escaped_hook = _escape_drawtext(hook_header_text)
        hook_filter = (
            f"{current_v}drawtext={font_str}:text='{escaped_hook}':"
            f"fontsize={hook_fs}:fontcolor=white:box=1:boxcolor=black@0.75:boxborderw=16:"
            f"x=(w-text_w)/2:y={hook_y}[v_hook]"
        )
        filter_chains.append(hook_filter)
        current_v = "[v_hook]"

    # 4. Russian Subtitles
    if subtitles_text:
        escaped_sub = _escape_drawtext(subtitles_text)
        sub_filter = (
            f"{current_v}drawtext={font_str}:text='{escaped_sub}':"
            f"fontsize={sub_fs}:fontcolor=yellow:borderw=4:bordercolor=black:"
            f"box=1:boxcolor=black@0.4:boxborderw=8:"
            f"x=(w-text_w)/2:y={sub_y}[v_sub]"
        )
        filter_chains.append(sub_filter)
        current_v = "[v_sub]"

    # 5. Bottom CTA Watermark
    if watermark_text:
        escaped_text = _escape_drawtext(watermark_text)
        drawtext_filter = (
            f"{current_v}drawtext={font_str}:text='{escaped_text}':"
            f"fontsize={wm_fs}:fontcolor=white:box=1:boxcolor=black@0.65:boxborderw=10:"
            f"x=(w-text_w)/2:y={wm_y}[v_text]"
        )
        filter_chains.append(drawtext_filter)
        current_v = "[v_text]"

    # 6. Video speed modification (1.03x)
    v_speed_filter = f"{current_v}setpts=PTS/{speed_factor}[v_out]"
    filter_chains.append(v_speed_filter)

    # 7. Audio stream processing & Background music
    viral_a_filter = get_viral_audio_filter(viral_effects)
    has_music = bg_music_path and Path(bg_music_path).exists()
    if has_music:
        music_p = Path(bg_music_path).resolve()
        a_filter = (
            f"[a_cat]atempo={speed_factor},volume=1.0[main_a];"
            f"[1:a]aloop=loop=-1:size=2e+09,atrim=0:{total_duration:.3f},atempo={speed_factor},"
            f"volume={bg_music_volume}[music_a];"
            f"[main_a][music_a]amix=inputs=2:duration=first:dropout_transition=2[mixed_a]"
        )
        if viral_a_filter:
            a_filter += f";[mixed_a]{viral_a_filter}[a_out]"
        else:
            a_filter += f";[mixed_a]anull[a_out]"
        filter_chains.append(a_filter)
    else:
        if viral_a_filter:
            a_speed_filter = f"[a_cat]atempo={speed_factor},{viral_a_filter}[a_out]"
        else:
            a_speed_filter = f"[a_cat]atempo={speed_factor}[a_out]"
        filter_chains.append(a_speed_filter)

    filter_complex_str = ";".join(filter_chains)

    cmd = [
        ffmpeg_bin,
        "-y",
        "-i", str(input_p),
    ]

    if has_music:
        cmd.extend(["-i", str(music_p)])

    cmd.extend([
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
    ])

    try:
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        result = subprocess.run(
            cmd,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=creationflags
        )
    except subprocess.CalledProcessError as err:
        err_msg = err.stderr if err.stderr else str(err)
        raise RuntimeError(f"FFmpeg multi-segment rendering failed with exit code {err.returncode}:\n{err_msg}") from err

    return str(out_p)


def render_final(
    input_path: str,
    output_path: str,
    segments: List[Tuple[float, float]],
    music_path: Optional[str] = None,
    music_vol: float = 0.28,
    dialog_vol: float = 1.0,
    hook_text: Optional[str] = None,
    subtitle_text: Optional[str] = None,
    watermark_text: Optional[str] = "Смотри в профиле",
    crop_mode: str = "fullscreen",
    aspect_ratio: str = "9:16",
    viral_effects: Optional[Dict[str, bool]] = None,
    speed: float = 1.03,
    preset: str = "veryfast",
    crf: int = 21,
    font_path: Optional[str] = None,
    ffmpeg_binary: Optional[str] = None,
    progress_callback: Optional[Callable[[float], None]] = None,
) -> str:
    """
    Final video render pipeline implementing Stage 1A with TikTok/Reels viral effects
    and selectable aspect ratio (9:16, 4:5, 1:1).
    1. Trims each segment (in/out)
    2. Merges (concat) all trimmed segments
    3. Formats to target aspect ratio (fullscreen crop or blurscreen)
    4. Applies TikTok/Reels viral effects (zoom punch, flash, pop CC, vignette, sharpen)
    5. Mixes dialogue audio and background music via amix + optional bass boost
    6. Overlays hook header, subtitles, and watermark scaled to aspect ratio
    7. Applies speed factor adjustment (1.03x anti-hash)
    8. Parses FFmpeg stderr in real-time to report progress
    """
    input_p = Path(input_path).resolve()
    out_p = Path(output_path).resolve()
    out_p.parent.mkdir(parents=True, exist_ok=True)

    if not input_p.exists():
        raise FileNotFoundError(f"Input video file not found: {input_path}")

    valid_segments = [(max(0.0, s), max(s + 0.1, e)) for (s, e) in segments if e > s]
    if not valid_segments:
        valid_segments = [(0.0, 15.0)]

    total_duration = sum(e - s for s, e in valid_segments)
    expected_out_duration = max(0.1, total_duration / speed)

    ffmpeg_bin = ffmpeg_binary or get_ffmpeg_path()
    target_w, target_h = get_aspect_dims(aspect_ratio)
    quarter_w = ((target_w // 4) // 2) * 2
    quarter_h = ((target_h // 4) // 2) * 2

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

    filter_chains = []

    # 1. Trim filters for each segment
    for i, (s, e) in enumerate(valid_segments):
        filter_chains.append(f"[0:v]trim=start={s:.3f}:end={e:.3f},setpts=PTS-STARTPTS[v{i}]")
        filter_chains.append(f"[0:a]atrim=start={s:.3f}:end={e:.3f},asetpts=PTS-STARTPTS[a{i}]")

    concat_inputs = "".join(f"[v{i}][a{i}]" for i in range(len(valid_segments)))
    filter_chains.append(f"{concat_inputs}concat=n={len(valid_segments)}:v=1:a=1[v_cat][a_cat]")

    # 2. Geometry layout (Fullscreen vs Blurscreen)
    if crop_mode == "blurscreen":
        bg_filter = (
            f"[v_cat]split=2[fg_in][bg_in];"
            f"[bg_in]scale={quarter_w}:{quarter_h}:force_original_aspect_ratio=increase,"
            f"crop={quarter_w}:{quarter_h},"
            f"boxblur=luma_radius=8:luma_power=2,"
            f"scale={target_w}:{target_h}[bg];"
            f"[fg_in]scale={target_w}:-1[fg];"
            f"[bg][fg]overlay=0:(H-h)/2[v_base]"
        )
        filter_chains.append(bg_filter)
    else:
        fs_filter = f"[v_cat]scale={target_w}:{target_h}:force_original_aspect_ratio=increase,crop={target_w}:{target_h},setsar=1[v_base]"
        filter_chains.append(fs_filter)

    # Apply viral video filters (TikTok/Reels/Instagram)
    viral_chains, current_v = build_viral_video_filters("[v_base]", viral_effects, target_w, target_h)
    filter_chains.extend(viral_chains)

    # Dynamic positioning and font sizing based on target_h
    hook_y = int(target_h * 0.11)
    sub_y = int(target_h * 0.78)
    wm_y = int(target_h * 0.90)
    hook_fs = max(32, int(48 * (target_h / 1920)))
    sub_fs = max(28, int(46 * (target_h / 1920)))
    wm_fs = max(22, int(36 * (target_h / 1920)))

    # 3. Top Viral Hook Header
    if hook_text:
        escaped_hook = _escape_drawtext(hook_text)
        hook_filter = (
            f"{current_v}drawtext={font_str}:text='{escaped_hook}':"
            f"fontsize={hook_fs}:fontcolor=white:box=1:boxcolor=black@0.75:boxborderw=16:"
            f"x=(w-text_w)/2:y={hook_y}[v_hook]"
        )
        filter_chains.append(hook_filter)
        current_v = "[v_hook]"

    # 4. Russian Subtitles
    if subtitle_text:
        escaped_sub = _escape_drawtext(subtitle_text)
        sub_filter = (
            f"{current_v}drawtext={font_str}:text='{escaped_sub}':"
            f"fontsize={sub_fs}:fontcolor=yellow:borderw=4:bordercolor=black:"
            f"box=1:boxcolor=black@0.4:boxborderw=8:"
            f"x=(w-text_w)/2:y={sub_y}[v_sub]"
        )
        filter_chains.append(sub_filter)
        current_v = "[v_sub]"

    # 5. Bottom CTA Watermark
    if watermark_text:
        escaped_text = _escape_drawtext(watermark_text)
        drawtext_filter = (
            f"{current_v}drawtext={font_str}:text='{escaped_text}':"
            f"fontsize={wm_fs}:fontcolor=white:box=1:boxcolor=black@0.65:boxborderw=10:"
            f"x=(w-text_w)/2:y={wm_y}[v_text]"
        )
        filter_chains.append(drawtext_filter)
        current_v = "[v_text]"

    # 6. Video speed modification (anti-hash)
    v_speed_filter = f"{current_v}setpts=PTS/{speed:.4f}[v_out]"
    filter_chains.append(v_speed_filter)

    # 7. Audio stream processing & Background music mixing
    viral_a_filter = get_viral_audio_filter(viral_effects)
    has_music = bool(music_path and Path(music_path).exists())
    if has_music:
        music_p = Path(music_path).resolve()
        # [a_cat] volume -> [main_a]; [1:a] loop, trim, volume -> [music_a]; amix -> [mixed_a]; atempo -> [a_out]
        a_filter = (
            f"[a_cat]volume={dialog_vol:.2f}[main_a];"
            f"[1:a]aloop=loop=-1:size=2e+09,atrim=0:{total_duration:.3f},volume={music_vol:.2f}[music_a];"
            f"[main_a][music_a]amix=inputs=2:duration=first:dropout_transition=2[mixed_a];"
        )
        if viral_a_filter:
            a_filter += f"[mixed_a]{viral_a_filter},atempo={speed:.4f}[a_out]"
        else:
            a_filter += f"[mixed_a]atempo={speed:.4f}[a_out]"
        filter_chains.append(a_filter)
    else:
        if viral_a_filter:
            a_speed_filter = f"[a_cat]volume={dialog_vol:.2f},{viral_a_filter},atempo={speed:.4f}[a_out]"
        else:
            a_speed_filter = f"[a_cat]volume={dialog_vol:.2f},atempo={speed:.4f}[a_out]"
        filter_chains.append(a_speed_filter)

    filter_complex_str = ";".join(filter_chains)

    cmd = [
        ffmpeg_bin,
        "-y",
        "-i", str(input_p),
    ]

    if has_music:
        cmd.extend(["-stream_loop", "-1", "-i", str(music_p)])

    cmd.extend([
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
    ])

    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
        encoding="utf-8",
        errors="replace",
        creationflags=creationflags
    )

    # Real-time stderr parsing for progress
    time_regex = re.compile(r"time=(\d+):(\d+):(\d+\.\d+)")
    stderr_lines = []
    while True:
        line = proc.stderr.readline()
        if not line and proc.poll() is not None:
            break
        if line:
            stderr_lines.append(line)
            m = time_regex.search(line)
            if m and progress_callback:
                hours, minutes, seconds = int(m.group(1)), int(m.group(2)), float(m.group(3))
                cur_secs = hours * 3600 + minutes * 60 + seconds
                ratio = min(1.0, max(0.0, cur_secs / expected_out_duration))
                progress_callback(ratio)

    proc.wait()
    if proc.returncode != 0:
        err_msg = "".join(stderr_lines[-30:])
        raise RuntimeError(f"FFmpeg render_final failed with exit code {proc.returncode}:\n{err_msg}")

    if progress_callback:
        progress_callback(1.0)

    return str(out_p)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Render 9:16 vertical shorts with Fullscreen or Blurscreen.")
    parser.add_argument("--input", "-i", required=True, help="Input video file path")
    parser.add_argument("--output", "-o", required=True, help="Output video file path")
    parser.add_argument("--start", "-s", type=float, default=0.0, help="Start time in seconds")
    parser.add_argument("--duration", "-d", type=float, default=15.0, help="Duration in seconds")
    parser.add_argument("--layout", "-l", choices=["fullscreen", "blurscreen"], default="fullscreen", help="Layout mode")
    parser.add_argument("--watermark", "-w", type=str, default="Смотри в приложении", help="Watermark text")
    parser.add_argument("--hook", type=str, default=None, help="Top hook header text for viral styling")
    parser.add_argument("--subs", type=str, default=None, help="Russian subtitles text to burn")
    parser.add_argument("--music", type=str, default=None, help="Background music audio file")
    parser.add_argument("--speed", type=float, default=1.03, help="Speed factor (e.g. 1.03)")

    args = parser.parse_args()
    print(f"Processing: {args.input} -> {args.output} (start {args.start}s, duration {args.duration}s, layout: {args.layout})...")
    out = render_vertical_clip(
        input_path=args.input,
        output_path=args.output,
        start_time=args.start,
        duration=args.duration,
        layout_mode=args.layout,
        watermark_text=args.watermark,
        hook_header_text=args.hook,
        subtitles_text=args.subs,
        bg_music_path=args.music,
        speed_factor=args.speed,
    )
    print(f"Done! Saved to: {out}")
