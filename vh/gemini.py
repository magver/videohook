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
from typing import Any, Dict, List, Optional, Tuple

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
                  storyboard: Optional[str] = None, audio: Optional[str] = None, max_seconds: int = 58,
                  ru_audio: bool = False, progress=None) -> Dict[str, Any]:
    """Смысловой разбор видео: цельный отрезок-история, акценты, замедление, субтитры, хук, идеи монтажа."""
    lo, hi = max(15, target_seconds - 10), max_seconds
    hint_txt = ""
    if hints:
        rows = [f"  - {h['start']:.0f}–{h['end']:.0f} c ({ {'heatmap': 'зрители YouTube пересматривают чаще всего', 'local': 'пик громкости и динамики монтажа'}.get(h.get('source'), h.get('source', '')) })"
                for h in hints[:4]]
        hint_txt = ("Объективные сигналы — где находится пик интереса (кульминация). Отрезок должен ВКЛЮЧАТЬ пик "
                    "вместе с его завязкой и развязкой, а не начинаться с него:\n" + "\n".join(rows) + "\n")
    media = []
    if storyboard:
        media.append("раскадровка 4×4 (кадры слева направо, сверху вниз, равномерно по времени)")
    if audio:
        media.append("аудиодорожка WAV того же файла — слушай её для реплик и субтитров")
    media_txt = ("Дополнительно даны: " + "; ".join(media) + ".\n") if media else ""
    subs_rule = ("Звук на русском (русская озвучка): субтитры — точная расшифровка русской речи."
                 if ru_audio else
                 "Если речь не на русском — переведи на естественный разговорный русский (не дословно), "
                 "сохраняя смысл, характер персонажа и длину фразы.")
    prompt = f"""Проанализируй видео — это фрагмент аниме ({context}).
Настроение сцены: {mood or 'определи сам'}.{f' Рабочий хук: «{hook}».' if hook else ''}
{media_txt}
ЗАДАЧА: выбрать отрезок для вертикального ролика, который зритель поймёт БЕЗ контекста и досмотрит до конца.
Длина {lo}–{hi} c: бери столько, сколько нужно для законченной истории, не укорачивай ради цифры.

Правила выбора границ (самое важное):
1. Отрезок — законченная мини-история: завязка (кто, что происходит, 2–6 c) → нарастание → кульминация → развязка/реакция.
2. НАЧАЛО — на смене плана или в паузе ПЕРЕД первой репликой завязки. Никогда не начинай посреди фразы или движения.
3. КОНЕЦ — после последней реплики и реакции на кульминацию (взгляд, падение, тишина, реакция других героев),
   на смене плана или в паузе. Никогда не обрывай фразу, удар или музыкальную фразу. Лучше на 3 c длиннее, чем обрезать суть.
4. Если сцена длиннее {hi} c — не режь её пополам: верни 2–4 части ("parts"), выбросив затянутую середину
   (повторы, долгие проходы, флешбеки), но сохранив завязку и финал. Каждая часть тоже начинается и заканчивается на границе фразы.
5. Избегай заставок, титров, превью следующей серии, чёрных кадров, чужих водяных знаков на весь экран.
{hint_txt}
Монтаж (время — в секундах от начала этого файла):
- "accents": 2–6 ударных моментов (удар, взрыв, резкий поворот головы, ключевое слово) — туда встанут панч-зум, вспышка и низкий удар звука;
- "slowmo": один момент кульминации для замедления (t — центр, dur 0.8–1.6 c), или null, если замедление убьёт сцену (диалог, комедия);
- "transition": переход между частями — fadewhite (эпик), fadeblack (мрак, драма), dissolve (эмоции), zoomin (поворот), slideleft (комедия), cut;
- музыку не предлагай: используется родной звук сцены.

Субтитры: "subtitles" — ВСЕ реплики внутри выбранного отрезка (всех частей), по одной фразе, start/end по речи.
{subs_rule} Если речи нет — пустой список. "dialogue_heavy": true, если сцена держится на диалоге.
Тон хука/подписи: {_voice(mood)}.

Верни JSON:
{{"parts": [{{"start": сек, "end": сек}}] (1–4 части по порядку; одна часть, если сцена укладывается),
 "segments": [{{"start": сек, "end": сек, "score": 0..1, "reason": "почему"}}] (до 3 альтернатив цельных отрезков, по убыванию score; первый = охват parts),
 "story": "одной фразой: завязка → кульминация → развязка",
 "hook": "заголовок-хук до 45 символов без эмодзи",
 "caption": "короткая подпись/контекст на русском для экрана, до 70 символов",
 "accents": [сек, ...],
 "slowmo": {{"t": сек, "dur": сек}} или null,
 "transition": "…",
 "subtitles": [{{"start": сек, "end": сек, "text": "реплика на русском"}}],
 "dialogue_heavy": true/false,
 "edit_ideas": ["что ещё сделать в монтаже"],
 "mood": "{MOODS}"}}"""
    local = [str(path)] + [p for p in (storyboard, audio) if p]
    data = parse_json(generate(prompt, local_paths=local, temperature=0.3, task_name="analyze", progress=progress))
    if not isinstance(data, dict):
        raise RuntimeError("Gemini вернул неожиданный формат анализа")

    def _span(x: Any) -> Optional[Tuple[float, float]]:
        try:
            st, en = float(x["start"]), float(x["end"])
        except (KeyError, TypeError, ValueError):
            return None
        return (st, en) if en > st else None

    segs = []
    for s in data.get("segments") or []:
        sp = _span(s)
        if sp:
            segs.append({"start": round(sp[0], 2), "end": round(sp[1], 2), "score": float(s.get("score", 0.8)),
                         "source": "gemini", "reason": s.get("reason", "Gemini")})
    parts = [sp for sp in (_span(p) for p in data.get("parts") or []) if sp]
    parts.sort()
    if parts:
        whole = {"start": round(parts[0][0], 2), "end": round(parts[-1][1], 2), "score": 1.0, "source": "gemini",
                 "reason": data.get("story") or "Цельная история по Gemini"}
        segs = [whole] + [s for s in segs if abs(s["start"] - whole["start"]) > 1 or abs(s["end"] - whole["end"]) > 1]
    data["segments"] = segs
    data["parts"] = [[round(a, 2), round(b, 2)] for a, b in parts]
    subs = []
    for x in data.get("subtitles") or []:
        sp = _span(x)
        if sp and str(x.get("text", "")).strip():
            subs.append({"start": round(sp[0], 2), "end": round(sp[1], 2), "text": str(x["text"]).strip()})
    data["subtitles"] = subs
    acc = []
    for a in data.get("accents") or []:
        try:
            acc.append(round(float(a if not isinstance(a, dict) else a.get("t")), 2))
        except (TypeError, ValueError):
            continue
    data["accents"] = sorted(set(acc))
    sm = data.get("slowmo")
    try:
        data["slowmo"] = {"t": float(sm["t"]), "dur": max(0.6, min(2.0, float(sm.get("dur", 1.2)))), "factor": 0.5} if sm else None
    except (KeyError, TypeError, ValueError):
        data["slowmo"] = None
    return data


def translate_subtitles(items: List[Dict[str, Any]], context: str, lang: str = "") -> List[Dict[str, Any]]:
    """Перевод субтитров на русский с сохранением таймингов (для роликов без русской озвучки)."""
    if not items:
        return []
    rows = [{"i": i, "text": x["text"]} for i, x in enumerate(items)]
    prompt = f"""Переведи субтитры сцены аниме ({context}) на естественный разговорный русский{f' с языка {lang}' if lang else ''}.
Правила: сохраняй характер речи персонажей и имена в русской традиции перевода; не добавляй пояснений;
если строка — автоматическое распознавание с ошибками, восстанови смысл по контексту; звуки и [музыка] — пустая строка.
Строк столько же, сколько на входе, те же номера i.
Вход: {json.dumps(rows, ensure_ascii=False)}
Верни JSON: {{"lines": [{{"i": 0, "text": "перевод"}}, ...]}}"""
    data = parse_json(generate(prompt, temperature=0.3, task_name="subtitles"))
    lines = data.get("lines", []) if isinstance(data, dict) else data
    tr = {int(x["i"]): str(x.get("text", "")).strip() for x in lines or [] if isinstance(x, dict) and "i" in x}
    return [{**x, "text": tr[i]} for i, x in enumerate(items) if tr.get(i)]


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
Канал автора: {settings.get('channel_name') or settings.get('brand_handle')} ({settings.get('brand_handle')})

Правила: язык — русский; первая строка — интрига/вопрос, вызывающий комментарии; не спойлерить развязку, если настроение twist;
обязательно указать название аниме и кредит правообладателю (© студия) — ролик сделан как фан-обзор;
без кликбейта про «полную серию» и без ссылок на пиратские сайты; хэштеги — по тайтлу, персонажам и жанру.
НЕ добавляй ссылки, подпись канала и призывы подписаться — они добавляются автоматически из настроек канала.
Верни JSON:
{{"youtube": {{"title": "до 90 символов, в конце #shorts", "description": "до 600 символов", "tags": ["до 12 тегов без #"]}},
 "tiktok": {{"caption": "до 300 символов, 4–6 хэштегов в конце"}},
 "instagram": {{"caption": "до 400 символов, 8–12 хэштегов в конце"}},
 "pinned_comment": "вопрос зрителям для закрепа"}}"""
    data = parse_json(generate(prompt, temperature=0.8, task_name="captions"))
    if not isinstance(data, dict) or "youtube" not in data:
        raise RuntimeError("Gemini вернул неожиданный формат описаний")
    return data
