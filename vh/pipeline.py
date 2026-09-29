"""Конвейер: момент → источник → лучший отрезок → монтаж 9:16 → Antigravity → описания → публикация.

Каждый шаг идемпотентен и может запускаться вручную из UI или цепочкой (автопилот).
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from . import antigravity, discovery, gemini, library, moments, publish
from .core import RENDERS_DIR, get_settings, rel_to_work, resolve_work, slugify, tasks
from .render import TRANSITIONS, contact_sheet, extract_audio, extract_thumbnail, render

log = logging.getLogger("videohook.pipeline")

TARGET_SECONDS = 35   # ориентир; итоговая длина подстраивается под законченную сцену


# ---------------------------------------------------------------------------
# Создание карточки
# ---------------------------------------------------------------------------
def _anime_fields(anime: Optional[Dict[str, Any]], fallback: str = "") -> Dict[str, Any]:
    if not anime:
        return {"anime": fallback, "anime_name": fallback, "anime_ru": "", "anime_en": fallback, "studio": "",
                "credit": f"Аниме: {fallback} · фан-обзор" if fallback else "Фан-обзор · права у правообладателей"}
    name = moments.display_name(anime)
    studio = anime.get("studio", "")
    short = anime.get("ru") or anime.get("en") or anime["key"]
    credit = f"Аниме: {anime.get('en') or short}" + (f" · © {studio}" if studio else "") + " · фан-обзор"
    return {"anime": short, "anime_name": name, "anime_ru": anime.get("ru", ""), "anime_en": anime.get("en", ""),
            "anime_key": anime["key"], "studio": studio, "credit": credit}


def _default_music(mood: str, preferred: str = "") -> str:
    """По умолчанию — родной звук сцены; музыка по настроению только если выбрана «авто»."""
    choice = get_settings().get("default_music") or "none"
    if choice == "auto":
        return preferred or moments.mood_music(mood or "epic")
    return choice


def create_from_moment(anime_key: str, moment_id: str) -> Dict[str, Any]:
    idx = moments._anime_index()
    anime = idx.get(anime_key)
    if not anime:
        raise KeyError("Тайтл не найден")
    m = next((x for x in anime["moments"] if moments.moment_id(anime_key, x["title"]) == moment_id), None)
    if not m:
        raise KeyError("Момент не найден")
    clip = library.add_clip({
        **_anime_fields(anime),
        "moment_id": moment_id,
        "title": m["title"],
        "episode": m.get("episode", ""),
        "hook": m.get("hook", m["title"]),
        "mood": m.get("mood", "epic"),
        "query": m.get("query", ""),
        "query_en": m.get("query_en", ""),
        "source_url": m.get("url", ""),
        "music": _default_music(m.get("mood", "epic"), m.get("music")),
    })
    moments.mark_used(moment_id, anime_key, clip["id"])
    return clip


def create_from_url(url: str, title: str = "") -> Dict[str, Any]:
    info = {}
    try:
        info = discovery.video_info(url)
    except Exception as exc:  # noqa: BLE001
        log.warning("video_info: %s", exc)
    text = f"{info.get('title', '')} {' '.join(info.get('tags', [])[:20])} {info.get('description', '')[:400]}"
    anime = moments.find_anime(text) or moments.find_anime(title)
    clip = library.add_clip({
        **_anime_fields(anime, title or "Аниме"),
        "title": title or info.get("title") or "Сцена",
        "hook": (title or info.get("title") or "")[:60],
        "source_url": info.get("url") or url,
        "source_title": info.get("title", ""),
        "source_channel": info.get("channel", ""),
        "source_views": info.get("views", 0),
        "music": _default_music("epic"),
    })
    return clip


# ---------------------------------------------------------------------------
# Шаг 1: источник и лучший отрезок
# ---------------------------------------------------------------------------
def _source_candidates(clip: Dict[str, Any], explicit_url: str = "") -> List[Dict[str, Any]]:
    """Ссылка пользователя/момента первой, затем результаты поиска (запасные варианты)."""
    out: List[Dict[str, Any]] = []
    for url in (explicit_url, clip.get("source_url")):
        if url and url not in [c["url"] for c in out]:
            out.append({"url": url, "title": "", "views": 0, "duration": 0, "channel": ""})
    if explicit_url:
        return out  # пользователь указал ссылку явно — не подменяем её найденными роликами
    query = {"title": clip.get("title", ""), "query": clip.get("query") or f"{clip.get('anime')} {clip.get('title')}",
             "query_en": clip.get("query_en", ""), "anime_en": clip.get("anime_en", ""),
             "anime_ru": clip.get("anime_ru", "") or clip.get("anime", ""),
             "prefer_ru_dub": bool(get_settings().get("prefer_ru_dub", True))}
    for c in discovery.find_source_for_moment(query):
        if c["url"] not in [o["url"] for o in out]:
            out.append(c)
    return out


def _download_best(url: str, clip: Dict[str, Any], t) -> Dict[str, Any]:
    info = discovery.video_info(url)
    dur = info.get("duration") or 0
    heat = discovery.heatmap_windows(info.get("heatmap") or [], TARGET_SECONDS, 3, dur)
    section = None
    if dur > 240 and heat:
        best = heat[0]
        section = (max(0.0, best["start"] - 45), min(dur, best["end"] + 45))

    def prog(p, msg):
        t(0.2 + 0.5 * p, msg)

    t(0.2, "Скачиваю " + ("нужный фрагмент…" if section else "видео…"))
    dl = discovery.download(url, section=section, name_hint=f"{clip.get('anime')}_{clip.get('title')}", progress=prog)
    return {"info": info, "heat": heat, "dl": dl}


def fetch_source(clip_id: str, task=None, source_url: str = "") -> Dict[str, Any]:
    clip = library.require_clip(clip_id)
    t = task.update if task else (lambda *a, **k: None)
    t(0.05, "Ищу лучший источник на YouTube…")
    candidates = _source_candidates(clip, source_url)
    if not candidates:
        raise RuntimeError("На YouTube не нашлось роликов с этой сценой. Уточните запрос момента или вставьте ссылку")
    got = None
    errors: List[str] = []
    for i, cand in enumerate(candidates[:5]):
        t(0.15, f"Источник {i + 1}/{min(5, len(candidates))}: читаю метаданные и карту пересмотров…")
        try:
            got = _download_best(cand["url"], clip, t)
            break
        except Exception as exc:  # noqa: BLE001 — недоступное видео: пробуем следующее
            log.warning("source %s: %s", cand["url"], exc)
            errors.append(f"{cand['url']}: {str(exc)[:120]}")
            if isinstance(exc, discovery.SourceError):
                raise
    if not got:
        raise RuntimeError("Не удалось скачать ни один источник:\n" + "\n".join(errors))
    info, heat, dl = got["info"], got["heat"], got["dl"]
    offset = dl["offset"]
    local_dur = dl["duration"]

    t(0.75, "Ищу лучший момент (звук, динамика, пересмотры)…")
    heat_local = []
    for h in heat:
        s0, e0 = h["start"] - offset, h["end"] - offset
        if e0 > 0 and s0 < local_dur:
            heat_local.append({**h, "start": round(max(0.0, s0), 2), "end": round(min(local_dur, e0), 2)})
    local = discovery.local_windows(dl["path"], TARGET_SECONDS, 3)
    suggestions = discovery.merge_suggestions(heat_local, local, top_k=5)
    best = suggestions[0] if suggestions else {"start": 0.0, "end": min(local_dur, TARGET_SECONDS)}
    # границы — к естественным точкам: склейка/пауза перед завязкой, пауза после последней фразы
    max_len = float(get_settings().get("clip_max_seconds", 58))
    try:
        best = {**best, **discovery.refine_bounds(dl["path"], best["start"], best["end"], max_len=max_len)}
        if suggestions:
            suggestions[0] = {**suggestions[0], "start": best["start"], "end": best["end"]}
    except Exception as exc:  # noqa: BLE001
        log.warning("refine_bounds: %s", exc)
    accents: List[float] = []
    try:
        accents = discovery.audio_peaks(dl["path"], best["start"], best["end"])
    except Exception as exc:  # noqa: BLE001
        log.warning("audio_peaks: %s", exc)

    t(0.8, "Субтитры…")
    subtitles: List[Dict[str, Any]] = []
    subs_kind = ""
    try:
        subs = info.get("subs_ru") or {}
        if subs:
            got = discovery.fetch_subtitles(subs, offset, offset + local_dur)
            subtitles = [{"start": round(x["start"] - offset, 2), "end": round(x["end"] - offset, 2), "text": x["text"]}
                         for x in got["items"] if x["end"] - offset > 0]
            subs_kind = "youtube-" + (subs.get("kind", "") if got["lang"] == "ru" else "orig")
            if subtitles and got["lang"] != "ru":
                # автоперевод YouTube недоступен — переводим оригинал через ИИ, без ИИ субтитры не показываем
                subtitles = (gemini.translate_subtitles(subtitles, clip.get("anime_name") or "", got["lang"])
                             if gemini.available() else [])
                subs_kind = "gemini-translate"
    except Exception as exc:  # noqa: BLE001
        log.warning("subtitles: %s", exc)

    RENDERS_DIR.mkdir(parents=True, exist_ok=True)
    thumb = RENDERS_DIR / f"{clip_id}_src.jpg"
    try:
        extract_thumbnail(dl["path"], str(thumb), at=min(local_dur - 0.1, best["start"] + 1.0))
    except Exception:  # noqa: BLE001
        thumb = None  # type: ignore[assignment]

    patch = {
        "source_url": dl["info"]["url"],
        "source_title": dl["info"].get("title", ""),
        "source_channel": dl["info"].get("channel", ""),
        "source_views": dl["info"].get("views") or info.get("views", 0),
        "source_file": rel_to_work(dl["path"]),
        "source_duration": local_dur,
        "source_offset": offset,
        "source_candidates": [c for c in candidates if c.get("title")][:6],
        "suggestions": suggestions,
        "segment": {"start": best["start"], "end": best["end"]},
        "parts": [],
        "accents": accents,
        "slowmo": None,
        "subtitles": subtitles,
        "subtitles_source": subs_kind if subtitles else "",
        "ru_dub": bool(info.get("ru_dub")),
        "thumb": rel_to_work(thumb) if thumb else "",
    }
    library.update_clip(clip_id, patch, note=f"Источник: {patch['source_title'][:60]} ({int(local_dur)} c)")
    library.advance_stage(clip_id, "downloaded")

    if gemini.available():
        try:
            ai_analyze(clip_id, task)
        except Exception as exc:  # noqa: BLE001
            log.warning("Gemini analyze: %s", exc)
            library.update_clip(clip_id, {}, note=f"ИИ-анализ пропущен: {str(exc)[:100]}")
    t(1.0, "Источник готов")
    return library.require_clip(clip_id)


def ai_analyze(clip_id: str, task=None) -> Dict[str, Any]:
    """Gemini (через Antigravity или API) смотрит исходник и предлагает отрезки, хук, реплики, идеи монтажа."""
    clip = library.require_clip(clip_id)
    if task:
        task.update(0.85, "Gemini смотрит видео…")
    src = resolve_work(clip["source_file"])
    ctx = f"{clip.get('anime_name')}, сцена «{clip.get('title')}»"
    storyboard = audio = None
    try:
        sb = RENDERS_DIR / f"{clip_id}_storyboard.jpg"
        storyboard = contact_sheet(str(src), str(sb))
    except Exception as exc:  # noqa: BLE001
        log.warning("storyboard: %s", exc)
    try:
        audio = extract_audio(str(src), str(RENDERS_DIR / f"{clip_id}_audio.wav"))
    except Exception as exc:  # noqa: BLE001
        log.warning("audio: %s", exc)
    s = get_settings()
    data = gemini.analyze_video(src, ctx, TARGET_SECONDS, mood=clip.get("mood", ""), hook=clip.get("hook", ""),
                                hints=clip.get("suggestions", []), storyboard=storyboard, audio=audio,
                                max_seconds=int(s.get("clip_max_seconds", 58)), ru_audio=bool(clip.get("ru_dub")),
                                progress=(lambda m: task.update(None, m)) if task else None)
    dur = float(clip.get("source_duration") or 0) or 1e9
    suggestions = discovery.merge_suggestions(data.get("segments", []), clip.get("suggestions", []), top_k=5)
    patch: Dict[str, Any] = {"suggestions": suggestions,
                             "ai": {k: data.get(k) for k in ("hook", "caption", "mood", "story", "dialogue_heavy")}}
    if data.get("segments"):
        best = data["segments"][0]
        patch["segment"] = {"start": max(0.0, best["start"]), "end": min(dur, best["end"])}
        patch["parts"] = [[max(0.0, a), min(dur, b)] for a, b in data.get("parts") or [] if a < dur]
    elif suggestions:
        patch["segment"] = {"start": suggestions[0]["start"], "end": suggestions[0]["end"]}
    if data.get("accents"):
        patch["accents"] = data["accents"]
    patch["slowmo"] = data.get("slowmo")
    if data.get("transition") in TRANSITIONS:
        patch["transition"] = data["transition"]
    if data.get("subtitles"):
        patch["subtitles"] = data["subtitles"]
        patch["subtitles_source"] = "gemini"
    if data.get("hook"):
        patch["hook"] = data["hook"]
    if data.get("caption"):
        patch["caption"] = data["caption"]
    if data.get("key_lines"):
        patch["key_lines"] = [k for k in data["key_lines"] if isinstance(k, dict) and "t" in k and "text" in k]
    if data.get("edit_ideas"):
        patch["edit_ideas"] = data["edit_ideas"][:8]
    if data.get("mood") in antigravity.MOOD_DIRECTION:
        patch["mood"] = data["mood"]
    return library.update_clip(clip_id, patch, note="Gemini: анализ видео готов")


# ---------------------------------------------------------------------------
# Шаг 2: монтаж
# ---------------------------------------------------------------------------
def clip_segments(clip: Dict[str, Any]) -> List[List[float]]:
    """Части ролика: если ИИ разбил сцену на части и отрезок не меняли вручную — части, иначе весь отрезок."""
    seg = clip.get("segment") or {"start": 0, "end": TARGET_SECONDS}
    parts = clip.get("parts") or []
    if len(parts) > 1 and abs(parts[0][0] - seg["start"]) < 0.6 and abs(parts[-1][1] - seg["end"]) < 0.6:
        return [[float(a), float(b)] for a, b in parts]
    return [[float(seg["start"]), float(seg["end"])]]


def render_params(clip: Dict[str, Any], overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    s = get_settings()
    prev = (clip.get("render") or {}).get("params") or {}
    subs_on = s.get("subtitles_mode", "auto") != "off"
    p = {
        "template": prev.get("template") or s.get("default_template", "cinema"),
        "segments": clip_segments(clip),
        "mood": clip.get("mood", "epic"),
        "transition": prev.get("transition") or clip.get("transition") or "auto",
        "accents": clip.get("accents") or [],
        "slowmo": clip.get("slowmo"),
        "subtitles": clip.get("subtitles") if subs_on else [],
        "hook": clip.get("hook", ""),
        "caption": clip.get("caption", ""),
        "anime_label": clip.get("anime", ""),
        "handle": s.get("brand_handle", ""),
        "credit": clip.get("credit", ""),
        "music": prev.get("music") or clip.get("music") or "none",
        "music_volume": prev.get("music_volume", s.get("music_volume", 0.22)),
        "loop_friendly": prev.get("loop_friendly", s.get("loop_friendly", False)),
        "effects": prev.get("effects") or {},
        "max_seconds": s.get("clip_max_seconds", 45),
        "progress_bar": prev.get("progress_bar", True),
        "commentary": clip.get("commentary", ""),
    }
    p.update({k: v for k, v in (overrides or {}).items() if v is not None})
    if p.get("use_subtitles") is False:
        p["subtitles"] = []
    # реплики: время исходника → время ролика
    st = float(p["segments"][0][0])
    en = float(p["segments"][-1][1])
    p["key_lines"] = [{"t": round(float(k["t"]) - st, 2), "text": k["text"]}
                      for k in clip.get("key_lines", []) if st <= float(k["t"]) < en] if p.get("use_key_lines", True) else []
    return p


def make_render(clip_id: str, overrides: Optional[Dict[str, Any]] = None, task=None) -> Dict[str, Any]:
    clip = library.require_clip(clip_id)
    if not clip.get("source_file"):
        fetch_source(clip_id, task)
        clip = library.require_clip(clip_id)
    text_fields = {k: overrides.pop(k) for k in ("hook", "caption", "commentary") if overrides and k in overrides}
    if text_fields:
        clip = library.update_clip(clip_id, text_fields)
    if overrides and overrides.get("segments"):
        segs = overrides["segments"]
        patch = {"segment": {"start": float(segs[0][0]), "end": float(segs[-1][1])}}
        if segs != clip_segments(clip):
            patch["parts"] = [[float(a), float(b)] for a, b in segs] if len(segs) > 1 else []
        clip = library.update_clip(clip_id, patch)
    params = render_params(clip, overrides)
    out = RENDERS_DIR / f"{slugify(clip.get('anime') or 'anime', 20)}_{clip_id}.mp4"
    if task:
        task.update(0.05, "Монтаж 9:16…")
    res = render(str(resolve_work(clip["source_file"])), params, str(out),
                 progress=(lambda p: task.update(0.05 + 0.9 * p, f"Рендер {int(p * 100)}%")) if task else None)
    thumb = RENDERS_DIR / f"{clip_id}_render.jpg"
    try:
        extract_thumbnail(res["path"], str(thumb), at=1.2)
    except Exception:  # noqa: BLE001
        thumb = None  # type: ignore[assignment]
    store_params = {k: v for k, v in params.items() if k not in ("key_lines",)}
    library.update_clip(clip_id, {"render": {"file": rel_to_work(res["path"]), "params": store_params,
                                             "duration": res["duration"], "t": time.time(),
                                             "thumb": rel_to_work(thumb) if thumb else ""},
                                  "ag": {}},
                        note=f"Смонтировано: {params['template']}, {res['duration']} c")
    return library.advance_stage(clip_id, "rendered")


# ---------------------------------------------------------------------------
# Шаг 3: Antigravity или сразу в публикацию
# ---------------------------------------------------------------------------
def send_to_antigravity(clip_id: str, task=None) -> Dict[str, Any]:
    clip = library.require_clip(clip_id)
    if not (clip.get("render") or {}).get("file"):
        make_render(clip_id, task=task)
    if task:
        task.update(0.5, "Готовлю папку задачи для Antigravity…")
    library.update_clip(clip_id, {"ag": {}})
    antigravity.create_job(clip_id)
    res = antigravity.launch(clip_id)
    if task:
        task.update(1.0, "Агент запущен" if res["mode"] == "agentapi" else "Промпт готов — вставьте в Antigravity")
    return res


def finalize(clip_id: str, task=None) -> Dict[str, Any]:
    """Готовый ролик → описания → (автопубликация)."""
    clip = library.require_clip(clip_id)
    if not ((clip.get("publish") or {}).get("captions") or {}).get("youtube"):
        publish.prepare_captions(clip_id)
    if get_settings().get("auto_publish"):
        auto_publish(clip_id, task)
    return library.require_clip(clip_id)


def auto_publish(clip_id: str, task=None) -> Dict[str, Any]:
    results = {}
    if publish.youtube_status()["connected"]:
        try:
            results["youtube"] = publish.youtube_upload(clip_id, task)
        except Exception as exc:  # noqa: BLE001
            results["youtube"] = {"error": str(exc)}
    if publish.tiktok_status()["configured"]:
        try:
            results["tiktok"] = publish.tiktok_upload(clip_id, task)
        except Exception as exc:  # noqa: BLE001
            results["tiktok"] = {"error": str(exc)}
    return results


def on_ready(clip: Dict[str, Any]) -> None:
    tasks.submit("finalize", f"Описания и публикация: {clip.get('title', '')[:40]}", lambda t: finalize(clip["id"], t))


def full_pipeline(clip_id: str, task=None) -> Dict[str, Any]:
    t = task.update if task else (lambda *a, **k: None)
    s = get_settings()
    clip = library.require_clip(clip_id)
    if not clip.get("source_file"):
        t(0.02, "1/3 Источник…")
        fetch_source(clip_id, None)
    t(0.45, "2/3 Монтаж 9:16…")
    make_render(clip_id)
    if s.get("use_antigravity"):
        t(0.8, "3/3 Отправка в Antigravity…")
        send_to_antigravity(clip_id)
        t(1.0, "В Antigravity — результат подхватится автоматически")
    else:
        antigravity.use_draft_as_final(clip_id)
        finalize(clip_id)
        t(1.0, "Готово к публикации")
    return library.require_clip(clip_id)


# ---------------------------------------------------------------------------
# Автопилот
# ---------------------------------------------------------------------------
def pick_fresh_moments(count: int, anime_keys: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Свежие моменты: сначала тайтлы из трендов, по одному на тайтл за проход (разнообразие)."""
    keys = list(anime_keys or [])
    if not keys:
        try:
            keys = [a["key"] for a in moments.trending(limit=30) if a.get("in_base")]
        except Exception:  # noqa: BLE001
            keys = []
        keys += [a["key"] for a in moments.catalog() if a["key"] not in keys]
    picked: List[Dict[str, Any]] = []
    rounds = 0
    while len(picked) < count and rounds < 3:
        for k in keys:
            fresh = [m for m in moments.list_moments(k, include_used=False) if m["id"] not in {p["id"] for p in picked}]
            if fresh:
                picked.append(fresh[0])
            if len(picked) >= count:
                break
        rounds += 1
    return picked


def autopilot(count: int = 3, anime_keys: Optional[List[str]] = None, task=None) -> Dict[str, Any]:
    t = task.update if task else (lambda *a, **k: None)
    chosen = pick_fresh_moments(count, anime_keys)
    if not chosen:
        raise RuntimeError("Нет свежих моментов — база пополняется, попробуйте через минуту")
    done, failed = [], []
    for i, m in enumerate(chosen):
        base = i / len(chosen)
        t(base, f"[{i + 1}/{len(chosen)}] {m['title']}")
        try:
            clip = create_from_moment(m["anime_key"], m["id"])
            full_pipeline(clip["id"])
            done.append(clip["id"])
        except Exception as exc:  # noqa: BLE001
            log.warning("autopilot %s: %s", m["title"], exc)
            failed.append({"moment": m["title"], "error": str(exc)[:200]})
    moments.maintain_all()
    t(1.0, f"Автопилот: готово {len(done)}, ошибок {len(failed)}")
    return {"done": done, "failed": failed}
