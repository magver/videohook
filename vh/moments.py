"""База моментов: сид + самопополняемая пользовательская база, учёт использования, тренды AniList.

Жизненный цикл момента:
  fresh → (создан клип) used → момент уходит из выдачи «свежих»;
  если у тайтла свежих < MIN_FRESH — фоновое пополнение (Gemini → YouTube-поиск → жанровые шаблоны).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from typing import Any, Dict, List, Optional

import requests

from .core import DATA_DIR, SEED_MOMENTS, JsonStore, tasks

log = logging.getLogger("videohook.moments")

MIN_FRESH = 3          # минимальный запас свежих моментов на тайтл
REFILL_BATCH = 5       # сколько новых моментов добавлять за раз
ANILIST_URL = "https://graphql.anilist.co"

_seed_cache: Optional[Dict[str, Any]] = None
_user = JsonStore(DATA_DIR / "moments_user.json", {"anime": {}, "usage": {}, "trends": {}})
_refilling: set = set()
_refill_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Загрузка и нормализация
# ---------------------------------------------------------------------------
def _seed() -> Dict[str, Any]:
    global _seed_cache
    if _seed_cache is None:
        try:
            _seed_cache = json.loads(SEED_MOMENTS.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log.warning("moments.json не загружен: %s", exc)
            _seed_cache = {"anime": [], "genre_templates": {}, "mood_music": {}}
    return _seed_cache


def norm(text: str) -> str:
    text = (text or "").lower().replace("ё", "е")
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def moment_id(anime_key: str, title: str) -> str:
    return "m_" + hashlib.sha1(f"{norm(anime_key)}|{norm(title)}".encode("utf-8")).hexdigest()[:12]


def mood_music(mood: str) -> str:
    return _seed().get("mood_music", {}).get(mood or "epic", "epic_orchestral")


def genre_templates() -> Dict[str, List[Dict[str, str]]]:
    return _seed().get("genre_templates", {})


def _anime_index() -> Dict[str, Dict[str, Any]]:
    """Все тайтлы (сид + пользовательские) с объединёнными моментами."""
    index: Dict[str, Dict[str, Any]] = {}
    for a in _seed().get("anime", []):
        entry = {k: v for k, v in a.items() if k != "moments"}
        entry["moments"] = [dict(m, source=m.get("source", "base")) for m in a.get("moments", [])]
        index[a["key"]] = entry
    with _user.lock:
        user_anime = _user.load().get("anime", {})
        for key, ua in user_anime.items():
            entry = index.setdefault(key, {"key": key, "ru": ua.get("ru", ""), "en": ua.get("en", key),
                                           "studio": ua.get("studio", ""), "aliases": [], "genres": ua.get("genres", []),
                                           "moments": []})
            for field in ("ru", "en", "studio"):
                if ua.get(field) and not entry.get(field):
                    entry[field] = ua[field]
            entry["genres"] = entry.get("genres") or ua.get("genres", [])
            existing = {norm(m["title"]) for m in entry["moments"]}
            for m in ua.get("moments", []):
                if norm(m["title"]) not in existing:
                    entry["moments"].append(m)
                    existing.add(norm(m["title"]))
    for key, entry in index.items():
        for m in entry["moments"]:
            m["id"] = moment_id(key, m["title"])
            m["anime_key"] = key
    return index


def find_anime(title: str) -> Optional[Dict[str, Any]]:
    """Сопоставление произвольного названия (RU/EN/алиас/имя персонажа) с тайтлом из базы."""
    q = norm(title)
    if not q:
        return None
    best, best_len = None, 0
    for key, a in _anime_index().items():
        names = [key, a.get("ru", ""), a.get("en", ""), *a.get("aliases", [])]
        for n in names:
            nn = norm(n)
            if not nn or len(nn) < 3:
                continue
            if nn == q or (len(nn) >= 4 and (f" {nn} " in f" {q} " or (len(q) >= 5 and q in nn))):
                if len(nn) > best_len:
                    best, best_len = a, len(nn)
    return best


def display_name(anime: Dict[str, Any]) -> str:
    ru, en = anime.get("ru") or "", anime.get("en") or ""
    if ru and en and norm(ru) != norm(en):
        return f"{ru} ({en})"
    return ru or en or anime.get("key", "")


# ---------------------------------------------------------------------------
# Использование
# ---------------------------------------------------------------------------
def _usage() -> Dict[str, Any]:
    return _user.load().setdefault("usage", {})


def usage_of(mid: str) -> Dict[str, Any]:
    with _user.lock:
        return dict(_usage().get(mid, {}))


def _decorate(m: Dict[str, Any]) -> Dict[str, Any]:
    u = usage_of(m["id"])
    out = dict(m)
    out["used_count"] = u.get("count", 0)
    out["last_used"] = u.get("last", 0)
    out["used"] = out["used_count"] > 0
    out.setdefault("music", mood_music(m.get("mood", "epic")))
    return out


def mark_used(mid: str, anime_key: str, clip_id: str = "", auto_refill: bool = True) -> Dict[str, Any]:
    with _user.lock:
        rec = _usage().setdefault(mid, {"count": 0, "clips": []})
        rec["count"] = rec.get("count", 0) + 1
        rec["last"] = time.time()
        if clip_id:
            rec["clips"] = (rec.get("clips", []) + [clip_id])[-10:]
        _user.save()
    if auto_refill and anime_key:
        ensure_fresh(anime_key)
    return rec


def reset_usage(mid: str) -> None:
    with _user.lock:
        _usage().pop(mid, None)
        _user.save()


def list_moments(anime_key: str, include_used: bool = True) -> List[Dict[str, Any]]:
    a = _anime_index().get(anime_key)
    if not a:
        return []
    items = [_decorate(m) for m in a["moments"]]
    if not include_used:
        items = [m for m in items if not m["used"]]
    # свежие сверху, ИИ/найденные после базовых, использованные — вниз
    items.sort(key=lambda m: (m["used"], m.get("added", 0) * -1 if m.get("source") != "base" else 0))
    return items


def fresh_count(anime_key: str) -> int:
    return len(list_moments(anime_key, include_used=False))


def catalog(query: str = "", include_used: bool = True) -> List[Dict[str, Any]]:
    """Каталог тайтлов с моментами для UI."""
    q = norm(query)
    out = []
    for key, a in _anime_index().items():
        hay = norm(" ".join([key, a.get("ru", ""), a.get("en", ""), *a.get("aliases", [])]))
        moments = list_moments(key, include_used)
        if q and q not in hay and not any(q in norm(m["title"]) for m in moments):
            continue
        out.append({
            "key": key, "name": display_name(a), "ru": a.get("ru", ""), "en": a.get("en", ""),
            "studio": a.get("studio", ""), "genres": a.get("genres", []),
            "fresh": sum(1 for m in moments if not m["used"]), "total": len(moments),
            "moments": moments, "refilling": key in _refilling,
        })
    out.sort(key=lambda a: (-a["fresh"], a["name"]))
    return out


# ---------------------------------------------------------------------------
# Пополнение
# ---------------------------------------------------------------------------
def add_moments(anime_key: str, moments: List[Dict[str, Any]], meta: Optional[Dict[str, Any]] = None,
                source: str = "ai") -> int:
    """Добавляет новые моменты в пользовательскую базу, отбрасывая дубли. Возвращает число добавленных."""
    index = _anime_index()
    existing = {norm(m["title"]) for m in index.get(anime_key, {}).get("moments", [])}
    existing_q = {norm(m.get("query", "")) for m in index.get(anime_key, {}).get("moments", [])}
    added = 0
    with _user.lock:
        ua = _user.load().setdefault("anime", {}).setdefault(anime_key, {"moments": []})
        for field, val in (meta or {}).items():
            if val and not ua.get(field):
                ua[field] = val
        for m in moments:
            title = (m.get("title") or "").strip()
            if not title or norm(title) in existing or (m.get("query") and norm(m["query"]) in existing_q):
                continue
            clean = {
                "title": title[:120],
                "episode": str(m.get("episode") or "")[:60],
                "query": (m.get("query") or f"{meta.get('ru') if meta else anime_key} {title}")[:200],
                "query_en": (m.get("query_en") or "")[:200],
                "hook": (m.get("hook") or title)[:90],
                "mood": m.get("mood") if m.get("mood") in ("epic", "dark", "twist", "emotional", "romantic", "funny") else "epic",
                "why": (m.get("why") or "")[:240],
                "url": m.get("url") or "",
                "views": m.get("views") or 0,
                "source": source,
                "added": time.time(),
            }
            ua.setdefault("moments", []).append(clean)
            existing.add(norm(title))
            added += 1
        _user.save()
    return added


def ensure_fresh(anime_key: str, force: bool = False) -> Optional[str]:
    """Запускает фоновое пополнение, если свежих моментов меньше порога. Возвращает id задачи."""
    if not force and fresh_count(anime_key) >= MIN_FRESH:
        return None
    with _refill_lock:
        if anime_key in _refilling:
            return None
        _refilling.add(anime_key)

    anime = _anime_index().get(anime_key, {"key": anime_key, "en": anime_key, "ru": "", "genres": []})

    def job(task):
        try:
            return refill(anime, task)
        finally:
            _refilling.discard(anime_key)

    t = tasks.submit("refill", f"Пополнение моментов: {display_name(anime)}", job)
    return t.id


def refill(anime: Dict[str, Any], task=None) -> Dict[str, Any]:
    """Пополнение базы: Gemini → YouTube (самое просматриваемое) → жанровые шаблоны."""
    from . import gemini, discovery  # локальный импорт: избегаем циклов

    key = anime["key"]
    name = display_name(anime)
    known = [m["title"] for m in anime.get("moments", [])]
    added, sources = 0, []

    if task:
        task.update(0.1, "Gemini подбирает новые моменты…")
    if gemini.available():
        try:
            fresh = gemini.suggest_moments(name, known, count=REFILL_BATCH, genres=anime.get("genres", []))
            n = add_moments(key, fresh, {"ru": anime.get("ru"), "en": anime.get("en"),
                                         "studio": anime.get("studio"), "genres": anime.get("genres")}, source="ai")
            added += n
            sources.append(f"Gemini: +{n}")
        except Exception as exc:  # noqa: BLE001
            sources.append(f"Gemini: ошибка ({str(exc)[:80]})")

    if added < REFILL_BATCH:
        if task:
            task.update(0.5, "Ищу самые просматриваемые ролики на YouTube…")
        try:
            found = discovery.popular_moments_for(anime, limit=REFILL_BATCH * 2, exclude_titles=known)
            n = add_moments(key, found[: REFILL_BATCH - added], {"ru": anime.get("ru"), "en": anime.get("en")},
                            source="youtube")
            added += n
            sources.append(f"YouTube: +{n}")
        except Exception as exc:  # noqa: BLE001
            sources.append(f"YouTube: ошибка ({str(exc)[:80]})")

    if added == 0 and fresh_count(key) == 0:
        base = anime.get("ru") or anime.get("en") or key
        gen = []
        for g in anime.get("genres") or ["Action"]:
            for tpl in genre_templates().get(g, []):
                gen.append({"title": f"{tpl['q'].capitalize()} — {base}", "query": f"{base} {tpl['q']}",
                            "query_en": f"{anime.get('en') or base} {tpl['q_en']}",
                            "hook": tpl["hook"].replace("{anime}", base), "mood": tpl["mood"]})
        n = add_moments(key, gen, {"ru": anime.get("ru"), "en": anime.get("en"), "genres": anime.get("genres")},
                        source="template")
        added += n
        sources.append(f"Шаблоны: +{n}")

    msg = f"{name}: добавлено {added} ({', '.join(sources)})"
    if task:
        task.update(1.0, msg)
    return {"anime": key, "added": added, "sources": sources, "message": msg}


def maintain_all(limit: int = 12) -> List[str]:
    """Проверка запаса по всем тайтлам (используется автопилотом и при старте)."""
    started = []
    for key in list(_anime_index().keys())[:200]:
        if len(started) >= limit:
            break
        tid = ensure_fresh(key)
        if tid:
            started.append(tid)
    return started


# ---------------------------------------------------------------------------
# AniList: тренды и поиск
# ---------------------------------------------------------------------------
_ANILIST_FIELDS = """
  id title { romaji english native } averageScore popularity trending episodes genres
  seasonYear coverImage { large } studios(isMain: true) { nodes { name } } siteUrl
  nextAiringEpisode { episode airingAt }
"""


def _anilist(query: str, variables: Dict[str, Any]) -> List[Dict[str, Any]]:
    resp = requests.post(ANILIST_URL, json={"query": query, "variables": variables}, timeout=12)
    resp.raise_for_status()
    return resp.json().get("data", {}).get("Page", {}).get("media", []) or []


def _to_anime(item: Dict[str, Any]) -> Dict[str, Any]:
    t = item.get("title") or {}
    en = t.get("english") or t.get("romaji") or "Unknown"
    match = find_anime(en) or find_anime(t.get("romaji") or "")
    key = match["key"] if match else norm(en)
    studios = [n.get("name") for n in (item.get("studios") or {}).get("nodes", []) if n.get("name")]
    return {
        "key": key,
        "en": en,
        "ru": match.get("ru", "") if match else "",
        "romaji": t.get("romaji") or "",
        "score": item.get("averageScore") or 0,
        "popularity": item.get("popularity") or 0,
        "trending": item.get("trending") or 0,
        "episodes": item.get("episodes"),
        "genres": item.get("genres") or [],
        "year": item.get("seasonYear"),
        "cover": (item.get("coverImage") or {}).get("large", ""),
        "studio": ", ".join(studios[:2]),
        "airing": bool(item.get("nextAiringEpisode")),
        "url": item.get("siteUrl", ""),
        "in_base": bool(match),
    }


def trending(sort: str = "TRENDING_DESC", limit: int = 30, use_cache: bool = True) -> List[Dict[str, Any]]:
    cache_key = f"{sort}:{limit}"
    with _user.lock:
        cached = _user.load().setdefault("trends", {}).get(cache_key)
    if use_cache and cached and time.time() - cached.get("ts", 0) < 6 * 3600:
        items = cached["items"]
    else:
        q = f"query ($sort: [MediaSort], $n: Int) {{ Page(page: 1, perPage: $n) {{ media(sort: $sort, type: ANIME, isAdult: false) {{ {_ANILIST_FIELDS} }} }} }}"
        try:
            items = [_to_anime(i) for i in _anilist(q, {"sort": [sort], "n": min(limit, 50)})]
            with _user.lock:
                _user.load()["trends"][cache_key] = {"ts": time.time(), "items": items}
                _user.save()
        except Exception as exc:  # noqa: BLE001
            log.warning("AniList недоступен: %s", exc)
            items = cached["items"] if cached else _offline_trending()
    for it in items:
        it["fresh"] = fresh_count(it["key"])
    return items


def search_anime(text: str, limit: int = 12) -> List[Dict[str, Any]]:
    q = f"query ($s: String, $n: Int) {{ Page(page: 1, perPage: $n) {{ media(search: $s, type: ANIME, isAdult: false) {{ {_ANILIST_FIELDS} }} }} }}"
    try:
        items = [_to_anime(i) for i in _anilist(q, {"s": text, "n": limit})]
    except Exception as exc:  # noqa: BLE001
        log.warning("AniList search: %s", exc)
        items = []
    local = find_anime(text)
    if local and not any(i["key"] == local["key"] for i in items):
        items.insert(0, {"key": local["key"], "en": local.get("en", ""), "ru": local.get("ru", ""),
                         "genres": local.get("genres", []), "studio": local.get("studio", ""), "cover": "",
                         "score": 0, "in_base": True})
    for it in items:
        it["fresh"] = fresh_count(it["key"])
    return items


def register_anime(info: Dict[str, Any]) -> str:
    """Регистрирует тайтл (например, из трендов AniList) в пользовательской базе и запускает пополнение."""
    key = info.get("key") or norm(info.get("en") or info.get("ru") or "")
    if not key:
        raise ValueError("Не указано название")
    if key not in _anime_index():
        with _user.lock:
            _user.load().setdefault("anime", {})[key] = {
                "ru": info.get("ru", ""), "en": info.get("en", ""), "studio": info.get("studio", ""),
                "genres": info.get("genres", []), "moments": [],
            }
            _user.save()
    ensure_fresh(key)
    return key


def _offline_trending() -> List[Dict[str, Any]]:
    out = []
    for a in _seed().get("anime", [])[:20]:
        out.append({"key": a["key"], "en": a["en"], "ru": a["ru"], "genres": a.get("genres", []),
                    "studio": a.get("studio", ""), "cover": "", "score": 0, "popularity": 0, "trending": 0,
                    "in_base": True, "offline": True})
    return out


def stats() -> Dict[str, Any]:
    idx = _anime_index()
    total = sum(len(a["moments"]) for a in idx.values())
    with _user.lock:
        used = sum(1 for u in _usage().values() if u.get("count"))
    return {"anime": len(idx), "moments": total, "used": used, "fresh": total - used,
            "refilling": sorted(_refilling)}
