"""Публикация: тексты под площадки + быстрые загрузки.

  YouTube Shorts — официальный YouTube Data API v3 (OAuth «Приложение для ПК», свой client_secret.json).
  TikTok        — Content Posting API (загрузка во «Входящие»/черновики, завершение в приложении).
  Instagram     — ассистированная публикация: подпись в буфере, файл выделен в проводнике, открыт веб-загрузчик.
Для любой площадки без настроенного API работает ассистированный режим.
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import subprocess
import sys
import time
import urllib.parse
import webbrowser
from pathlib import Path
from typing import Any, Dict, List

import requests

from . import library
from .core import DATA_DIR, JsonStore, get_settings, resolve_work

log = logging.getLogger("videohook.publish")

PLATFORMS = {
    "youtube": {"name": "YouTube Shorts", "upload_url": "https://studio.youtube.com/"},
    "tiktok": {"name": "TikTok", "upload_url": "https://www.tiktok.com/tiktokstudio/upload"},
    "instagram": {"name": "Instagram Reels", "upload_url": "https://www.instagram.com/"},
}
LIMITS = {"youtube_title": 100, "youtube_description": 5000, "tiktok": 2200, "instagram": 2200, "instagram_tags": 30}

_tokens = JsonStore(DATA_DIR / "tokens.json", {})


# ---------------------------------------------------------------------------
# Тексты
# ---------------------------------------------------------------------------
def _tag(text: str) -> str:
    return re.sub(r"[^\wа-яА-ЯёЁ]", "", text or "")


def hashtags_for(clip: Dict[str, Any], n: int = 10) -> List[str]:
    base = []
    for name in (clip.get("anime_en"), clip.get("anime_ru"), clip.get("anime")):
        t = _tag(name or "")
        if t and len(t) <= 30 and t.lower() not in [b.lower() for b in base]:
            base.append(t)
    mood_tags = {"epic": ["animefight", "эпично"], "dark": ["dark", "phonk"], "twist": ["plottwist", "неожиданно"],
                 "emotional": ["sad", "грустно"], "romantic": ["romance", "романтика"], "funny": ["funny", "смешно"]}
    common = ["anime", "аниме", "animeedit", "animemoments", "аниме_моменты", "fyp", "рекомендации", "shorts"]
    out = base + mood_tags.get(clip.get("mood", "epic"), []) + common
    seen, res = set(), []
    for t in out:
        if t.lower() not in seen:
            seen.add(t.lower())
            res.append(t)
    return res[:n]


def captions_offline(clip: Dict[str, Any]) -> Dict[str, Any]:
    s = get_settings()
    anime = clip.get("anime_name") or clip.get("anime") or "аниме"
    hook = clip.get("hook") or clip.get("title") or anime
    credit = clip.get("credit") or f"Аниме: {anime}"
    question = {
        "epic": "Кто сильнее — пишите в комментах 👇",
        "dark": "Смогли бы вы досмотреть это ночью? 👇",
        "twist": "Вы догадались раньше? Честно 👇",
        "emotional": "Кто тоже сдерживал слёзы? 🥲",
        "romantic": "Лучшая пара этого аниме? 💞",
        "funny": "Отметь друга, который так же делает 😂",
    }.get(clip.get("mood", "epic"), "Что думаете? 👇")
    tg = f"\nБольше разборов: {s['telegram']}" if s.get("telegram") else ""
    tags = hashtags_for(clip, 12)
    yt_title = f"{hook} | {anime}"
    if len(yt_title) > 88:
        yt_title = hook[:88]
    return {
        "youtube": {
            "title": f"{yt_title} #shorts"[: LIMITS["youtube_title"]],
            "description": f"{question}\n\n🎬 {clip.get('title') or ''} {('— ' + clip['episode']) if clip.get('episode') else ''}\n"
                           f"{credit}. Фрагмент использован в формате обзора/комментария.{tg}\n\n"
                           + " ".join(f"#{t}" for t in tags[:8]),
            "tags": tags[:12],
        },
        "tiktok": {"caption": f"{hook} — {anime}. {question}\n{credit}{tg}\n" + " ".join(f"#{t}" for t in tags[:6])},
        "instagram": {"caption": f"{hook}\n\n{question}\n\n🎬 {anime}\n{credit}{tg}\n.\n" + " ".join(f"#{t}" for t in tags[:12])},
        "pinned_comment": question,
        "source": "template",
    }


def prepare_captions(clip_id: str, use_ai: bool = True) -> Dict[str, Any]:
    from . import gemini

    clip = library.require_clip(clip_id)
    caps: Dict[str, Any]
    if use_ai and gemini.available():
        try:
            caps = gemini.write_captions(clip, get_settings())
            caps["source"] = "gemini"
        except Exception as exc:  # noqa: BLE001
            log.warning("captions via Gemini failed: %s", exc)
            caps = captions_offline(clip)
            caps["warning"] = f"Gemini: {str(exc)[:120]}"
    else:
        caps = captions_offline(clip)
    # нормализация лимитов
    yt = caps.setdefault("youtube", {})
    yt["title"] = (yt.get("title") or "")[: LIMITS["youtube_title"]]
    if "#shorts" not in yt["title"].lower() and len(yt["title"]) < 92:
        yt["title"] += " #shorts"
    for p in ("tiktok", "instagram"):
        c = caps.setdefault(p, {})
        c["caption"] = (c.get("caption") or "")[: LIMITS[p]]
    ig = caps["instagram"]["caption"]
    tags = re.findall(r"#\w+", ig)
    if len(tags) > LIMITS["instagram_tags"]:
        for t in tags[LIMITS["instagram_tags"]:]:
            ig = ig.replace(t, "", 1)
        caps["instagram"]["caption"] = ig.strip()
    library.update_clip(clip_id, {"publish": {"captions": caps}}, note=f"Описания подготовлены ({caps.get('source')})")
    return caps


# ---------------------------------------------------------------------------
# Утилиты рабочего стола
# ---------------------------------------------------------------------------
def reveal_file(path: Path) -> None:
    try:
        if os.name == "nt":
            subprocess.Popen(["explorer", "/select,", str(path)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path.parent)])
    except OSError as exc:
        log.warning("reveal: %s", exc)


def assisted(clip_id: str, platform: str) -> Dict[str, Any]:
    """Ассистированная публикация: открыть загрузчик площадки и показать файл."""
    clip = library.require_clip(clip_id)
    f = resolve_work(clip.get("final_file") or "")
    reveal_file(f)
    webbrowser.open(PLATFORMS[platform]["upload_url"])
    library.update_clip(clip_id, {"publish": {"status": {platform: {"state": "assisted", "t": time.time()}}}},
                        note=f"{PLATFORMS[platform]['name']}: открыт загрузчик")
    return {"state": "assisted", "file": str(f), "url": PLATFORMS[platform]["upload_url"]}


def mark_published(clip_id: str, platform: str, url: str = "") -> Dict[str, Any]:
    library.update_clip(clip_id, {"publish": {"status": {platform: {"state": "published", "url": url, "t": time.time()}}}},
                        note=f"{PLATFORMS[platform]['name']}: опубликовано")
    return library.advance_stage(clip_id, "published")


# ---------------------------------------------------------------------------
# YouTube Data API v3
# ---------------------------------------------------------------------------
YT_SCOPE = "https://www.googleapis.com/auth/youtube.upload"


def _yt_client() -> Dict[str, str]:
    path = (get_settings().get("youtube_client_secret") or "").strip()
    if not path or not Path(path).exists():
        raise RuntimeError("Укажите путь к client_secret.json (Google Cloud → OAuth-клиент «Приложение для ПК»)")
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return data.get("installed") or data.get("web") or data


def youtube_status() -> Dict[str, Any]:
    tok = _tokens.load().get("youtube") or {}
    configured = bool((get_settings().get("youtube_client_secret") or "").strip())
    return {"configured": configured, "connected": bool(tok.get("refresh_token")), "channel": tok.get("channel", "")}


def youtube_auth_url(redirect_uri: str) -> str:
    c = _yt_client()
    state = secrets.token_urlsafe(16)
    with _tokens.lock:
        _tokens.load()["youtube_state"] = {"state": state, "redirect_uri": redirect_uri}
        _tokens.save()
    q = {"client_id": c["client_id"], "redirect_uri": redirect_uri, "response_type": "code", "scope": YT_SCOPE,
         "access_type": "offline", "prompt": "consent", "state": state}
    return (c.get("auth_uri") or "https://accounts.google.com/o/oauth2/auth") + "?" + urllib.parse.urlencode(q)


def youtube_finish_auth(code: str, state: str) -> Dict[str, Any]:
    c = _yt_client()
    st = _tokens.load().get("youtube_state") or {}
    if not st or st.get("state") != state:
        raise RuntimeError("Неверный state OAuth — повторите подключение")
    r = requests.post(c.get("token_uri") or "https://oauth2.googleapis.com/token", data={
        "code": code, "client_id": c["client_id"], "client_secret": c.get("client_secret", ""),
        "redirect_uri": st["redirect_uri"], "grant_type": "authorization_code"}, timeout=30)
    r.raise_for_status()
    tok = r.json()
    tok["expires_at"] = time.time() + tok.get("expires_in", 3600) - 60
    with _tokens.lock:
        _tokens.load()["youtube"] = tok
        _tokens.load().pop("youtube_state", None)
        _tokens.save()
    return youtube_status()


def _yt_access_token() -> str:
    with _tokens.lock:
        tok = _tokens.load().get("youtube") or {}
    if not tok.get("refresh_token") and not tok.get("access_token"):
        raise RuntimeError("YouTube не подключён — нажмите «Подключить YouTube» в настройках")
    if tok.get("access_token") and tok.get("expires_at", 0) > time.time():
        return tok["access_token"]
    c = _yt_client()
    r = requests.post(c.get("token_uri") or "https://oauth2.googleapis.com/token", data={
        "client_id": c["client_id"], "client_secret": c.get("client_secret", ""),
        "refresh_token": tok["refresh_token"], "grant_type": "refresh_token"}, timeout=30)
    r.raise_for_status()
    new = r.json()
    with _tokens.lock:
        t = _tokens.load().setdefault("youtube", {})
        t["access_token"] = new["access_token"]
        t["expires_at"] = time.time() + new.get("expires_in", 3600) - 60
        _tokens.save()
    return new["access_token"]


def youtube_upload(clip_id: str, task=None) -> Dict[str, Any]:
    clip = library.require_clip(clip_id)
    caps = (clip.get("publish") or {}).get("captions") or prepare_captions(clip_id)
    yt = caps["youtube"]
    f = resolve_work(clip["final_file"])
    size = f.stat().st_size
    token = _yt_access_token()
    meta = {
        "snippet": {"title": yt["title"][:100], "description": yt.get("description", "")[:5000],
                    "tags": yt.get("tags", [])[:15], "categoryId": "1"},  # 1 = Film & Animation
        "status": {"privacyStatus": get_settings().get("youtube_privacy", "private"),
                   "selfDeclaredMadeForKids": False, "containsSyntheticMedia": False},
    }
    init = requests.post("https://www.googleapis.com/upload/youtube/v3/videos",
                         params={"uploadType": "resumable", "part": "snippet,status"},
                         headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8",
                                  "X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(size)},
                         data=json.dumps(meta), timeout=60)
    if init.status_code >= 400:
        raise RuntimeError(f"YouTube init {init.status_code}: {init.text[:300]}")
    upload_url = init.headers["Location"]
    if task:
        task.update(0.2, "Загрузка на YouTube…")
    with open(f, "rb") as fh:
        up = requests.put(upload_url, data=fh, headers={"Content-Type": "video/mp4", "Content-Length": str(size)},
                          timeout=1800)
    if up.status_code >= 400:
        raise RuntimeError(f"YouTube upload {up.status_code}: {up.text[:300]}")
    vid = up.json().get("id")
    url = f"https://youtube.com/shorts/{vid}"
    library.update_clip(clip_id, {"publish": {"status": {"youtube": {"state": "published", "url": url, "id": vid,
                                                                      "t": time.time()}}}},
                        note=f"YouTube: загружено ({meta['status']['privacyStatus']})")
    library.advance_stage(clip_id, "published")
    return {"state": "published", "url": url}


# ---------------------------------------------------------------------------
# TikTok Content Posting API (inbox upload)
# ---------------------------------------------------------------------------
TT_API = "https://open.tiktokapis.com/v2/post/publish"


def tiktok_chunks(size: int, chunk: int = 10 * 1024 * 1024) -> List[tuple]:
    """Разбиение по правилам TikTok: чанки 5–64 МБ, последний поглощает остаток; < 5 МБ — одним куском."""
    if size <= 64 * 1024 * 1024:
        return [(0, size - 1)]
    n = max(1, size // chunk)
    ranges = []
    for i in range(n):
        start = i * chunk
        end = size - 1 if i == n - 1 else start + chunk - 1
        ranges.append((start, end))
    return ranges


def tiktok_upload(clip_id: str, task=None) -> Dict[str, Any]:
    token = (get_settings().get("tiktok_access_token") or "").strip()
    if not token:
        raise RuntimeError("Нет TikTok access token (scope video.upload) — используйте ассистированную загрузку")
    clip = library.require_clip(clip_id)
    f = resolve_work(clip["final_file"])
    size = f.stat().st_size
    ranges = tiktok_chunks(size)
    chunk_size = ranges[0][1] - ranges[0][0] + 1
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8"}
    init = requests.post(f"{TT_API}/inbox/video/init/", headers=headers, timeout=60, json={
        "source_info": {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk_size,
                        "total_chunk_count": len(ranges)}})
    data = init.json()
    if init.status_code >= 400 or (data.get("error", {}).get("code") not in (None, "ok")):
        raise RuntimeError(f"TikTok init: {data.get('error') or init.text[:300]}")
    upload_url = data["data"]["upload_url"]
    publish_id = data["data"]["publish_id"]
    with open(f, "rb") as fh:
        for i, (a, b) in enumerate(ranges):
            fh.seek(a)
            body = fh.read(b - a + 1)
            r = requests.put(upload_url, data=body, timeout=600, headers={
                "Content-Type": "video/mp4", "Content-Length": str(len(body)), "Content-Range": f"bytes {a}-{b}/{size}"})
            if r.status_code >= 400:
                raise RuntimeError(f"TikTok upload chunk {i}: {r.status_code} {r.text[:200]}")
            if task:
                task.update(0.2 + 0.7 * (i + 1) / len(ranges), f"TikTok: {i + 1}/{len(ranges)}")
    library.update_clip(clip_id, {"publish": {"status": {"tiktok": {"state": "inbox", "publish_id": publish_id,
                                                                     "t": time.time()}}}},
                        note="TikTok: отправлено во входящие — завершите публикацию в приложении")
    return {"state": "inbox", "publish_id": publish_id,
            "hint": "Откройте TikTok → Входящие → уведомление о загрузке, вставьте подпись (она в буфере)"}


def tiktok_status() -> Dict[str, Any]:
    return {"configured": bool((get_settings().get("tiktok_access_token") or "").strip())}

