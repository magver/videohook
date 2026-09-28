"""Интеграция с Google Gemini (подписка Google AI Pro).

Два канала, выбираются автоматически:
  1. Gemini API (ключ из aistudio.google.com) — понимание видео через Files API, JSON-ответы.
  2. Gemini CLI (`gemini`, вход аккаунтом Google AI Pro — повышенные лимиты) — без ключа.
Если ни один канал не настроен, модуль сообщает об этом, а вызывающий код использует офлайн-эвристику.
"""

from __future__ import annotations

import json
import logging
import mimetypes
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from .core import SUBPROCESS_FLAGS, get_settings

log = logging.getLogger("videohook.gemini")
API = "https://generativelanguage.googleapis.com"


class GeminiUnavailable(RuntimeError):
    pass


def _key() -> str:
    return (get_settings().get("gemini_api_key") or "").strip()


def _cli() -> Optional[str]:
    s = get_settings()
    if not s.get("gemini_use_cli"):
        return None
    cmd = (s.get("gemini_cli_cmd") or "gemini").strip()
    return shutil.which(cmd) or (cmd if Path(cmd).exists() else None)


def available() -> bool:
    return bool(_key() or _cli())


def status() -> Dict[str, Any]:
    return {"api": bool(_key()), "cli": bool(_cli()), "model": get_settings().get("gemini_model")}


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


def generate(prompt: str, files: Optional[List[Dict[str, Any]]] = None, want_json: bool = True,
             temperature: float = 0.7, local_paths: Optional[List[str]] = None) -> str:
    """Текстовая генерация. files — объекты Files API (для API), local_paths — файлы для CLI."""
    key = _key()
    model = get_settings().get("gemini_model") or "gemini-flash-latest"
    if key:
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

    cli = _cli()
    if not cli:
        raise GeminiUnavailable("Gemini не настроен: добавьте API-ключ или установите Gemini CLI")
    cwd = None
    full_prompt = prompt
    if local_paths:
        cwd = str(Path(local_paths[0]).parent)
        refs = " ".join(f"@{Path(p).name}" for p in local_paths)
        full_prompt = f"{refs}\n\n{prompt}"
    if want_json:
        full_prompt += "\n\nОтветь ТОЛЬКО валидным JSON без пояснений и без markdown."
    res = subprocess.run([cli, "-p", full_prompt, "--output-format", "json"], capture_output=True, text=True,
                         encoding="utf-8", errors="replace", timeout=600, cwd=cwd, creationflags=SUBPROCESS_FLAGS)
    out = res.stdout.strip()
    if res.returncode != 0 and not out:
        # старые версии CLI без --output-format
        res = subprocess.run([cli, "-p", full_prompt], capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=600, cwd=cwd, creationflags=SUBPROCESS_FLAGS)
        out = res.stdout.strip()
        if res.returncode != 0:
            raise RuntimeError(f"Gemini CLI: {res.stderr.strip()[:400]}")
    try:
        wrapper = json.loads(out)
        if isinstance(wrapper, dict) and "response" in wrapper:
            return str(wrapper["response"])
    except ValueError:
        pass
    return out


def parse_json(text: str) -> Any:
    text = text.strip()
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


def suggest_moments(anime_name: str, known_titles: List[str], count: int = 5,
                    genres: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Новые вирусные моменты тайтла, которых ещё нет в базе."""
    known = "\n".join(f"- {t}" for t in known_titles[:60]) or "- (пока нет)"
    prompt = f"""Ты — редактор вирусного аниме-канала в TikTok/Reels/Shorts. Тайтл: «{anime_name}» (жанры: {', '.join(genres or []) or 'н/д'}).
Предложи {count} НОВЫХ самых узнаваемых и вирусных сцен этого тайтла, которых НЕТ в списке уже использованных:
{known}

Требования: реальные сцены (не выдумывай), разнообразие (бои, эмоции, юмор, повороты), каждая сцена 10–60 секунд.
Для каждой верни объект:
{{"title": "короткое название сцены на русском", "episode": "сезон/серия или арка", "query": "поисковый запрос для YouTube на русском",
"query_en": "search query in English", "hook": "цепляющий заголовок до 45 символов без эмодзи", "mood": "{MOODS}",
"why": "почему это залетит (1 фраза)"}}
Верни JSON-объект {{"moments": [...]}}."""
    data = parse_json(generate(prompt, temperature=0.9))
    items = data.get("moments", data) if isinstance(data, dict) else data
    return [m for m in items if isinstance(m, dict) and m.get("title")]


def analyze_video(path: str | Path, context: str, target_seconds: int = 35) -> Dict[str, Any]:
    """Смысловой разбор видео: лучшие отрезки, хук, реплики, идеи монтажа."""
    prompt = f"""Проанализируй видео — это фрагмент аниме ({context}).
Задача: выбрать отрезки для вирусного вертикального ролика длиной {max(10, target_seconds - 15)}–{target_seconds + 10} секунд.
Критерии: сильный визуальный хук в первые 2 секунды, кульминация, законченная мысль, возможность зациклить.
Верни JSON:
{{"segments": [{{"start": сек, "end": сек, "score": 0..1, "reason": "почему"}}] (до 4, по убыванию score),
 "hook": "заголовок-хук до 45 символов без эмодзи",
 "caption": "короткая подпись/цитата на русском для экрана, до 70 символов",
 "key_lines": [{{"t": сек, "text": "реплика на русском"}}] (до 8 ключевых реплик внутри лучшего отрезка),
 "edit_ideas": ["идеи монтажа: зум на секунде X, стоп-кадр, смена ритма…"],
 "mood": "{MOODS}"}}
Время — в секундах от начала этого файла."""
    files = None
    local = None
    if _key():
        files = [upload_file(path)]
    else:
        local = [str(path)]
    data = parse_json(generate(prompt, files=files, local_paths=local, temperature=0.4))
    segs = []
    for s in data.get("segments", []) if isinstance(data, dict) else []:
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
    """Описания под каждую площадку (с учётом их лимитов)."""
    prompt = f"""Сделай тексты для публикации вертикального аниме-ролика.
Аниме: {clip.get('anime_name') or clip.get('anime')}
Студия: {clip.get('studio') or 'н/д'}
Сцена: {clip.get('title')} ({clip.get('episode') or ''})
Хук на экране: {clip.get('hook')}
Настроение: {clip.get('mood')}
Канал автора: {settings.get('brand_handle')}; Telegram: {settings.get('telegram') or 'нет'}

Правила: язык — русский; первая строка — интрига/вопрос, вызывающий комментарии; обязательно указать название аниме
и кредит правообладателю (© студия) — ролик сделан как фан-обзор; без кликбейта про «полную серию».
Верни JSON:
{{"youtube": {{"title": "до 90 символов, в конце #shorts", "description": "до 600 символов", "tags": ["до 12 тегов без #"]}},
 "tiktok": {{"caption": "до 300 символов, 4–6 хэштегов в конце"}},
 "instagram": {{"caption": "до 400 символов, 8–12 хэштегов в конце"}},
 "pinned_comment": "вопрос зрителям для закрепа"}}"""
    data = parse_json(generate(prompt, temperature=0.8))
    if not isinstance(data, dict) or "youtube" not in data:
        raise RuntimeError("Gemini вернул неожиданный формат описаний")
    return data
