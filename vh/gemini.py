"""ИИ-слой VideoHook: Google Gemini (подписка Google AI Pro).

Два канала, выбираются автоматически (приоритет — Antigravity: модель мощнее, лимиты подписки выше):
  1. установленный Google Antigravity (agentapi) — без ключа, через файлы задач;
  2. Gemini API (ключ из aistudio.google.com) — запасной канал: Files API, JSON-ответы.
Если ни один канал не доступен, модуль сообщает об этом, а вызывающий код использует офлайн-эвристику.
"""

from __future__ import annotations

import json
import logging
import mimetypes
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from . import agbridge
from .core import get_settings

log = logging.getLogger("videohook.gemini")
API = "https://generativelanguage.googleapis.com"


class GeminiUnavailable(RuntimeError):
    pass


def _key() -> str:
    return (get_settings().get("gemini_api_key") or os.environ.get("GEMINI_API_KEY") or "").strip()


def _use_antigravity() -> bool:
    return bool(get_settings().get("ai_via_antigravity", True)) and agbridge.available()


def available() -> bool:
    return bool(_key()) or _use_antigravity()


def can_watch_video() -> bool:
    """Анализ видео: API загружает файл целиком, Antigravity смотрит файл/раскадровку."""
    return available()


def status() -> Dict[str, Any]:
    ag = _use_antigravity()
    return {"api": bool(_key()), "antigravity": ag, "model": get_settings().get("gemini_model"),
            "channel": "antigravity" if ag else ("api" if _key() else "")}


def _api_call(prompt: str, files: Optional[List[Dict[str, Any]]], want_json: bool, temperature: float) -> str:
    key = _key()
    model = get_settings().get("gemini_model") or "gemini-flash-latest"
    parts: List[Dict[str, Any]] = []
    for f in files or []:
        parts.append({"file_data": {"mime_type": f.get("mimeType", "video/mp4"), "file_uri": f["uri"]}})
    parts.append({"text": prompt})
    body: Dict[str, Any] = {"contents": [{"role": "user", "parts": parts}],
                            "generationConfig": {"temperature": temperature}}
    if want_json:
        body["generationConfig"]["responseMimeType"] = "application/json"
    r = requests.post(f"{API}/v1beta/models/{model}:generateContent", params={"key": key}, json=body, timeout=300)
    if r.status_code >= 400:
        raise RuntimeError(f"Gemini API {r.status_code}: {r.text[:400]}")
    cands = r.json().get("candidates") or []
    if not cands:
        raise RuntimeError("Gemini вернул пустой ответ (возможно, фильтр безопасности)")
    return "".join(p.get("text", "") for p in cands[0].get("content", {}).get("parts", []))


# ---------------------------------------------------------------------------
# Низкоуровневые вызовы
# ---------------------------------------------------------------------------
def upload_file(path: str | Path, wait: bool = True) -> Dict[str, Any]:
    """Загрузка файла через Files API (resumable), ожидание статуса ACTIVE."""
    key = _key()
    if not key:
        raise GeminiUnavailable("Нет API-ключа Gemini")
    p = Path(path)
    mime = mimetypes.guess_type(p.name)[0] or "video/mp4"
    size = p.stat().st_size
    start = requests.post(
        f"{API}/upload/v1beta/files",
        params={"key": key},
        headers={
            "X-Goog-Upload-Protocol": "resumable",
            "X-Goog-Upload-Command": "start",
            "X-Goog-Upload-Header-Content-Length": str(size),
            "X-Goog-Upload-Header-Content-Type": mime,
            "Content-Type": "application/json",
        },
        json={"file": {"display_name": p.name[:100]}},
        timeout=60,
    )
    start.raise_for_status()
    upload_url = start.headers.get("X-Goog-Upload-URL") or start.headers.get("x-goog-upload-url")
    if not upload_url:
        raise RuntimeError("Gemini Files API не вернул upload URL")
    with open(p, "rb") as fh:
        up = requests.post(upload_url, headers={
            "Content-Length": str(size), "X-Goog-Upload-Offset": "0", "X-Goog-Upload-Command": "upload, finalize",
        }, data=fh, timeout=600)
    up.raise_for_status()
    f = up.json().get("file", {})
    name = f.get("name")
    deadline = time.time() + 300
    while wait and f.get("state") == "PROCESSING" and time.time() < deadline:
        time.sleep(3)
        r = requests.get(f"{API}/v1beta/{name}", params={"key": key}, timeout=30)
        r.raise_for_status()
        f = r.json()
    if f.get("state") == "FAILED":
        raise RuntimeError("Gemini не смог обработать видео")
    return f


def generate(prompt: str, want_json: bool = True, temperature: float = 0.7,
             local_paths: Optional[List[str]] = None, task_name: str = "ai", progress=None) -> Any:
    """Генерация: сначала Antigravity, при недоступности или ошибке — Gemini API.

    local_paths — файлы (видео, раскадровка), которые должна посмотреть модель.
    Возвращает строку (API) или уже разобранный JSON/текст (Antigravity) — parse_json() принимает оба."""
    errors = []
    if _use_antigravity():
        try:
            return agbridge.run_task(task_name, prompt, expect="json" if want_json else "text",
                                     files=local_paths, timeout=900 if local_paths else 420, progress=progress)
        except agbridge.AntigravityError as exc:
            log.warning("Antigravity %s: %s", task_name, exc)
            errors.append(f"Antigravity: {exc}")
    if _key():
        files = None
        videos = [p for p in local_paths or [] if Path(p).suffix.lower() in (".mp4", ".mkv", ".webm", ".mov")]
        if videos:
            if progress:
                progress("Gemini API: загружаю видео…")
            files = [upload_file(videos[0])]
        return _api_call(prompt, files, want_json, temperature)
    if errors:
        raise RuntimeError("; ".join(errors))
    raise GeminiUnavailable("ИИ не подключён: запустите Antigravity или добавьте API-ключ Gemini")


def parse_json(text: Any) -> Any:
    if isinstance(text, (dict, list)):
        return text
    text = str(text).strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        return json.loads(text)
    except ValueError:
        m = re.search(r"(\{.*\}|\[.*\])", text, re.S)
        if m:
            return json.loads(m.group(1))
        raise


# ---------------------------------------------------------------------------
# Прикладные функции
# ---------------------------------------------------------------------------
MOODS = "epic | dark | twist | emotional | romantic | funny"

# Как писать хук/подпись/описание в зависимости от настроения сцены
MOOD_VOICE = {
    "epic": "хук обещает зрелище и силу («Он превзошёл сильнейшего…»), подпись — ключевая фраза момента, вопрос зрителям про «кто сильнее»",
    "dark": "хук интригует жутью или жестокостью без спойлера развязки, тон сдержанный, без смайлов-шуток",
    "twist": "хук ставит загадку и НЕ раскрывает поворот («Никто не ожидал, кто он на самом деле»), вопрос «вы догадались раньше?»",
    "emotional": "хук про чувства и потерю, мягкий тон, без кликбейта и капса, вопрос про пережитые эмоции",
    "romantic": "хук про отношения героев, тёплый тон, вопрос про любимую пару",
    "funny": "хук короткий и абсурдный, подпись — смешная реплика, вопрос-челлендж «отметь друга»",
}


def _voice(mood: str) -> str:
    return MOOD_VOICE.get(mood or "epic", MOOD_VOICE["epic"])


def suggest_moments(anime_name: str, known_titles: List[str], count: int = 5,
                    genres: Optional[List[str]] = None, anime_en: str = "") -> List[Dict[str, Any]]:
    """Новые вирусные моменты тайтла, которых ещё нет в базе."""
    known = "\n".join(f"- {t}" for t in known_titles[:60]) or "- (пока нет)"
    prompt = f"""Ты — редактор вирусного аниме-канала в TikTok/Reels/Shorts. Тайтл: «{anime_name}» (англ.: {anime_en or anime_name}; жанры: {', '.join(genres or []) or 'н/д'}).
Предложи {count} НОВЫХ самых узнаваемых и вирусных сцен этого тайтла, которых НЕТ в списке уже использованных:
{known}

Требования:
- только реальные сцены из аниме-адаптации (не выдумывай, не бери сцены только из манги);
- разнообразие настроений (бои, эмоции, юмор, повороты) — не больше двух сцен одного настроения;
- сцена укладывается в 10–60 секунд экранного времени;
- поисковые запросы должны находить на YouTube именно эту сцену из аниме: имена персонажей + действие + название тайтла;
  НЕ добавляй слова amv, edit, reaction, review, manga, trailer, opening.
Для каждой сцены верни объект:
{{"title": "короткое название сцены на русском", "episode": "сезон/серия или арка", "query": "запрос для YouTube на русском",
"query_en": "YouTube search query in English: <anime title> <characters> <action> scene", "hook": "цепляющий заголовок до 45 символов без эмодзи",
"mood": "{MOODS}", "why": "почему это залетит (1 фраза)"}}
Верни JSON-объект {{"moments": [...]}}."""
    data = parse_json(generate(prompt, temperature=0.9, task_name="moments"))
    items = data.get("moments", []) if isinstance(data, dict) else data
    return [m for m in items or [] if isinstance(m, dict) and m.get("title")]


def analyze_video(path: str | Path, context: str, target_seconds: int = 35, mood: str = "",
                  hook: str = "", hints: Optional[List[Dict[str, Any]]] = None,
                  storyboard: Optional[str] = None, progress=None) -> Dict[str, Any]:
    """Смысловой разбор видео: лучшие отрезки, хук, реплики, идеи монтажа."""
    lo, hi = max(10, target_seconds - 15), target_seconds + 10
    hint_txt = ""
    if hints:
        rows = [f"  - {h['start']:.0f}–{h['end']:.0f} c ({ {'heatmap': 'зрители YouTube пересматривают чаще всего', 'local': 'пик громкости и динамики монтажа'}.get(h.get('source'), h.get('source', '')) })"
                for h in hints[:4]]
        hint_txt = "Объективные сигналы (проверь их в первую очередь, но решай по содержанию):\n" + "\n".join(rows) + "\n"
    sb_txt = ""
    if storyboard:
        sb_txt = ("Также есть раскадровка 4×4 (кадры слева направо, сверху вниз, равномерно по времени) — "
                  "используй её, если не можешь посмотреть видео целиком.\n")
    prompt = f"""Проанализируй видео — это фрагмент аниме ({context}).
Настроение сцены: {mood or 'определи сам'}.{f' Рабочий хук: «{hook}».' if hook else ''}
Задача: выбрать отрезки для вирусного вертикального ролика длиной {lo}–{hi} секунд.
Критерии: сильный визуальный хук в первые 2 секунды, кульминация внутри отрезка, законченная мысль, возможность зациклить.
Избегай: заставок, титров, чёрных кадров, чужих надписей/водяных знаков на весь экран, статичных кадров без действия.
{hint_txt}{sb_txt}Тон текстов: {_voice(mood)}.
Верни JSON:
{{"segments": [{{"start": сек, "end": сек, "score": 0..1, "reason": "почему"}}] (до 4, по убыванию score),
 "hook": "заголовок-хук до 45 символов без эмодзи",
 "caption": "короткая подпись/цитата на русском для экрана, до 70 символов",
 "key_lines": [{{"t": сек, "text": "реплика на русском"}}] (до 8 ключевых реплик внутри лучшего отрезка; если речи нет — пустой список),
 "edit_ideas": ["идеи монтажа: зум на секунде X, стоп-кадр, смена ритма…"],
 "mood": "{MOODS}"}}
Время — в секундах от начала этого файла."""
    local = [str(path)] + ([storyboard] if storyboard else [])
    data = parse_json(generate(prompt, local_paths=local, temperature=0.4, task_name="analyze", progress=progress))
    if not isinstance(data, dict):
        raise RuntimeError("Gemini вернул неожиданный формат анализа")
    segs = []
    for s in data.get("segments", []):
        try:
            st, en = float(s["start"]), float(s["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if en > st:
            segs.append({"start": round(st, 2), "end": round(en, 2), "score": float(s.get("score", 0.8)),
                         "source": "gemini", "reason": s.get("reason", "Gemini")})
    data["segments"] = segs
    return data


def write_captions(clip: Dict[str, Any], settings: Dict[str, Any]) -> Dict[str, Any]:
    """Описания под каждую площадку (с учётом их лимитов и настроения сцены)."""
    mood = clip.get("mood") or "epic"
    lines = "; ".join(k.get("text", "") for k in (clip.get("key_lines") or [])[:4] if isinstance(k, dict))
    prompt = f"""Сделай тексты для публикации вертикального аниме-ролика.
Аниме: {clip.get('anime_name') or clip.get('anime')}
Студия: {clip.get('studio') or 'н/д'}
Сцена: {clip.get('title')} ({clip.get('episode') or ''})
Хук на экране: {clip.get('hook')}
Подпись на экране: {clip.get('caption') or 'нет'}
Реплики: {lines or 'нет'}
Настроение: {mood} — {_voice(mood)}
Канал автора: {settings.get('brand_handle')}; Telegram: {settings.get('telegram') or 'нет'}

Правила: язык — русский; первая строка — интрига/вопрос, вызывающий комментарии; не спойлерить развязку, если настроение twist;
обязательно указать название аниме и кредит правообладателю (© студия) — ролик сделан как фан-обзор;
без кликбейта про «полную серию» и без ссылок на пиратские сайты; хэштеги — по тайтлу, персонажам и жанру.
Верни JSON:
{{"youtube": {{"title": "до 90 символов, в конце #shorts", "description": "до 600 символов", "tags": ["до 12 тегов без #"]}},
 "tiktok": {{"caption": "до 300 символов, 4–6 хэштегов в конце"}},
 "instagram": {{"caption": "до 400 символов, 8–12 хэштегов в конце"}},
 "pinned_comment": "вопрос зрителям для закрепа"}}"""
    data = parse_json(generate(prompt, temperature=0.8, task_name="captions"))
    if not isinstance(data, dict) or "youtube" not in data:
        raise RuntimeError("Gemini вернул неожиданный формат описаний")
    return data
