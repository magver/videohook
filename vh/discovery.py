"""Поиск источников и лучших отрезков.

Сигналы для выбора отрезка (объединяются в единый рейтинг):
  * YouTube «Самые пересматриваемые» (heatmap) — реальные данные зрителей;
  * громкость звука (RMS) — кульминации, крики, взрывы;
  * плотность смены сцен — динамичный монтаж/бой;
  * Gemini — смысловой анализ видео (см. gemini.analyze_video).
"""

from __future__ import annotations

import array
import logging
import math
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .core import SOURCES_DIR, SUBPROCESS_FLAGS, ffmpeg_bin, ffmpeg_dir_for_ytdlp, probe, slugify

log = logging.getLogger("videohook.discovery")

BAD_WORDS = ("amv", "reaction", "реакция", "обзор", "review", "trailer", "трейлер", "opening", "опенинг",
             "ending", "эндинг", "tier list", "fan animation", "фан анимация", "roblox", "minecraft",
             "podcast", "подкаст", "stream", "стрим", "edit audio", "karaoke", "караоке")


class _YdlLog:
    def debug(self, msg: str) -> None:
        pass

    def warning(self, msg: str) -> None:
        log.debug("yt-dlp: %s", msg)

    def error(self, msg: str) -> None:
        log.info("yt-dlp: %s", msg[:300])


class SourceError(RuntimeError):
    pass


def _import_ytdlp():
    try:
        import yt_dlp  # тяжёлый импорт — лениво
    except ImportError as exc:
        raise SourceError("Не установлен yt-dlp — поиск и скачивание с YouTube невозможны. "
                          "Выполните: python -m pip install -r requirements.txt") from exc
    return yt_dlp


def _js_runtimes() -> Dict[str, Dict[str, Any]]:
    """YouTube требует JS-движок для расшифровки ссылок: deno (по умолчанию yt-dlp) или node."""
    rt: Dict[str, Dict[str, Any]] = {"deno": {}}
    if shutil.which("node"):
        rt["node"] = {}
    return rt


def _base_opts() -> Dict[str, Any]:
    return {"quiet": True, "no_warnings": True, "socket_timeout": 20, "logger": _YdlLog(),
            "js_runtimes": _js_runtimes()}


def _ydl(opts: Dict[str, Any]):
    yt_dlp = _import_ytdlp()
    base = _base_opts()
    base.update(opts)
    return yt_dlp.YoutubeDL(base)


def _is_relevant(title: str) -> bool:
    t = (title or "").lower()
    return not any(w in t for w in BAD_WORDS)


def search_videos(query: str, limit: int = 12, min_dur: int = 15, max_dur: int = 1800) -> List[Dict[str, Any]]:
    """Поиск на YouTube с ранжированием по просмотрам и релевантности."""
    with _ydl({"extract_flat": "in_playlist", "skip_download": True}) as ydl:
        data = ydl.extract_info(f"ytsearch{limit}:{query}", download=False) or {}
    out = []
    for e in data.get("entries") or []:
        if not e:
            continue
        dur = e.get("duration") or 0
        title = e.get("title") or ""
        if dur and not (min_dur <= dur <= max_dur):
            continue
        if not _is_relevant(title):
            continue
        vid = e.get("id") or ""
        out.append({
            "id": vid,
            "title": title,
            "url": e.get("url") if str(e.get("url", "")).startswith("http") else f"https://www.youtube.com/watch?v={vid}",
            "duration": dur,
            "views": e.get("view_count") or 0,
            "channel": e.get("channel") or e.get("uploader") or "",
            "thumbnail": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg" if vid else "",
            "is_short": bool(dur and dur <= 60),
        })
    out.sort(key=lambda v: v["views"], reverse=True)
    return out


def video_info(url: str) -> Dict[str, Any]:
    """Полная информация о видео, включая heatmap (самые пересматриваемые места)."""
    with _ydl({"skip_download": True}) as ydl:
        info = ydl.extract_info(url, download=False) or {}
    if "entries" in info and info["entries"]:
        info = next((e for e in info["entries"] if e), info)
    return {
        "id": info.get("id", ""),
        "title": info.get("title", ""),
        "url": info.get("webpage_url") or url,
        "duration": info.get("duration") or 0,
        "views": info.get("view_count") or 0,
        "likes": info.get("like_count") or 0,
        "channel": info.get("channel") or info.get("uploader") or "",
        "description": (info.get("description") or "")[:3000],
        "tags": info.get("tags") or [],
        "thumbnail": info.get("thumbnail") or "",
        "heatmap": info.get("heatmap") or [],
        "chapters": info.get("chapters") or [],
        "extractor": info.get("extractor_key", ""),
    }


# ---------------------------------------------------------------------------
# Сигналы
# ---------------------------------------------------------------------------
def heatmap_windows(heatmap: List[Dict[str, float]], window: float = 30.0, top_k: int = 3,
                    duration: float = 0.0) -> List[Dict[str, Any]]:
    """Лучшие окна по данным «Самые пересматриваемые» YouTube."""
    if not heatmap:
        return []
    pts = [((h["start_time"] + h["end_time"]) / 2.0, float(h.get("value", 0))) for h in heatmap]
    total = duration or max(h["end_time"] for h in heatmap)
    candidates = []
    step = max(1.0, window / 6)
    t = 0.0
    while t + window <= total + 0.01 or not candidates:
        vals = [v for (c, v) in pts if t <= c < t + window]
        if vals:
            candidates.append((sum(vals) / len(vals), t))
        t += step
        if t > total:
            break
    return _nms(candidates, window, top_k, "heatmap", "Самый пересматриваемый момент (YouTube)")


def _nms(candidates: List[Tuple[float, float]], window: float, top_k: int, source: str, reason: str,
         min_gap: Optional[float] = None) -> List[Dict[str, Any]]:
    candidates = sorted(candidates, reverse=True)
    if not candidates:
        return []
    top = candidates[0][0] or 1.0
    gap = min_gap if min_gap is not None else window * 0.8
    picked: List[Dict[str, Any]] = []
    for score, start in candidates:
        if all(abs(start - p["start"]) >= gap for p in picked):
            picked.append({"start": round(start, 2), "end": round(start + window, 2),
                           "score": round(score / top, 3), "source": source, "reason": reason})
        if len(picked) >= top_k:
            break
    return picked


def audio_envelope(path: str | Path, rate: int = 4000, hop: float = 0.5) -> List[float]:
    """RMS-огибающая громкости с шагом hop секунд."""
    cmd = [ffmpeg_bin(), "-hide_banner", "-i", str(path), "-vn", "-ac", "1", "-ar", str(rate), "-f", "s16le", "-"]
    proc = subprocess.run(cmd, capture_output=True, creationflags=SUBPROCESS_FLAGS)
    samples = array.array("h")
    samples.frombytes(proc.stdout[: len(proc.stdout) // 2 * 2])
    n = max(1, int(rate * hop))
    env = []
    for i in range(0, len(samples) - n + 1, n):
        chunk = samples[i:i + n]
        env.append(math.sqrt(sum(s * s for s in chunk) / n))
    return env


def scene_cuts(path: str | Path, threshold: float = 0.3) -> List[float]:
    """Моменты смены сцен (секунды) — быстрый проход по уменьшенному видео."""
    cmd = [ffmpeg_bin(), "-hide_banner", "-i", str(path), "-an",
           "-vf", f"scale=160:-2,select='gt(scene,{threshold})',showinfo", "-f", "null", "-"]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          creationflags=SUBPROCESS_FLAGS)
    return [float(m) for m in re.findall(r"pts_time:([\d.]+)", proc.stderr)]


def local_windows(path: str | Path, window: float = 30.0, top_k: int = 3,
                  skip_edges: Optional[float] = None) -> List[Dict[str, Any]]:
    """Лучшие окна по громкости + динамике монтажа для локального файла."""
    info = probe(path)
    dur = info["duration"]
    if dur <= window + 1:
        return [{"start": 0.0, "end": round(dur, 2), "score": 1.0, "source": "local", "reason": "Ролик целиком"}]
    # у длинных серий пропускаем опенинг/эндинг
    edge = skip_edges if skip_edges is not None else (90.0 if dur > 900 else 0.0)
    hop = 0.5
    env = audio_envelope(path, hop=hop) if info["has_audio"] else []
    cuts = scene_cuts(path)
    peak = max(env) if env else 1.0
    candidates = []
    step = 2.0
    t = edge
    while t + window <= dur - edge:
        a0, a1 = int(t / hop), int((t + window) / hop)
        seg = env[a0:a1]
        loud = (sum(seg) / len(seg) / peak) if seg else 0.0
        burst = (max(seg) / peak) if seg else 0.0
        n_cuts = sum(1 for c in cuts if t <= c < t + window)
        pace = min(1.0, n_cuts / (window / 2.5))   # ~1 склейка / 2.5 c = максимум динамики
        score = 0.45 * loud + 0.2 * burst + 0.35 * pace
        candidates.append((score, t))
        t += step
    return _nms(candidates, window, top_k, "local", "Громкость и динамика монтажа")


def merge_suggestions(*groups: List[Dict[str, Any]], top_k: int = 5) -> List[Dict[str, Any]]:
    """Объединяет подсказки разных источников: совпадающие по времени усиливают друг друга."""
    weights = {"gemini": 1.0, "heatmap": 0.9, "local": 0.6}
    flat = [dict(s) for g in groups for s in g]
    for s in flat:
        s["rank"] = s.get("score", 0.5) * weights.get(s.get("source", ""), 0.5)
        for o in flat:
            if o is not s and o.get("source") != s.get("source"):
                overlap = min(s["end"], o["end"]) - max(s["start"], o["start"])
                if overlap > 0.5 * (s["end"] - s["start"]):
                    s["rank"] += 0.25 * o.get("score", 0.5)
                    s.setdefault("confirmed_by", []).append(o.get("source"))
    flat.sort(key=lambda s: s["rank"], reverse=True)
    picked: List[Dict[str, Any]] = []
    for s in flat:
        if all(min(s["end"], p["end"]) - max(s["start"], p["start"]) < 0.5 * (s["end"] - s["start"]) for p in picked):
            s["confirmed_by"] = sorted(set(s.get("confirmed_by", [])))
            picked.append(s)
        if len(picked) >= top_k:
            break
    return picked


# ---------------------------------------------------------------------------
# Поиск источника для момента
# ---------------------------------------------------------------------------
def source_queries(moment: Dict[str, Any]) -> List[str]:
    """Запросы от точного к общему: свои запросы момента, затем «тайтл + сцена» на EN/RU."""
    title = (moment.get("title") or "").strip()
    en = (moment.get("anime_en") or "").strip()
    ru = (moment.get("anime_ru") or moment.get("anime") or "").strip()
    qs = [moment.get("query_en"), moment.get("query"),
          f"{en} {moment.get('query_en') or title} scene" if en and moment.get("query_en") and en.lower() not in moment["query_en"].lower() else "",
          f"{ru} {title}" if ru and title else "",
          f"{en} {title}" if en and title else "",
          f"{en} best scenes" if en else ""]
    out: List[str] = []
    for q in qs:
        q = re.sub(r"\s+", " ", (q or "")).strip()
        if q and q.lower() not in [o.lower() for o in out]:
            out.append(q)
    return out


def find_source_for_moment(moment: Dict[str, Any], anime: Optional[Dict[str, Any]] = None,
                           want: int = 8) -> List[Dict[str, Any]]:
    """Кандидаты-источники для момента, лучшие по просмотрам и длительности.

    Запросы перебираются от точного к общему, пока не наберётся достаточно кандидатов."""
    if moment.get("url"):
        return [{"url": moment["url"], "title": moment.get("title", ""), "views": moment.get("views", 0),
                 "duration": 0, "channel": "", "thumbnail": ""}]
    if anime:
        moment = {"anime_en": anime.get("en", ""), "anime_ru": anime.get("ru", ""), **moment}
    _import_ytdlp()  # без yt-dlp — понятная ошибка, а не «ничего не найдено»
    results: List[Dict[str, Any]] = []
    seen = set()
    errors: List[str] = []
    for i, q in enumerate(source_queries(moment)):
        if len(results) >= want and i >= 2:
            break
        try:
            for v in search_videos(q, limit=10, min_dur=20, max_dur=1800):
                if v["id"] and v["id"] not in seen:
                    seen.add(v["id"])
                    v["rank_bonus"] = 1.0 if i < 2 else 0.8   # точные запросы важнее общих
                    results.append(v)
        except Exception as exc:  # noqa: BLE001
            log.warning("search %s: %s", q, exc)
            errors.append(str(exc)[:160])
    if not results and errors:
        raise SourceError("Поиск на YouTube не работает: " + errors[-1])

    # предпочтение: 1–10 минут (сцена целиком, а не чужой шортс и не полная серия)
    def score(v):
        d = v["duration"] or 120
        fit = 1.0 if 60 <= d <= 600 else (0.6 if d < 60 else 0.7)
        return math.log10(v["views"] + 10) * fit * v.get("rank_bonus", 1.0)
    results.sort(key=score, reverse=True)
    return results[:want]


def popular_moments_for(anime: Dict[str, Any], limit: int = 10,
                        exclude_titles: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Новые моменты из самых просматриваемых роликов по тайтлу (используется при пополнении базы)."""
    from .moments import norm

    exclude = {norm(t) for t in (exclude_titles or [])}
    name_en = anime.get("en") or anime.get("key")
    name_ru = anime.get("ru") or ""
    found: List[Dict[str, Any]] = []
    seen = set()
    for q in (f"{name_en} best scene", f"{name_en} epic moment", f"{name_ru} лучший момент" if name_ru else ""):
        if not q:
            continue
        for v in search_videos(q, limit=15, min_dur=20, max_dur=900):
            if v["id"] in seen:
                continue
            seen.add(v["id"])
            title = re.sub(r"\s*[|\-–—]\s*[^|\-–—]*$", "", v["title"]).strip() or v["title"]
            title = re.sub(r"#\S+", "", title).strip()[:100]
            if norm(title) in exclude or len(title) < 6:
                continue
            found.append({"title": title, "query": v["title"], "url": v["url"], "views": v["views"],
                          "hook": title, "mood": "epic",
                          "why": f"{v['views']:,} просмотров · {v['channel']}".replace(",", " ")})
    found.sort(key=lambda m: m["views"], reverse=True)
    return found[:limit]


# ---------------------------------------------------------------------------
# Скачивание
# ---------------------------------------------------------------------------
def download(url: str, section: Optional[Tuple[float, float]] = None, name_hint: str = "",
             progress: Optional[Callable[[float, str], None]] = None) -> Dict[str, Any]:
    """Скачивает видео (или только нужный отрезок) в папку sources. Возвращает путь и метаданные."""
    yt_dlp = _import_ytdlp()
    from yt_dlp.utils import download_range_func

    SOURCES_DIR.mkdir(parents=True, exist_ok=True)
    stem = slugify(name_hint or "src", 30)
    suffix = f"_{int(section[0])}-{int(section[1])}" if section else ""
    tmpl = str(SOURCES_DIR / f"{stem}_%(id)s{suffix}.%(ext)s")

    def hook(d):
        if progress and d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            got = d.get("downloaded_bytes") or 0
            progress(min(0.99, got / total) if total else 0.3, "Загрузка…")

    opts: Dict[str, Any] = {
        **_base_opts(),
        "format": "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080][ext=mp4]/bv*[height<=1080]+ba/b",
        "merge_output_format": "mp4",
        "outtmpl": tmpl,
        "ffmpeg_location": ffmpeg_dir_for_ytdlp(),
        "noplaylist": True,
        "retries": 3,
        "fragment_retries": 3,
        "progress_hooks": [hook],
    }
    if section:
        opts["download_ranges"] = download_range_func(None, [(max(0.0, section[0]), section[1])])
        opts["force_keyframes_at_cuts"] = True
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True) or {}
        if "entries" in info and info["entries"]:
            info = next((e for e in info["entries"] if e), info)
        path = Path(ydl.prepare_filename(info))
    if not path.exists():
        for ext in (".mp4", ".mkv", ".webm"):
            cand = path.with_suffix(ext)
            if cand.exists():
                path = cand
                break
    if not path.exists():
        matches = sorted(SOURCES_DIR.glob(f"*{info.get('id', '???')}*"), key=lambda p: p.stat().st_mtime)
        if not matches:
            raise FileNotFoundError("yt-dlp не создал файл")
        path = matches[-1]
    meta = probe(path)
    return {
        "path": str(path),
        "duration": meta["duration"],
        "width": meta["width"],
        "height": meta["height"],
        "offset": section[0] if section else 0.0,
        "info": {
            "id": info.get("id"), "title": info.get("title"), "channel": info.get("channel") or info.get("uploader"),
            "views": info.get("view_count"), "url": info.get("webpage_url") or url,
            "heatmap": info.get("heatmap") or [],
            "duration": info.get("duration"),
        },
    }
