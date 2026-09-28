import json
import logging
import os
import subprocess
import time
from typing import Any, Dict, List, Optional
import requests

logger = logging.getLogger(__name__)

ANILIST_API_URL = "https://graphql.anilist.co"

# Curated database of iconic viral moments, episodes, Russian audio queries, and hooks
ICONIC_MOMENTS_DB: Dict[str, List[Dict[str, Any]]] = {
    "demon slayer": [
        {
            "title": "Серия 19: Танец Бога Огня против Руи",
            "query": "Клинок рассекающий демонов 19 серия танец бога огня русская озвучка русские субтитры 1080p",
            "hook": "САМЫЙ КРАСИВЫЙ БОЙ В АНИМЕ 🔥",
            "subs": "Танец Бога Огня! Я защищу её любой ценой!",
            "default_start": 20.0,
            "default_duration": 20.0,
        },
        {
            "title": "2 сезон Серия 10: Удзуй и Тэндзиро против Гютаро",
            "query": "Клинок 2 сезон 10 серия бой удзуй против гютаро русская озвучка 1080p",
            "hook": "БЮДЖЕТ ЭТОГО БОЯ СЛОМАЛ ИНТЕРНЕТ 😱",
            "subs": "Мы победим! Мы не сдадимся!",
            "default_start": 30.0,
            "default_duration": 22.0,
        },
    ],
    "attack on titan": [
        {
            "title": "3 сезон Серия 17: Леви против Звероподобного",
            "query": "Атака титанов Леви против Звероподобного бой русская озвучка 1080p",
            "hook": "ЛЕВИ В ЯРОСТИ НЕ ОСТАНОВИТЬ ⚡",
            "subs": "Ты думал, что сможешь сбежать от меня?!",
            "default_start": 15.0,
            "default_duration": 25.0,
        },
        {
            "title": "2 сезон Серия 6: Признание Райнера и Бертольда",
            "query": "Атака титанов Райнер и Бертольд признание русская озвучка 1080p",
            "hook": "ГЛАВНЫЙ СЮЖЕТНЫЙ ТВИТ В ИСТОРИИ 🤯",
            "subs": "Я — Бронированный, а он — Колоссальный титан.",
            "default_start": 10.0,
            "default_duration": 20.0,
        },
    ],
    "jujutsu kaisen": [
        {
            "title": "2 сезон Серия 16: Сукуна против Магораги",
            "query": "Магическая битва Сукуна против Магораги русская озвучка 1080p",
            "hook": "СУКУНА ПОКАЗАЛ ИСТИННОЕ БЕЗУМИЕ 😈",
            "subs": "Расширение территории: Гробница Зла!",
            "default_start": 25.0,
            "default_duration": 22.0,
        },
        {
            "title": "1 сезон Серия 20: Hollow Purple Годзё Сатору",
            "query": "Магическая битва Годзё Фиолетовый hollow purple русская озвучка 1080p",
            "hook": "МОМЕНТ КОГДА ОН СНЯЛ ПОВЯЗКУ 💥",
            "subs": "Тайная техника: Фиолетовый!",
            "default_start": 12.0,
            "default_duration": 18.0,
        },
    ],
    "solo leveling": [
        {
            "title": "1 сезон Серия 12: Джин-Ву произносит 'Восстань' (Arise)",
            "query": "Поднятие уровня в одиночку Джин Ву Восстань русская озвучка 1080p",
            "hook": "СЛОВО, ОТ КОТОРОГО ДРОЖИТ ВЕСЬ МИР: 'ВОССТАНЬ' 👑",
            "subs": "Восстань! Теперь ты служишь мне.",
            "default_start": 10.0,
            "default_duration": 20.0,
        },
        {
            "title": "1 сезон Серия 6: Джин-Ву уничтожает предателей",
            "query": "Поднятие уровня в одиночку 6 серия бой русская озвучка 1080p",
            "hook": "ОН БОЛЬШЕ НЕ СЛАБЫЙ ОХОТНИК E-РАНГА 💀",
            "subs": "Вы сами выбрали свою судьбу.",
            "default_start": 15.0,
            "default_duration": 22.0,
        }
    ],
    "death note": [
        {
            "title": "Серия 24: Возвращение памяти Лайта ('Я победил!')",
            "query": "Тетрадь смерти Лайт вернул память русская озвучка 1080p",
            "hook": "ГЕНИАЛЬНЫЙ ПЛАН ЛАЙТА СРАБОТАЛ 🧠",
            "subs": "Всё идет точно по моему плану! Я победил!",
            "default_start": 10.0,
            "default_duration": 20.0,
        },
    ],
    "one piece": [
        {
            "title": "Серия 1071: Пробуждение Gear 5 Луффи",
            "query": "Ван Пис Луффи 5 гир gear 5 русская озвучка 1080p",
            "hook": "ВЕСЬ МИР ЖДАЛ ЭТОТ МОМЕНТ 10 ЛЕТ ✨",
            "subs": "Это пик моих возможностей! 5-й Гир!",
            "default_start": 20.0,
            "default_duration": 25.0,
        },
        {
            "title": "Серия 1015: Удар Red Rock по Кайдо",
            "query": "Ван Пис 1015 серия Луффи ред рок русская озвучка 1080p",
            "hook": "ЛУЧШАЯ АНИМАЦИЯ В ONE PIECE 💥",
            "subs": "Я стану королём пиратов!",
            "default_start": 15.0,
            "default_duration": 22.0,
        },
    ],
    "chainsaw man": [
        {
            "title": "Серия 8: Макима и храм возмездия",
            "query": "Человек бензопила Макима в храме русская озвучка 1080p",
            "hook": "САМАЯ ЖУТКАЯ СИЛА В АНИМЕ 🩸",
            "subs": "Произнеси его имя...",
            "default_start": 10.0,
            "default_duration": 22.0,
        },
    ],
    "naruto": [
        {
            "title": "Шиппуден Серия 375: Какаши против Обито",
            "query": "Наруто Какаши против Обито бой русская озвучка 1080p",
            "hook": "ЛУЧШИЙ РУКОПАШНЫЙ БОЙ ВСЕХ ВРЕМЕН 🥋",
            "subs": "Мы были друзьями, Обито...",
            "default_start": 20.0,
            "default_duration": 25.0,
        },
        {
            "title": "Серия 133: Наруто против Саске в Долине Завершения",
            "query": "Наруто против Саске долина завершения русская озвучка 1080p",
            "hook": "НОСТАЛЬГИЯ ДО МУРАШЕК ⚡",
            "subs": "Я верну тебя в Коноху, чего бы это ни стоило!",
            "default_start": 15.0,
            "default_duration": 20.0,
        },
    ],
    "bleach": [
        {
            "title": "ТКВ: Банкай Ямамото (Zanka no Tachi)",
            "query": "Блич банкай Ямамото русская озвучка 1080p",
            "hook": "ПЛАМЯ ТЕМПЕРАТУРОЙ 15 МИЛЛИОНОВ ГРАДУСОВ 🗡️",
            "subs": "Банкай: Дзанко но Тати!",
            "default_start": 15.0,
            "default_duration": 25.0,
        },
    ],
}


# 5C. Автогенерация моментов по жанрам
GENRE_MOMENTS: Dict[str, List[Dict[str, str]]] = {
    "Action": [
        {"query_add": "epic fight scene", "hook": "Эпичный бой!"},
        {"query_add": "power up transformation", "hook": "Трансформация!"},
        {"query_add": "final battle", "hook": "Финальная битва!"},
        {"query_add": "sword fight compilation", "hook": "Лучшие бои на мечах!"},
    ],
    "Romance": [
        {"query_add": "confession scene", "hook": "Признание!"},
        {"query_add": "cute moment", "hook": "Милое событие!"},
        {"query_add": "heartbreak scene", "hook": "Душераздирающая сцена!"},
        {"query_add": "first kiss anime", "hook": "Первый поцелуй!"},
    ],
    "Horror": [
        {"query_add": "scariest moment", "hook": "Самый жуткий момент!"},
        {"query_add": "jump scare anime", "hook": "Жуткая сцена!"},
        {"query_add": "plot twist reveal", "hook": "Шокирующий поворот!"},
    ],
    "Comedy": [
        {"query_add": "funniest scene", "hook": "Смешнейшая сцена!"},
        {"query_add": "funny reaction compilation", "hook": "Эпичные реакции!"},
        {"query_add": "meme moment", "hook": "Стал мемом!"},
    ],
    "Drama": [
        {"query_add": "betrayal scene", "hook": "Предательство!"},
        {"query_add": "emotional sacrifice", "hook": "Жертва ради других!"},
        {"query_add": "saddest scene", "hook": "Самая грустная сцена!"},
        {"query_add": "reunion scene", "hook": "Воссоединение!"},
    ],
    "Fantasy": [
        {"query_add": "magic reveal scene", "hook": "Проявление магии!"},
        {"query_add": "world building moment", "hook": "Открытие мира!"},
        {"query_add": "summoning scene", "hook": "Призыв!"},
    ],
    "Sci-Fi": [
        {"query_add": "space battle scene", "hook": "Космическая битва!"},
        {"query_add": "mecha fight scene", "hook": "Бой мехов!"},
        {"query_add": "time travel moment", "hook": "Путешествие во времени!"},
    ],
}


# =============================================================================
# 5A. MyAnimeList API (Jikan v4)
# =============================================================================
class MALIntegration:
    BASE_URL = "https://api.jikan.moe/v4"

    def get_top_anime(self, limit: int = 50, filter_type: str = "bypopularity") -> List[Dict[str, Any]]:
        """Топ аниме по популярности через Jikan API"""
        url = f"{self.BASE_URL}/top/anime"
        params = {"limit": min(limit, 25), "filter": filter_type}
        try:
            resp = requests.get(url, params=params, timeout=10)
            time.sleep(1)  # Rate limit Jikan (3 req/sec)
            if resp.status_code == 200:
                data = resp.json()
                return data.get("data", [])
        except Exception as e:
            logger.warning(f"Jikan MAL get_top_anime warning: {e}")
        return []

    def search_anime(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Поиск аниме через Jikan API"""
        url = f"{self.BASE_URL}/anime"
        params = {"q": query, "limit": limit, "order_by": "score", "sort": "desc"}
        try:
            resp = requests.get(url, params=params, timeout=10)
            time.sleep(1)
            if resp.status_code == 200:
                data = resp.json()
                return data.get("data", [])
        except Exception as e:
            logger.warning(f"Jikan MAL search_anime warning: {e}")
        return []

    def get_anime_episodes(self, mal_id: int) -> List[Dict[str, Any]]:
        """Эпизоды аниме через Jikan API"""
        url = f"{self.BASE_URL}/anime/{mal_id}/episodes"
        try:
            resp = requests.get(url, timeout=10)
            time.sleep(1)
            if resp.status_code == 200:
                data = resp.json()
                return data.get("data", [])
        except Exception as e:
            logger.warning(f"Jikan MAL get_anime_episodes warning: {e}")
        return []


# =============================================================================
# 5B. YouTube Search через yt-dlp
# =============================================================================
def search_youtube_moments(query: str, max_results: int = 10) -> List[Dict[str, Any]]:
    """Поиск аниме-моментов на YouTube через yt-dlp"""
    cmd = [
        "yt-dlp", "--flat-playlist", "-J",
        f"ytsearch{max_results}:{query}"
    ]
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=creationflags,
            timeout=20,
        )
        if result.returncode != 0 or not result.stdout:
            return []
        data = json.loads(result.stdout)
        results = []
        for entry in data.get("entries", []):
            dur = entry.get("duration")
            if dur and 30 < dur < 600:
                results.append({
                    "title": entry.get("title", ""),
                    "url": entry.get("url") or f"https://www.youtube.com/watch?v={entry.get('id', '')}",
                    "duration": dur,
                    "views": entry.get("view_count", 0),
                })
        return results
    except Exception as e:
        logger.warning(f"search_youtube_moments warning: {e}")
        return []


# =============================================================================
# 5E. Кэширование данных трендов (24 часа)
# =============================================================================
CACHE_FILE = "trends_cache.json"
CACHE_TTL = 86400  # 24 часа


class TrendsCache:
    def __init__(self, cache_file: str = CACHE_FILE, ttl: int = CACHE_TTL):
        self.cache_file = cache_file
        self.ttl = ttl

    def load(self, key: Optional[str] = None) -> List[Dict[str, Any]]:
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, encoding="utf-8") as f:
                    data = json.load(f)
                ts = data.get("ts", 0)
                if time.time() - ts < self.ttl:
                    if key:
                        buckets = data.get("buckets", {})
                        if key in buckets:
                            return buckets[key]
                    return data.get("items", [])
            except Exception as e:
                logger.warning(f"TrendsCache load error: {e}")
        return []

    def save(self, items: List[Dict[str, Any]], key: Optional[str] = None):
        try:
            data = {"ts": time.time(), "items": items, "buckets": {}}
            if os.path.exists(self.cache_file):
                try:
                    with open(self.cache_file, encoding="utf-8") as f:
                        old = json.load(f)
                        if isinstance(old, dict):
                            data["buckets"] = old.get("buckets", {})
                except Exception:
                    pass
            if key:
                data["buckets"][key] = items
            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"TrendsCache save error: {e}")

    def clear(self):
        try:
            if os.path.exists(self.cache_file):
                os.remove(self.cache_file)
        except Exception:
            pass


# Singleton cache instance
trends_cache = TrendsCache()


# =============================================================================
# 5C & 5D. Расширенная генерация моментов и AniList-запрос
# =============================================================================
def generate_moments_for_anime(
    anime_title: str, genres: Optional[List[str]] = None, score: float = 0.0
) -> List[Dict[str, Any]]:
    """
    Returns known curated moments from ICONIC_MOMENTS_DB or auto-generates
    viral hooks based on genre mapping for any anime.
    """
    clean_title = anime_title.lower().strip()

    # 1. Match in curated database
    for key, moments in ICONIC_MOMENTS_DB.items():
        if key in clean_title or clean_title in key:
            for m in moments:
                m["is_iconic"] = True
            return moments

    # 2. Match based on genre templates (Stage 5C)
    generated: List[Dict[str, Any]] = []
    if genres:
        for g in genres:
            templates = GENRE_MOMENTS.get(g)
            if templates:
                for tmpl in templates:
                    q_add = tmpl["query_add"]
                    hook = tmpl["hook"]
                    generated.append({
                        "title": f"{hook} ({g})",
                        "query": f"{anime_title} {q_add} русская озвучка 1080p",
                        "hook": f"{hook} — {anime_title.upper()[:22]} 🔥",
                        "subs": f"Невероятный момент из {anime_title}!",
                        "default_start": 15.0,
                        "default_duration": 22.0,
                        "is_iconic": False,
                    })
                    if len(generated) >= 4:
                        break
            if len(generated) >= 4:
                break

    if generated:
        return generated

    # 3. Dynamic general fallback
    return [
        {
            "title": "Кульминационный бой (Русская озвучка)",
            "query": f"{anime_title} лучший бой русская озвучка русские субтитры 1080p",
            "hook": f"САМЫЙ ЭПИЧНЫЙ БОЙ В {anime_title.upper()[:24]} 🔥",
            "subs": "Я превзойду все свои пределы!",
            "default_start": 15.0,
            "default_duration": 25.0,
            "is_iconic": False,
        },
        {
            "title": "Эмоциональный пик (Русские субтитры)",
            "query": f"{anime_title} эмоциональный момент русские субтитры 1080p",
            "hook": "ЭТОТ МОМЕНТ ВОШЕЛ В ИСТОРИЮ 😱",
            "subs": "Я никогда не отступлю от своего слова!",
            "default_start": 10.0,
            "default_duration": 20.0,
            "is_iconic": False,
        },
        {
            "title": "Культовая сцена (Русский дубляж)",
            "query": f"{anime_title} культовая сцена на русском дубляж 1080p",
            "hook": "МОМЕНТ, РАЗБИВШИЙ ИНТЕРНЕТ ⚡",
            "subs": "Ты даже не представляешь, с кем связался!",
            "default_start": 12.0,
            "default_duration": 22.0,
            "is_iconic": False,
        },
    ]


def get_iconic_moments_for_anime(
    anime_title: str, genres: Optional[List[str]] = None, score: float = 0.0
) -> List[Dict[str, Any]]:
    """Backwards-compatible wrapper."""
    return generate_moments_for_anime(anime_title, genres=genres, score=score)


def fetch_trending_with_episodes(
    sort_type: str = "TRENDING_DESC", limit: int = 50, use_cache: bool = True
) -> List[Dict[str, Any]]:
    """
    5D: Fetches trending anime with episode info, genres, tags, score, and generated moments.
    Cached locally for 24 hours.
    """
    cache_key = f"{sort_type}_{limit}"
    if use_cache:
        cached = trends_cache.load(key=cache_key)
        if cached:
            return cached

    query = """
    query ($page: Int, $sort: [MediaSort], $perPage: Int) {
        Page(page: $page, perPage: $perPage) {
            media(sort: $sort, type: ANIME) {
                id
                title { romaji english native }
                averageScore
                trending
                episodes
                genres
                tags { name }
                seasonYear
                description(asHtml: false)
                coverImage { large medium }
                nextAiringEpisode { episode }
            }
        }
    }
    """

    variables = {
        "page": 1,
        "sort": [sort_type],
        "perPage": min(limit, 50),
    }

    results: List[Dict[str, Any]] = []

    try:
        response = requests.post(
            ANILIST_API_URL,
            json={"query": query, "variables": variables},
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()
        raw_list = data.get("data", {}).get("Page", {}).get("media", [])

        for item in raw_list:
            title_dict = item.get("title", {})
            main_title = title_dict.get("english") or title_dict.get("romaji") or "Без названия"
            alt_title = title_dict.get("romaji") if title_dict.get("english") else ""
            score = float(item.get("averageScore") or 0)
            genres = item.get("genres") or []
            episodes = item.get("episodes") or "?"
            clean_title = main_title.lower()

            # Check if anime has curated iconic moments
            is_iconic = any(k in clean_title or clean_title in k for k in ICONIC_MOMENTS_DB.keys())
            moments = generate_moments_for_anime(main_title, genres=genres, score=score)

            results.append({
                "id": item.get("id"),
                "title": main_title,
                "alt_title": alt_title,
                "score": score,
                "trending": item.get("trending") or 0,
                "episodes": episodes,
                "genres": genres,
                "tags": [t.get("name") for t in (item.get("tags") or []) if t.get("name")],
                "year": item.get("seasonYear") or "",
                "cover": item.get("coverImage", {}).get("large") or "",
                "description": (item.get("description") or "").replace("\n", " ")[:180] + "...",
                "has_iconic": is_iconic,
                "has_youtube": True,
                "moments": moments,
            })

        if results:
            trends_cache.save(results, key=cache_key)
            return results

    except Exception as e:
        logger.error(f"Error fetching trending with episodes from AniList: {e}")

    # Fallback if network or API error
    fallback_items = [
        {
            "id": 1, "title": "Demon Slayer: Kimetsu no Yaiba", "score": 85, "episodes": 26,
            "genres": ["Action", "Fantasy"], "year": 2019, "cover": "", "has_iconic": True,
            "has_youtube": True, "moments": ICONIC_MOMENTS_DB["demon slayer"],
            "description": "Тандзиро Камадо отправляется в путь, чтобы отомстить демонам и спасти сестру."
        },
        {
            "id": 2, "title": "Attack on Titan", "score": 90, "episodes": 25,
            "genres": ["Action", "Drama"], "year": 2013, "cover": "", "has_iconic": True,
            "has_youtube": True, "moments": ICONIC_MOMENTS_DB["attack on titan"],
            "description": "Человечество ведёт войну за выживание против гигантских титанов."
        },
        {
            "id": 3, "title": "Jujutsu Kaisen", "score": 87, "episodes": 24,
            "genres": ["Action", "Supernatural", "Fantasy"], "year": 2020, "cover": "", "has_iconic": True,
            "has_youtube": True, "moments": ICONIC_MOMENTS_DB["jujutsu kaisen"],
            "description": "Юдзи Итадори вступает в мир магических проклятий."
        },
        {
            "id": 4, "title": "Solo Leveling", "score": 83, "episodes": 12,
            "genres": ["Action", "Fantasy"], "year": 2024, "cover": "", "has_iconic": True,
            "has_youtube": True, "moments": ICONIC_MOMENTS_DB["solo leveling"],
            "description": "Слабейший охотник Сон Джин-Ву получает таинственную способность прокачки."
        },
        {
            "id": 5, "title": "One Piece", "score": 89, "episodes": 1100,
            "genres": ["Action", "Adventure", "Comedy"], "year": 1999, "cover": "", "has_iconic": True,
            "has_youtube": True, "moments": ICONIC_MOMENTS_DB["one piece"],
            "description": "Монки Д. Луффи собирает команду в поисках сокровища Ван Пис."
        },
        {
            "id": 6, "title": "Death Note", "score": 88, "episodes": 37,
            "genres": ["Drama", "Horror", "Mystery"], "year": 2006, "cover": "", "has_iconic": True,
            "has_youtube": True, "moments": ICONIC_MOMENTS_DB["death note"],
            "description": "Лайт Ягами находит тетрадь, способную убивать любого человека."
        },
        {
            "id": 7, "title": "Chainsaw Man", "score": 84, "episodes": 12,
            "genres": ["Action", "Horror", "Fantasy"], "year": 2022, "cover": "", "has_iconic": True,
            "has_youtube": True, "moments": ICONIC_MOMENTS_DB["chainsaw man"],
            "description": "Дэндзи заключает контракт с демоном-бензопилой Почитой."
        },
        {
            "id": 8, "title": "Naruto", "score": 82, "episodes": 720,
            "genres": ["Action", "Adventure", "Comedy"], "year": 2002, "cover": "", "has_iconic": True,
            "has_youtube": True, "moments": ICONIC_MOMENTS_DB["naruto"],
            "description": "История Наруто Узумаки на пути к признанию и титулу Хокаге."
        },
        {
            "id": 9, "title": "Bleach: Thousand-Year Blood War", "score": 89, "episodes": 26,
            "genres": ["Action", "Fantasy", "Supernatural"], "year": 2022, "cover": "", "has_iconic": True,
            "has_youtube": True, "moments": ICONIC_MOMENTS_DB["bleach"],
            "description": "Финальная битва синигами Общества Душ против Квинси."
        },
    ]
    return fallback_items


def fetch_top_anime(sort_type: str = "TRENDING_DESC", limit: int = 25) -> List[Dict[str, Any]]:
    """Standard entry point: delegates to fetch_trending_with_episodes with caching."""
    return fetch_trending_with_episodes(sort_type=sort_type, limit=limit, use_cache=True)


def search_anime_by_title(search_text: str, limit: int = 15) -> List[Dict[str, Any]]:
    """Searches anime on AniList by custom title / keyword."""
    if not search_text.strip():
        return fetch_top_anime()

    query = """
    query ($search: String, $perPage: Int) {
      Page(page: 1, perPage: $perPage) {
        media(search: $search, type: ANIME) {
          id
          title { romaji english native }
          averageScore
          popularity
          episodes
          genres
          seasonYear
          description(asHtml: false)
          coverImage { large medium }
        }
      }
    }
    """
    variables = {
        "search": search_text.strip(),
        "perPage": limit,
    }

    try:
        response = requests.post(
            ANILIST_API_URL,
            json={"query": query, "variables": variables},
            timeout=8,
        )
        response.raise_for_status()
        data = response.json()
        raw_list = data.get("data", {}).get("Page", {}).get("media", [])

        results = []
        for item in raw_list:
            title_dict = item.get("title", {})
            main_title = title_dict.get("english") or title_dict.get("romaji") or "Без названия"
            alt_title = title_dict.get("romaji") if title_dict.get("english") else ""
            score = float(item.get("averageScore") or 0)
            genres = item.get("genres") or []
            clean_title = main_title.lower()
            is_iconic = any(k in clean_title or clean_title in k for k in ICONIC_MOMENTS_DB.keys())
            moments = generate_moments_for_anime(main_title, genres=genres, score=score)

            results.append({
                "id": item.get("id"),
                "title": main_title,
                "alt_title": alt_title,
                "score": score,
                "popularity": item.get("popularity") or 0,
                "episodes": item.get("episodes") or "?",
                "genres": genres,
                "year": item.get("seasonYear") or "",
                "cover": item.get("coverImage", {}).get("large") or "",
                "description": (item.get("description") or "").replace("\n", " ")[:180] + "...",
                "has_iconic": is_iconic,
                "has_youtube": True,
                "moments": moments,
            })
        return results
    except Exception as e:
        logger.error(f"Error searching anime on AniList: {e}")
        return []
