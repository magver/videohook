import os
import shutil
from pathlib import Path
from typing import Callable, Optional
import yt_dlp

from anime_detector import is_genuine_anime_video
from clipper import ensure_ffmpeg_in_path, get_ffmpeg_path


def download_clip_by_query(
    search_query: str,
    output_dir: str = "cache_clips",
    progress_callback: Optional[Callable[[str, float], None]] = None,
) -> str:
    """
    Searches YouTube for top matching HD clip and downloads it to output_dir.
    Searches top 5 candidates to select the most relevant clip under 15 minutes,
    preventing failures caused by long movies or compilations.
    Uses ffmpeg for merging audio+video.

    Returns:
        Absolute path to the downloaded video file.
    """
    out_dir = Path(output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    # Ensure ffmpeg.exe exists in PATH and get exact binary path
    ffmpeg_bin = ensure_ffmpeg_in_path()
    out_template = str(out_dir / "%(id)s.%(ext)s")

    def yt_progress_hook(d):
        if progress_callback and d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 1
            downloaded = d.get("downloaded_bytes") or 0
            ratio = min(1.0, max(0.0, downloaded / total))
            progress_callback(f"Загрузка: {ratio * 100:.1f}%", ratio)

    is_direct_url = search_query.startswith("http://") or search_query.startswith("https://")
    search_spec = search_query if is_direct_url else f"ytsearch8:{search_query}"

    search_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "skip_download": True,
        "socket_timeout": 20,
    }

    selected_url = search_query
    if not is_direct_url:
        try:
            with yt_dlp.YoutubeDL(search_opts) as ydl_search:
                search_data = ydl_search.extract_info(search_spec, download=False)
                entries = search_data.get("entries", []) if search_data else []
                # Filter out None entries and find best genuine anime candidate (<= 15 min / 900s, >= 15s)
                valid_entries = [e for e in entries if e and isinstance(e, dict)]
                best_entry = None
                for e in valid_entries:
                    dur = e.get("duration") or 0
                    t = e.get("title") or ""
                    if 15 <= dur <= 900 and is_genuine_anime_video(t):
                        best_entry = e
                        break
                if not best_entry:
                    for e in valid_entries:
                        dur = e.get("duration") or 0
                        if 15 <= dur <= 900:
                            best_entry = e
                            break
                # Fallback to shortest entry or first entry if all > 900s
                if not best_entry and valid_entries:
                    valid_entries.sort(key=lambda x: x.get("duration") or 999999)
                    best_entry = valid_entries[0]

                if best_entry:
                    selected_url = best_entry.get("url") or best_entry.get("webpage_url") or f"https://www.youtube.com/watch?v={best_entry.get('id')}"
        except Exception as e:
            # Fallback to original query
            selected_url = f"ytsearch1:{search_query}"

    download_opts = {
        "format": "best[ext=mp4][height<=1080]/bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=1080]+bestaudio/best[height<=1080]/best",
        "outtmpl": out_template,
        "ffmpeg_location": ffmpeg_bin,
        "quiet": True,
        "no_warnings": True,
        "progress_hooks": [yt_progress_hook],
        "noplaylist": True,
        "retries": 3,
        "fragment_retries": 3,
        "socket_timeout": 30,
    }

    with yt_dlp.YoutubeDL(download_opts) as ydl:
        info = ydl.extract_info(selected_url, download=True)
        if info and "entries" in info:
            entries = [e for e in info["entries"] if e]
            info = entries[0] if entries else info
        if not info:
            raise RuntimeError(f"Не удалось получить информацию о видео: {search_query}")

        filename = ydl.prepare_filename(info)

        # In case merge resulted in .mp4 or .mkv
        p = Path(filename)
        if not p.exists() or p.stat().st_size == 0:
            for ext in [".mp4", ".mkv", ".webm", ".ts"]:
                candidate = p.with_suffix(ext)
                if candidate.exists() and candidate.stat().st_size > 0:
                    return str(candidate)
            # Check directory for any recently created file matching id
            video_id = info.get("id", "")
            if video_id:
                for cand in out_dir.glob(f"*{video_id}*"):
                    if cand.is_file() and cand.stat().st_size > 1024:
                        return str(cand)
            raise FileNotFoundError(f"Загруженный видеофайл не найден на диске: {filename}")

        return str(p)
