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
             local_paths: Optional[List[str]] = None, task_name: str = "ai", progress=None,
             chat: Optional[Dict[str, Any]] = None) -> Any:
    """Генерация: сначала Antigravity, при недоступности или ошибке — Gemini API.

    local_paths — файлы (видео, раскадровка), которые должна посмотреть модель.
    Возвращает строку (API) или уже разобранный JSON/текст (Antigravity) — parse_json() принимает оба."""
    errors = []
    if _use_antigravity():
        try:
            return agbridge.run_task(task_name, prompt, expect="json" if want_json else "text",
                                     files=local_paths, timeout=1500 if local_paths else 480, progress=progress,
                                     chat=chat)
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
"mood": "{MOODS}", "why": "почему это залетит (1 фраза)", "virality": "шанс успеха шортса 0–100, строго"}}
Верни JSON-объект {{"moments": [...]}}."""
    data = parse_json(generate(prompt, temperature=0.9, task_name="moments"))
    items = data.get("moments", []) if isinstance(data, dict) else data
    return [m for m in items or [] if isinstance(m, dict) and m.get("title")]


def analyze_video(path: str | Path, context: str, target_seconds: int = 35, mood: str = "",
                  hook: str = "", hints: Optional[List[Dict[str, Any]]] = None,
                  storyboard: Optional[Any] = None, audio: Optional[str] = None, max_seconds: int = 58,
                  ru_audio: bool = False, progress=None, chat: Optional[Dict[str, Any]] = None,
                  duration: float = 0.0, min_seconds: int = 35) -> Dict[str, Any]:
    """Глубокий разбор исходника: смысловая карта (биты) всего файла, кульминация, акценты, субтитры, хук.

    Какие куски войдут в ролик, решает vh.scenes.plan_from_beats — по принципу «максимум, сокращаем пустое»."""
    sheets = storyboard if isinstance(storyboard, list) else ([storyboard] if storyboard else [])
    hint_txt = ""
    if hints:
        names = {"heatmap": "зрители YouTube пересматривают чаще всего", "local": "пик громкости и динамики монтажа"}
        rows = [f"  - {h['start']:.0f}–{h['end']:.0f} c ({names.get(h.get('source'), h.get('source', ''))})"
                for h in hints[:4]]
        hint_txt = "Объективные сигналы интереса зрителей (проверь, совпадают ли с кульминацией):\n" + "\n".join(rows)
    subs_rule = ("Звук на русском (русская озвучка): субтитры — точная расшифровка речи."
                 if ru_audio else
                 "Речь не на русском — переводи на живой разговорный русский: смысл, характер и эмоция персонажа важнее "
                 "дословности; имена — в принятой русской транскрипции; длина фразы примерно как у оригинала.")
    media = []
    if sheets:
        media.append(f"раскадровки ({len(sheets)} шт.): кадр каждую секунду, таймкод в левом верхнем углу каждого кадра — "
                     "по ним определяй время событий с точностью до секунды")
    if audio:
        media.append("WAV-дорожка файла — прослушай её ЦЕЛИКОМ: реплики, интонации, удары, музыка, тишина")
    media.append("само видео (если инструмент позволяет его открыть)")
    prompt = f"""Ты — опытный монтажёр вирусных аниме-шортсов и сценарист. Перед тобой исходник: {context}.
Длительность файла: {duration:.1f} c. Настроение по базе: {mood or 'не задано'}.{f' Рабочий хук: «{hook}».' if hook else ''}
Материалы: {'; '.join(media)}.
{hint_txt}

Цель: ролик {min_seconds}–{max_seconds} c, который зритель без контекста ПОЙМЁТ и досмотрит. Главная ошибка, которой нельзя
допустить: обрезанная завязка, выброшенные реплики, оборванный финал. Длинный цельный ролик лучше короткого рваного.
Монтажные решения (что оставить) примет программа по твоей разметке — поэтому разметка должна быть честной и подробной.

ШАГ 1. Восприятие. Просмотри ВСЕ раскадровки по порядку и прослушай звук целиком. Не пропускай середину файла.
Выпиши для себя: кто в кадре, что происходит, кто что говорит, где звучат удары/крики/музыка, где тишина.

ШАГ 2. Смысловая карта ("beats"). Раздели ВЕСЬ файл от 0 до {duration:.1f} c на последовательные биты по 2–10 c, БЕЗ пропусков и
перекрытий (конец одного = начало следующего). Граница бита — смена плана, конец фразы или смена действия.
Для каждого бита:
  - "what": что происходит (конкретно: «Гай открывает восьмые врата, вокруг пар», а не «сцена боя»);
  - "speech": true, если в бите звучит реплика (любая речь персонажей). Не путай с криками без слов;
  - "line": реплика на русском (если есть);
  - "importance" 0–10: насколько бит нужен, чтобы понять историю (завязка, мотив, ключевая фраза — высокая);
  - "intensity" 0–10: динамика и эмоция (удары, движение камеры, крик, слёзы — высокая; статичный кадр, тишина — низкая);
  - "role": intro_outro (заставка, титры, превью серии, логотипы) | setup (завязка: кто и почему) | build (нарастание) |
    climax (кульминация) | payoff (развязка, итог) | reaction (реакция героев на произошедшее) | filler (проход, пауза без смысла).
Оценки ставь по смыслу. Диалог, раскрывающий мотив героя, — важен, даже если в кадре ничего не двигается.

ШАГ 3. Режиссура.
  - "peak": секунда главной кульминации;
  - "story": одной фразой завязка → кульминация → развязка;
  - "accents": 4–10 секунд ударных моментов по всему файлу (удар, вспышка силы, резкий поворот, ключевое слово) —
    туда встанут панч-зум, вспышка и звуковой удар; распредели их по всему действию, а не только в кульминации;
  - "slowmo": {{"t": секунда, "dur": 0.8–1.6}} — один самый зрелищный момент для замедления или null (диалог, комедия);
  - "hook": заголовок до 45 символов без эмодзи; тон: {_voice(mood)};
  - "caption": короткий контекст для зрителя без спойлера, до 70 символов;
  - "subtitles": ВСЕ реплики файла по одной фразе с точными start/end по звуку. {subs_rule} Если речи нет — [];
  - "dialogue_heavy": true, если сцена держится на диалоге;
  - "mood": {MOODS};
  - "virality": 0–100 — шанс, что ролик залетит (узнаваемость сцены, эмоция, понятность без контекста, хук);
  - "virality_why": одна фраза почему;
  - "edit_ideas": 3–6 конкретных идей монтажа с секундами.

Верни ОДИН JSON:
{{"beats": [{{"start": 0.0, "end": 4.5, "what": "…", "speech": false, "line": "", "importance": 5, "intensity": 3, "role": "setup"}}, …],
 "peak": 0.0, "story": "…", "accents": [0.0, …], "slowmo": {{"t": 0.0, "dur": 1.2}} или null,
 "hook": "…", "caption": "…", "subtitles": [{{"start": 0.0, "end": 0.0, "text": "…"}}], "dialogue_heavy": false,
 "mood": "…", "virality": 0, "virality_why": "…", "edit_ideas": ["…"]}}
Все времена — секунды от начала этого файла."""
    local = [str(path)] + [str(x) for x in sheets] + ([audio] if audio else [])
    data = parse_json(generate(prompt, local_paths=local, temperature=0.3, task_name="analyze", progress=progress,
                               chat=chat))
    if not isinstance(data, dict):
        raise RuntimeError("Gemini вернул неожиданный формат анализа")

    def _span(x: Any) -> Optional[Tuple[float, float]]:
        try:
            st, en = float(x["start"]), float(x["end"])
        except (KeyError, TypeError, ValueError):
            return None
        return (st, en) if en > st else None

    from .scenes import normalize_beats, plan_from_beats

    beats = [dict(b, start=sp[0], end=sp[1]) for b in data.get("beats") or [] if isinstance(b, dict)
             for sp in [_span(b)] if sp]
    dur = duration or max((b["end"] for b in beats), default=0.0)
    beats = normalize_beats(beats, dur)
    try:
        peak = float(data.get("peak")) if data.get("peak") is not None else None
    except (TypeError, ValueError):
        peak = None
    plan = plan_from_beats(beats, dur, peak, min_len=min_seconds, max_len=max_seconds) if beats else {"parts": []}
    parts = plan.get("parts") or []
    segs = []
    if parts:
        segs.append({"start": parts[0][0], "end": parts[-1][1], "score": 1.0, "source": "gemini",
                     "reason": data.get("story") or "Цельная сцена по смысловой карте Gemini"})
    data["beats"] = beats
    data["plan_removed"] = plan.get("removed", [])
    data["segments"] = segs
    data["parts"] = parts
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
    try:
        data["virality"] = max(0, min(100, int(float(data.get("virality")))))
    except (TypeError, ValueError):
        data["virality"] = None
    return data


def rate_moments(anime_name: str, genres: List[str], items: List[Dict[str, Any]], trend: Dict[str, Any],
                 progress=None) -> Dict[str, Dict[str, Any]]:
    """Шанс успеха шортса по каждому моменту: 0–100 и короткое объяснение."""
    rows = [{"id": m["id"], "title": m.get("title"), "episode": m.get("episode", ""), "mood": m.get("mood", ""),
             "why": m.get("why", ""), "views_source": m.get("views") or 0, "used_before": bool(m.get("used"))}
            for m in items]
    trend_txt = (f"Популярность на AniList: {trend.get('popularity', 'н/д')}, тренд: {trend.get('trending', 'н/д')}, "
                 f"рейтинг {trend.get('score', 'н/д')}, сейчас выходит: {'да' if trend.get('airing') else 'нет'}."
                 if trend else "Данных трендов нет — оцени по своей информации о популярности тайтла.")
    prompt = f"""Ты — аналитик вирусного аниме-контента в TikTok, Reels и YouTube Shorts (русскоязычная аудитория).
Тайтл: «{anime_name}» (жанры: {', '.join(genres or []) or 'н/д'}). {trend_txt}

Оцени для каждой сцены шанс, что вертикальный ролик 35–58 c с ней наберёт заметно больше просмотров, чем среднее по каналу.
Учитывай (по убыванию веса):
1. узнаваемость и культовость сцены (её ищут, пересматривают, цитируют; мемы);
2. эмоциональный пик и зрелищность, которые считываются в первые 2 секунды;
3. понятность без контекста (сцена самодостаточна за 40–60 c);
4. актуальность тайтла сейчас (новый сезон, тренд, годовщина);
5. насыщенность: сцену уже залили тысячи каналов — шанс ниже; редкая, но сильная — выше;
6. риск спойлера для текущей аудитории и потенциал комментариев/споров.
Будь строгим: 80+ — только культовые сцены на пике популярности; 50–70 — хорошая сцена; ниже 40 — слабая или заезженная.
Сцены: {json.dumps(rows, ensure_ascii=False)}
Верни JSON: {{"scores": [{{"id": "…", "score": 0, "why": "одна фраза: главный фактор"}}]}}"""
    data = parse_json(generate(prompt, temperature=0.3, task_name="rate", progress=progress))
    lst = data.get("scores", []) if isinstance(data, dict) else data
    return {str(x["id"]): x for x in lst or [] if isinstance(x, dict) and x.get("id") is not None}


def translate_subtitles(items: List[Dict[str, Any]], context: str, lang: str = "",
                        chat: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
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
    data = parse_json(generate(prompt, temperature=0.3, task_name="subtitles", chat=chat))
    lines = data.get("lines", []) if isinstance(data, dict) else data
    tr = {int(x["i"]): str(x.get("text", "")).strip() for x in lines or [] if isinstance(x, dict) and "i" in x}
    return [{**x, "text": tr[i]} for i, x in enumerate(items) if tr.get(i)]


def write_captions(clip: Dict[str, Any], settings: Dict[str, Any],
                   chat: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
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
    data = parse_json(generate(prompt, temperature=0.8, task_name="captions", chat=chat))
    if not isinstance(data, dict) or "youtube" not in data:
        raise RuntimeError("Gemini вернул неожиданный формат описаний")
    return data
