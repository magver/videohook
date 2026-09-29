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
import time
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
        "subs_ru": _pick_ru_subs(info),
        "ru_dub": is_ru_dub(info.get("title", "")),
    }


RU_DUB = re.compile(r"озвуч|дубляж|русск|на русском|\brus\b|\bru\b|anilibria|анилибри|anidub|анидаб|studio ?band|"
                    r"jam ?club|shiza|шиза|animevost|аниме ?вост|dream ?cast|amazing ?dubbing", re.I)


def is_ru_dub(title: str) -> bool:
    """Ролик с русской озвучкой (по названию: студии озвучки, «русская озвучка», «дубляж»)."""
    return bool(RU_DUB.search(title or ""))


def _pick_ru_subs(info: Dict[str, Any]) -> Dict[str, str]:
    """Субтитры YouTube (json3): ручные русские, иначе автоперевод на русский.
    orig_url — те же субтитры без перевода: YouTube часто ограничивает автоперевод (429), тогда
    оригинал переводится через ИИ."""
    for kind, pool in (("manual", info.get("subtitles") or {}), ("auto", info.get("automatic_captions") or {})):
        for lang in ("ru", "ru-RU", "ru-orig"):
            fmts = pool.get(lang) or []
            f = next((x for x in fmts if x.get("ext") == "json3"), None)
            if f and f.get("url"):
                url = f["url"]
                orig = re.sub(r"&tlang=[^&]+", "", url) if "tlang=" in url else ""
                m = re.search(r"[?&]lang=([\w-]+)", orig)
                return {"kind": kind, "url": url, "lang": "ru", "orig_url": orig,
                        "orig_lang": m.group(1) if m else ""}
    for lang in ("en", "en-US", "en-GB"):
        f = next((x for x in (info.get("subtitles") or {}).get(lang) or [] if x.get("ext") == "json3"), None)
        if f and f.get("url"):
            return {"kind": "manual", "url": "", "lang": "", "orig_url": f["url"], "orig_lang": lang}
    return {}


def _get_json(url: str, tries: int = 2) -> Dict[str, Any]:
    import json as _json

    last: Optional[Exception] = None
    for i in range(tries):
        try:
            with _ydl({}) as ydl:   # сессия yt-dlp: те же заголовки и куки, что и при извлечении
                return _json.loads(ydl.urlopen(url).read().decode("utf-8", "replace"))
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(2.5 * (i + 1))
    raise RuntimeError(f"субтитры недоступны: {last}")


def _parse_json3(data: Dict[str, Any], t0: float, t1: float) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for ev in data.get("events") or []:
        text = "".join(sg.get("utf8", "") for sg in ev.get("segs") or []).replace("\n", " ").strip()
        if not text or "tStartMs" not in ev:
            continue
        st = ev["tStartMs"] / 1000.0
        en = st + (ev.get("dDurationMs") or 2000) / 1000.0
        if en < t0 or st > t1:
            continue
        if out and (text == out[-1]["text"] or out[-1]["text"].endswith(text)):
            out[-1]["end"] = max(out[-1]["end"], en)
            continue
        if out and out[-1]["end"] > st:
            out[-1]["end"] = st
        out.append({"start": round(st, 2), "end": round(en, 2), "text": re.sub(r"\s+", " ", text)})
    return out


def fetch_subtitles(subs: Dict[str, str], t0: float = 0.0, t1: float = 1e9) -> Dict[str, Any]:
    """Фразы субтитров [{start, end, text}] в интервале [t0, t1] (время видео) и их язык.
    Сначала русские; если автоперевод недоступен — оригинал (lang != "ru", нужен перевод)."""
    errors = []
    for url, lang in ((subs.get("url"), subs.get("lang") or "ru"), (subs.get("orig_url"), subs.get("orig_lang") or "")):
        if not url:
            continue
        try:
            items = _parse_json3(_get_json(url), t0, t1)
            if items:
                return {"items": items, "lang": lang}
        except Exception as exc:  # noqa: BLE001
            errors.append(str(exc)[:120])
    if errors:
        log.info("subtitles: %s", "; ".join(errors))
    return {"items": [], "lang": ""}


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


def speech_pauses(env: List[float], hop: float, min_len: float = 0.25) -> List[float]:
    """Середины пауз (тишина между фразами) по RMS-огибающей."""
    if not env:
        return []
    srt = sorted(env)
    loud = srt[int(len(srt) * 0.9)] or 1.0
    quiet = max(srt[int(len(srt) * 0.2)] * 1.3, loud * 0.12)
    pauses, run = [], 0
    for i, v in enumerate(env + [loud]):
        if v <= quiet:
            run += 1
        else:
            if run * hop >= min_len:
                pauses.append(round((i - run / 2) * hop, 2))
            run = 0
    return pauses


def refine_bounds(path: str | Path, start: float, end: float, min_len: float = 18.0,
                  max_len: float = 58.0) -> Dict[str, Any]:
    """Сдвигает границы отрезка к естественным точкам: начало — к склейке/паузе перед завязкой,
    конец — к паузе после последней фразы или к смене сцены. Отрезок не рвёт реплику посередине."""
    info = probe(path)
    dur = info["duration"] or end
    hop = 0.1
    env = audio_envelope(path, hop=hop) if info["has_audio"] else []
    cuts = scene_cuts(path)
    pauses = speech_pauses(env, hop)

    def near(points, t, tol=0.35):
        return any(abs(p - t) <= tol for p in points)

    # начало: окно [start-8, start+1.5], ближе к исходному — лучше; склейка в тишине — идеал
    best_s, best_sc = start, 0.0
    for c in sorted(set([round(x, 2) for x in cuts + pauses])):
        if not (start - 8.0 <= c <= start + 1.5) or c < 0:
            continue
        sc = (1.2 if near(cuts, c, 0.05) else 0) + (1.0 if near(pauses, c) else 0) - 0.12 * abs(c - start)
        if sc > best_sc:
            best_s, best_sc = c, sc
    # конец: окно [end-1.5, end+10], с учётом максимальной длины; пауза после фразы важнее склейки
    hi = min(dur, best_s + max_len, end + 10.0)
    best_e, best_ec = min(end, hi), 0.0
    for c in sorted(set([round(x, 2) for x in cuts + pauses])):
        if not (end - 1.5 <= c <= hi):
            continue
        sc = (1.4 if near(pauses, c) else 0) + (1.0 if near(cuts, c, 0.05) else 0) - 0.08 * abs(c - end)
        if sc > best_ec:
            best_e, best_ec = c, sc
    if near(pauses, best_e) and not near(cuts, best_e, 0.05):
        best_e = min(hi, best_e + 0.4)  # дать реакции/звуку договорить
    if best_e - best_s < min_len:
        best_e = min(dur, best_s + min_len)
    return {"start": round(max(0.0, best_s), 2), "end": round(min(dur, best_e), 2),
            "snapped": bool(best_sc > 0 or best_ec > 0)}


def audio_peaks(path: str | Path, start: float, end: float, n: int = 3, min_gap: float = 4.0) -> List[float]:
    """Ударные моменты отрезка без ИИ: самые резкие всплески громкости (удары, взрывы, крики)."""
    hop = 0.1
    env = audio_envelope(path, hop=hop)
    if not env:
        return []
    i0, i1 = int(start / hop) + 10, min(len(env), int(end / hop) - 10)   # не у самых краёв
    onsets = []
    for i in range(max(i0, 5), i1):
        prev = sum(env[i - 5:i]) / 5 or 1.0
        onsets.append((env[i] / prev * env[i], round(i * hop, 2)))
    peak = max((v for v, _ in onsets), default=0) or 1.0
    picked: List[float] = []
    for v, t in sorted(onsets, reverse=True):
        if v < peak * 0.35:
            break
        if all(abs(t - p) >= min_gap for p in picked):
            picked.append(t)
        if len(picked) >= n:
            break
    return sorted(picked)


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
    dub = [f"{ru} {title} русская озвучка"] if moment.get("prefer_ru_dub") and ru and title else []
    qs = [moment.get("query_en"), *dub, moment.get("query"),
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
        if len(results) >= want and i >= 3:
            break
        try:
            for v in search_videos(q, limit=10, min_dur=20, max_dur=1800):
                if v["id"] and v["id"] not in seen:
                    seen.add(v["id"])
                    v["rank_bonus"] = 1.0 if i < 3 else 0.8   # точные запросы важнее общих
                    v["ru_dub"] = is_ru_dub(v["title"])
                    if v["ru_dub"] and moment.get("prefer_ru_dub"):
                        v["rank_bonus"] *= 1.35                # русская озвучка — в приоритете
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
