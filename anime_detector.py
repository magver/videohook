"""
Anime Detector Module for VideoHook
Intelligently recognizes anime titles from video descriptions, titles, and hashtags
across YouTube Shorts, TikTok, Instagram Reels, VK, and RuTube.
"""

import re
from typing import Any, Dict, List, Optional, Tuple


# Comprehensive mapping of Russian and English titles, popular aliases, and keywords
ANIME_KNOWLEDGE_BASE: List[Dict[str, Any]] = [
    {
        "ru": "Клинок, рассекающий демонов",
        "en": "Demon Slayer: Kimetsu no Yaiba",
        "aliases": ["клинок рассекающий демонов", "клинок", "демон слейер", "истребитель демонов", "kimetsu no yaiba", "demon slayer", "тандзиро", "танжиро", "незуко", "несуко", "зеницу", "zenitsu", "inosuke", "rengoku", "кёдзюро"],
        "tags": ["demonslayer", "kimetsunoyaiba", "крд", "клинок", "истребительдемонов"],
    },
    {
        "ru": "Магическая битва",
        "en": "Jujutsu Kaisen",
        "aliases": ["магическая битва", "магичка", "магическаябитва", "jujutsu kaisen", "jjk", "годжо", "годзё", " годжо сатору", "годзё сатору", "satoru gojo", "сукуна", "sukuna", "мегуми", "megumi", "итадори", "itadori", "юта оккоцу"],
        "tags": ["jujutsukaisen", "jjk", "магическаябитва", "годжо", "сукуна", "gojo"],
    },
    {
        "ru": "Атака титанов",
        "en": "Attack on Titan",
        "aliases": ["атака титанов", "атакатитанов", "вторжение гигантов", "attack on titan", "aot", "shingeki no kyojin", "леви", "леви аккерман", "эрвин", "эрен", "эрен йегер", "eren", "levi", "микаса", "armin"],
        "tags": ["attackontitan", "aot", "shingekinokyojin", "атакатитанов", "леви", "эрен"],
    },
    {
        "ru": "Поднятие уровня в одиночку",
        "en": "Solo Leveling",
        "aliases": ["поднятие уровня в одиночку", "поднятие уровня", "соло левелинг", "solo leveling", "сун джин ву", "сон джин-ву", "сон джину", "джин ву", "дзинву", "jinwoo", "arise", "восстань"],
        "tags": ["sololeveling", "поднятиеуровня", "сололевелинг", "jinwoo", "дзинву"],
    },
    {
        "ru": "Ван Пис",
        "en": "One Piece",
        "aliases": ["ван пис", "ван-пис", "ванпис", "один кусок", "one piece", "луффи", "luffy", "зоро", "zoro", "санжи", "санри", "санди", "sanji", "gear 5", "5 гир", "шанкс", "кайдо"],
        "tags": ["onepiece", "ванпис", "луффи", "luffy", "zoro", "зоро"],
    },
    {
        "ru": "Тетрадь смерти",
        "en": "Death Note",
        "aliases": ["тетрадь смерти", "тетрадка смерти", "death note", "лайт", "лайт ягами", "ягами лайт", "light yagami", "l", "эль", "рязан", "рюк", "ryuk", "миса"],
        "tags": ["deathnote", "тетрадьсмерти", "лайтягами", "lightyagami"],
    },
    {
        "ru": "Человек-бензопила",
        "en": "Chainsaw Man",
        "aliases": ["человек бензопила", "человек-бензопила", "бензопила", "пила", "chainsaw man", "csm", "дэндзи", "дендзи", "denji", "макима", "makima", "пауэр", "power", "почита", "аки хаякава"],
        "tags": ["chainsawman", "человекбензопила", "макима", "дэндзи", "denji", "csm"],
    },
    {
        "ru": "Наруто / Боруто",
        "en": "Naruto / Boruto",
        "aliases": ["наруто", "наруто ураганные хроники", "боруто", "naruto", "naruto shippuden", "boruto", "саске", "итачи", "itachi", "sasuke", "минато", "какаши", "kakashi", "мадара", "madara", "пей пейн", "джирайя"],
        "tags": ["naruto", "наруто", "саске", "итачи", "madara", "boruto"],
    },
    {
        "ru": "Блич",
        "en": "Bleach",
        "aliases": ["блич", "bleach", "тысячелетняя кровавая война", "ихтиго", "ичиго", "ichigo", "айзен", "aizen", "улькиорра", "зараки кенпачи", "бьякуя", "рукия"],
        "tags": ["bleach", "блич", "ичиго", "айзен", "ichigo"],
    },
    {
        "ru": "Токийский гуль",
        "en": "Tokyo Ghoul",
        "aliases": ["токийский гуль", "токийскийгуль", "гуль", "tokyo ghoul", "канеки", "канеки кен", "kaneki", "kaneki ken", "1000-7", "я гуль"],
        "tags": ["tokyoghoul", "токийскийгуль", "канеки", "kaneki"],
    },
    {
        "ru": "Берсерк",
        "en": "Berserk",
        "aliases": ["берсерк", "berserk", "гатс", "гатц", "guts", "гриффит", "griffith", "каска", "клеймор"],
        "tags": ["berserk", "берсерк", "гатс", "guts"],
    },
    {
        "ru": "Хантер х Хантер",
        "en": "Hunter x Hunter",
        "aliases": ["хантер х хантер", "охотник х охотник", "hunter x hunter", "hxh", "гон", "киллуа", "killua", "gon", "хисока", "hisoka", "курупика", "меруем"],
        "tags": ["hunterxhunter", "hxh", "киллуа", "хисока", "хантер"],
    },
    {
        "ru": "Моя геройская академия",
        "en": "My Hero Academia",
        "aliases": ["моя геройская академия", "геройская академия", "мга", "my hero academia", "boku no hero academia", "mha", "bnha", "деку", "мидория", "бакуго", "бакугоу", "тодороки", "всемогущий"],
        "tags": ["myheroacademia", "mha", "мга", "моягеройскаяакадемия", "деку", "тодороки"],
    },
    {
        "ru": "Реинкарнация безработного",
        "en": "Mushoku Tensei: Jobless Reincarnation",
        "aliases": ["реинкарнация безработного", "безработный", "mushoku tensei", "jobless reincarnation", "рудеус", "руди", "эрис", "сильфи", "рокси"],
        "tags": ["mushokutensei", "реинкарнациябезработного", "рудеус"],
    },
    {
        "ru": "Невероятные приключения ДжоДжо",
        "en": "JoJo's Bizarre Adventure",
        "aliases": ["джоджо", "джо джо", "невероятные приключения джоджо", "jojo", "jojo's bizarre adventure", "джотаро", "дио", "дио брандо", "dio", "jotaro", "giorno", "джорно"],
        "tags": ["jojo", "джоджо", "jotaro", "dio"],
    },
    {
        "ru": "Ванпанчмен",
        "en": "One Punch Man",
        "aliases": ["ванпанчмен", "ван панч мен", "человек один удар", "one punch man", "opm", "сайтама", "saitama", "генос", "гароу", "garou"],
        "tags": ["onepunchman", "opm", "ванпанчмен", "сайтама"],
    },
    {
        "ru": "Семья шпиона",
        "en": "Spy x Family",
        "aliases": ["семья шпиона", "шпионская семья", "spy x family", "spyxfamily", "аня форджер", "аня", "anya", "лойд", "йор"],
        "tags": ["spyxfamily", "семьяшпиона", "аняфорджер"],
    },
    {
        "ru": "Гран Блю (Необъятный океан)",
        "en": "Grand Blue Dreaming",
        "aliases": ["гранд блю", "необъятный океан", "гран блю", "grand blue", "иори", "кохей", "дайвинг"],
        "tags": ["grandblue", "необъятныйокеан"],
    },
    {
        "ru": "Врата Штейна",
        "en": "Steins;Gate",
        "aliases": ["врата штейна", "врата штайнера", "steins gate", "steins;gate", "окабе", "окабэ", "курису", "маюри", "эл пси конгру"],
        "tags": ["steinsgate", "враташтейна", "окабе"],
    },
    {
        "ru": "Евангелион",
        "en": "Neon Genesis Evangelion",
        "aliases": ["евангелион", "ева", "evangelion", "neon genesis evangelion", "синдзи", "синдзи икари", "рей", "аска", "аска лэнгли"],
        "tags": ["evangelion", "евангелион", "аска", "синдзи"],
    },
    {
        "ru": "Сага о Винланде",
        "en": "Vinland Saga",
        "aliases": ["сага о винланде", "винланд сага", "винланд", "vinland saga", "торфинн", "аскеладд", "торкелль"],
        "tags": ["vinlandsaga", "сагаовинланде", "торфинн"],
    },
    {
        "ru": "Доктор Стоун",
        "en": "Dr. Stone",
        "aliases": ["доктор стоун", "д-р стоун", "dr. stone", "dr stone", "сэнку", "сенку", "хром"],
        "tags": ["drstone", "докторстоун", "сенку"],
    },
    {
        "ru": "Код Гиас",
        "en": "Code Geass",
        "aliases": ["код гиас", "код гиасс", "code geass", "лелуш", "лелуш ламперуж", "сузаку", "с.с."],
        "tags": ["codegeass", "кодгиас", "лелуш"],
    },
    {
        "ru": "Синий экзорцист",
        "en": "Blue Exorcist",
        "aliases": ["синий экзорцист", "blue exorcist", "ao no exorcist", "рин окумура", "юкио"],
        "tags": ["blueexorcist", "синийэкзорцист"],
    },
    {
        "ru": "Черный клевер",
        "en": "Black Clover",
        "aliases": ["черный клевер", "чёрный клевер", "black clover", "аста", "asta", "юно", "ями"],
        "tags": ["blackclover", "черныйклевер", "аста"],
    },
]

# Direct regex patterns often used by content creators in descriptions and titles
PATTERNS = [
    re.compile(r"(?:🎬|🍿|📺|🎌|🔥)?\s*(?:Аниме|Anime|Тайтл|Название|Анимешечка|Название аниме|Тайтл аниме)\s*[:—–\-]\s*([^\n\r#|,\.\!\?]+)", re.IGNORECASE),
    re.compile(r"(?:🎬|🍿)?\s*\[\s*(?:Аниме|Anime)\s*[:—–\-]\s*([^\]]+)\]", re.IGNORECASE),
    re.compile(r"«([^»]+)»", re.IGNORECASE),
    re.compile(r"\"([^\"]+)\"", re.IGNORECASE),
]


def clean_str(s: str) -> str:
    """Normalize string for search matching."""
    s = s.lower().strip()
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def detect_anime_title(
    title: str = "",
    description: str = "",
    tags: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Detects the anime title from video title, description, and tags.

    Returns dict with:
        - "found": bool
        - "display_title": formatted title (e.g. "Магическая битва (Jujutsu Kaisen)")
        - "ru_title": Russian title
        - "en_title": English title
        - "confidence": float (0.0 to 1.0)
        - "matched_alias": string that matched
        - "source_field": 'explicit_marker' | 'title_match' | 'tag_match' | 'description_match' | 'fallback_title'
    """
    combined_text = f"{title}\n{description}".strip()
    tags = tags or []

    # 1. Look for explicit "Аниме: <Название>" pattern in title & description
    for pattern in PATTERNS:
        match = pattern.search(combined_text)
        if match:
            extracted = match.group(1).strip()
            # Clean up trailing hashtags or quotes
            extracted = re.sub(r"#.*", "", extracted).strip()
            if len(extracted) >= 2 and len(extracted) <= 60:
                # Check if this extracted text matches any known anime in our KB
                matched_kb = _find_in_kb(extracted)
                if matched_kb:
                    return {
                        "found": True,
                        "display_title": f"{matched_kb['ru']} ({matched_kb['en']})",
                        "ru_title": matched_kb["ru"],
                        "en_title": matched_kb["en"],
                        "confidence": 0.98,
                        "matched_alias": extracted,
                        "source_field": "explicit_marker",
                    }
                else:
                    return {
                        "found": True,
                        "display_title": extracted,
                        "ru_title": extracted,
                        "en_title": extracted,
                        "confidence": 0.85,
                        "matched_alias": extracted,
                        "source_field": "explicit_marker",
                    }

    # 2. Check title against Knowledge Base aliases (prioritize video title)
    title_clean = clean_str(title)
    for entry in ANIME_KNOWLEDGE_BASE:
        for alias in entry["aliases"]:
            clean_al = clean_str(alias)
            # Match whole words or phrase
            pattern_word = rf"\b{re.escape(clean_al)}\b"
            if re.search(pattern_word, title_clean):
                return {
                    "found": True,
                    "display_title": f"{entry['ru']} ({entry['en']})",
                    "ru_title": entry["ru"],
                    "en_title": entry["en"],
                    "confidence": 0.95,
                    "matched_alias": alias,
                    "source_field": "title_match",
                }

    # 3. Check tags / hashtags
    for tag in tags:
        tag_clean = clean_str(tag).replace(" ", "")
        for entry in ANIME_KNOWLEDGE_BASE:
            for t in entry.get("tags", []):
                if t == tag_clean or t in tag_clean:
                    return {
                        "found": True,
                        "display_title": f"{entry['ru']} ({entry['en']})",
                        "ru_title": entry["ru"],
                        "en_title": entry["en"],
                        "confidence": 0.90,
                        "matched_alias": tag,
                        "source_field": "tag_match",
                    }

    # 4. Check description against Knowledge Base aliases
    desc_clean = clean_str(description)
    for entry in ANIME_KNOWLEDGE_BASE:
        for alias in entry["aliases"]:
            clean_al = clean_str(alias)
            pattern_word = rf"\b{re.escape(clean_al)}\b"
            if re.search(pattern_word, desc_clean):
                return {
                    "found": True,
                    "display_title": f"{entry['ru']} ({entry['en']})",
                    "ru_title": entry["ru"],
                    "en_title": entry["en"],
                    "confidence": 0.88,
                    "matched_alias": alias,
                    "source_field": "description_match",
                }

    # 5. Fallback: try to extract something reasonable from title
    # Remove #shorts, #anime, #edit, etc.
    cleaned_title = re.sub(r"#(shorts|аниме|anime|fyp|viral|edit|rek|рекомендации|врек)[^\s]*", "", title, flags=re.IGNORECASE).strip()
    cleaned_title = re.sub(r"[|—–\-].*", "", cleaned_title).strip()
    if cleaned_title and len(cleaned_title) >= 3:
        return {
            "found": True,
            "display_title": cleaned_title,
            "ru_title": cleaned_title,
            "en_title": cleaned_title,
            "confidence": 0.50,
            "matched_alias": cleaned_title,
            "source_field": "fallback_title",
        }

    return {
        "found": False,
        "display_title": "Не определено (Укажите вручную)",
        "ru_title": "",
        "en_title": "",
        "confidence": 0.0,
        "matched_alias": "",
        "source_field": "none",
    }


def _find_in_kb(text: str) -> Optional[Dict[str, Any]]:
    """Helper to find matching anime entry in knowledge base."""
    text_clean = clean_str(text)
    for entry in ANIME_KNOWLEDGE_BASE:
        if clean_str(entry["ru"]) in text_clean or text_clean in clean_str(entry["ru"]):
            return entry
        if clean_str(entry["en"]) in text_clean or text_clean in clean_str(entry["en"]):
            return entry
        for alias in entry["aliases"]:
            if clean_str(alias) == text_clean or clean_str(alias) in text_clean:
                return entry
    return None


def format_description_with_anime(
    original_description: str,
    anime_title: str,
    platform_name: str = "Shorts",
    has_subtitles: bool = True,
    no_watermark: bool = True,
) -> str:
    """
    Guarantees the anime title is prominently displayed in the description,
    along with viral hashtags and specifications.
    """
    header = (
        f"🎬 НАЗВАНИЕ АНИМЕ: {anime_title}\n"
        f"🌐 Платформа: {platform_name} | 🗣 Озвучка: Русская | 📝 Субтитры: {'Да (Русские)' if has_subtitles else 'Вшитые'}\n"
        f"🛡 Водяные знаки: {'Отсутствуют (Clean HD)' if no_watermark else 'Оригинал'}\n"
        f"{'-'*45}\n"
    )
    body = original_description.strip() if original_description else "Культовый момент из аниме с русской озвучкой и субтитрами."
    return f"{header}{body}"


NON_ANIME_KEYWORDS = [
    "cosplay", "косплей", "vlog", "влог", "reaction", "реакция", "irl",
    "practicing", "trick", "обзор", "review", "tutorial", "guitar", "piano",
    "кавер", "cover", "parody", "пародия", "challenge", "челлендж", "drawing",
    "рисунок", "how to", "live action", "фильм с актерами", "workout", "фигурка",
    "unboxing", "распаковка", "lego", "лего", "tiktok cringe", "своими руками",
    "speedpaint", "speed paint", "reaction mashup", "реакции", "лайфхак", "блогер",
    "vlogger", "vlogging", "haul", "asmr", "асмпр"
]

ANIME_POSITIVE_KEYWORDS = [
    "аниме", "anime", "отрывок", "сцена", "серия", "эпизод", "бой", "fight",
    "amv", "озвучка", "субтитры", "дубляж", "клип", "сабы", "клинок", "демон",
    "магическая", "титан", "leveling", "луффи", "наруто", "блич", "гуль",
    "берсерк", "ягами", "джоджо", "сатору", "годжо", "джин ву", "леви",
    "эрен", "сукуна", "дендзи", "дэндзи", "макима", "лайт", "ван пис", "one piece",
    "animation", "анимация", "мульт", "season", "сезон", "ep", "series"
]


def is_genuine_anime_video(title: str, description: str = "") -> bool:
    """
    Strictly verifies that the video title and description belong to an actual
    anime animation rather than real-life vloggers, cosplay, reactions, or covers.
    """
    title_lower = title.lower()
    desc_lower = description.lower()

    # 1. Immediate rejection if strong non-anime keyword is found in title
    for bad in NON_ANIME_KEYWORDS:
        if bad in title_lower:
            return False

    # 2. Check if recognized in ANIME_KNOWLEDGE_BASE
    for entry in ANIME_KNOWLEDGE_BASE:
        for alias in entry["aliases"]:
            if len(alias) >= 3 and alias in title_lower:
                return True

    # 3. Check for positive anime tokens
    for pos in ANIME_POSITIVE_KEYWORDS:
        if pos in title_lower:
            return True

    return False

