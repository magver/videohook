"""
Antigravity Bridge & Viral Prompt Engineering Engine for VideoHook
Generates high-converting, viral editing prompts, retention hooks,
and Telegram channel conversion funnels for Antigravity Pro.
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional


# High-converting Telegram call-to-action hooks
TELEGRAM_HOOK_TEMPLATES = {
    "epic_fight": {
        "badge": "🔥 ЭПИЧНЫЙ БОЙ / СЦЕНА",
        "hook_title": "«САМЫЙ ЖЕСТОКИЙ И КРАСИВЫЙ БОЙ В ИСТОРИИ АНИМЕ»",
        "cta_text": "🎬 Полную серию в 4K 60FPS без цензуры и с русской озвучкой выложил в Telegram: {tg_channel}",
        "banner_sub": "Смотри продолжение в шапке / в закрепленном сообщении",
        "tg_magnet": "Эксклюзивный дубляж + файл серии без сжатия в Telegram!",
    },
    "cliffhanger": {
        "badge": "⚡ ИНТРИГА / КЛИФФХЭНГЕР",
        "hook_title": "«ОН СКРЫВАЛ СВОЮ СИЛУ ВСЕ 10 ЛЕТ, НО КОГДА ЕГО ДРУГА ЗАДЕЛИ...»",
        "cta_text": "😱 Что произошло дальше? Финал этой битвы и продолжение смотри в Telegram: {tg_channel}",
        "banner_sub": "Вся правда о силе героя уже в закрепе нашего канала",
        "tg_magnet": "Продолжение уже в Telegram-канале (ссылка в профиле)!",
    },
    "theory_secret": {
        "badge": "🧠 ТАЙНА / РАЗБОР / ПАСХАЛКИ",
        "hook_title": "«99% ЗРИТЕЛЕЙ НЕ ЗАМЕТИЛИ ЭТУ ДЕТАЛЬ В 1 СЕЗОНЕ!»",
        "cta_text": "🔍 Полный разбор всех скрытых пасхалок манги и спойлеры ко 2 сезону в Telegram: {tg_channel}",
        "banner_sub": "Разбор теорий и секретные арты в закрепе канала",
        "tg_magnet": "Секретные материалы и закрытый аниме-чат в Telegram!",
    },
    "soundtrack_edit": {
        "badge": "🎧 ТРЕНДОВЫЙ ЭДИТ / ФОНК / МУЗЫКА",
        "hook_title": "«ЭТОТ ТРЕК ДЕЛАЕТ ЛЮБУЮ СЦЕНУ НА 1000% ЭПИЧНЕЕ»",
        "cta_text": "🎵 Трек из видео в максимальном качестве (FLAC/MP3) и пак живых обоев в Telegram: {tg_channel}",
        "banner_sub": "Качай оригинальный трек и обои по ссылке в описании",
        "tg_magnet": "Аудио-трек и обои 4K уже в закрепе Telegram!",
    },
}


def build_viral_antigravity_prompt(
    video_path: str,
    anime_title: str,
    clip_title: str = "",
    platform_origin: str = "YouTube Shorts",
    telegram_channel: str = "@anime_hook",
    hook_type: str = "epic_fight",
    target_formats: str = "Reels, TikTok, YouTube Shorts (9:16)",
) -> Dict[str, Any]:
    """
    Builds a professional, comprehensive viral prompt formatted specifically
    for Google Antigravity Pro to analyze, edit, enhance and export high-conversion shorts.
    """
    video_p = Path(video_path).resolve()
    clean_tg = telegram_channel.strip()
    if not clean_tg:
        clean_tg = "@anime_channel"
    elif not clean_tg.startswith("@") and not clean_tg.startswith("http"):
        clean_tg = f"@{clean_tg}"

    raw_strategy = TELEGRAM_HOOK_TEMPLATES.get(hook_type, TELEGRAM_HOOK_TEMPLATES["epic_fight"])
    strategy = {
        "badge": raw_strategy["badge"],
        "hook_title": raw_strategy["hook_title"],
        "cta_text": raw_strategy["cta_text"].format(tg_channel=clean_tg),
        "banner_sub": raw_strategy["banner_sub"],
        "tg_magnet": raw_strategy["tg_magnet"].format(tg_channel=clean_tg) if "{tg_channel}" in raw_strategy.get("tg_magnet", "") else raw_strategy["tg_magnet"],
    }

    # Generate hashtags
    clean_anime_tag = "".join(c for c in anime_title if c.isalnum())
    hashtags = (
        f"#{clean_anime_tag} #аниме #{anime_title.split()[0].lower()} #anime #animeedit "
        f"#shorts #tiktok #рекомендации #рек #fyp #тренды #анимемоменты #amv #топаниме"
    )

    prompt_markdown = f"""# 🚀 ЗАДАЧА ДЛЯ АНТИГРАВИТИ: ВИРУСНЫЙ МОНТАЖ АНИМЕ-РОЛИКА 9:16 С ВОРОНКОЙ В TELEGRAM

## 🎯 ГЛАВНАЯ ЦЕЛЬ
Отредактировать предоставленный аниме-клип для **TikTok, Instagram Reels и YouTube Shorts**, максимизировав удержание аудитории (Retention 110%+) и конверсию зрителей в подписчиков Telegram-канала **{clean_tg}**.

---

## 📁 ВХОДНЫЕ ДАННЫЕ
- **Локальный видеофайл**: `{video_p.as_posix()}`
- **Название аниме**: {anime_title}
- **Исходный эпизод / момент**: {clip_title or 'Вирусная кульминационная сцена'}
- **Целевые площадки**: {target_formats}
- **Telegram-канал автора**: `{clean_tg}`
- **Выбранная стратегия виральности**: {strategy['badge']}
- **ИИ-модель обработки**: ⚡ Gemini 3.8 Flash (High Reasoning / Fast)

---

## 🧠 СТРАТЕГИЯ ВИРУСНОГО УДЕРЖАНИЯ (RETENTION ARCHITECTURE)

### 1. 🎣 ХУК В ПЕРВЫЕ 0-3 СЕКУНДЫ (ШОК-ФАКТОР)
- **Цель**: Не дать зрителю смахнуть ролик.
- **Действие**:
  - Наложить в верхней трети экрана крупный контрастный заголовок:
    `🔥 {strategy['hook_title']}`
  - Добавить звуковой эффект удара (whoosh / sub-bass hit / riser) на первой секунде.
  - Применить легкий зум 1.05x -> 1.15x с фиксацией на глазах / эмоциях персонажа.

### 2. ⚡ ДИНАМИКА И КЛИПОВЫЙ МОНТАЖ (3 – 35 СЕКУНДЫ)
- **Обрезка «воды»**: Удалить любые паузы длиннее 0.4 секунды.
- **Динамические субтитры (ASS / TikTok style)**:
  - Центрированные субтитры крупным шрифтом (Montserrat / Arial Black) с белым текстом, черной обводкой 4px и **ярко-желтым (#FACC15) или неоново-зеленым (#10B981) выделением ключевых ударных слов**.
  - Показывать по 2-4 слова одновременно (высокий темп чтения).
- **Цветокоррекция (Anime Pop CC)**:
  - Насыщенность +15%, легкий контраст +10%, эффект легкой виньетки по краям для концентрации взгляда в центре кадра 9:16.

### 3. 📢 МОЩНЫЙ CALL-TO-ACTION (CTA) В TELEGRAM (ПОСЛЕДНИЕ 3-5 СЕКУНД)
- **Психологический триггер**: Зритель должен почувствовать непреодолимое желание узнать продолжение / забрать эксклюзив.
- **Оверлей на экране**:
  - Появление стилизованной плашки с иконкой Telegram:
    `📲 {clean_tg}`
  - Текст призыва:
    `«{strategy['cta_text']}»`
  - Добавить анимированную стрелку, указывающую вниз на описание / шапку профиля.

---

## 📝 ГОТОВОЕ ОПИСАНИЕ И ТЕГИ ДЛЯ ПУБЛИКАЦИИ

```text
🎬 НАЗВАНИЕ АНИМЕ: {anime_title}
---------------------------------------------
{strategy['hook_title']}

😱 {strategy['cta_text']}
👉 Переходи в Telegram: {clean_tg} (ссылка в шапке профиля)
---------------------------------------------
{hashtags}
```

---

## 🛠 ИНСТРУКЦИЯ ПО АВТОМАТИЗАЦИИ ДЛЯ АНТИГРАВИТИ

Антигравити, выполни следующие шаги прямо в проекте:
1. Проверь наличие видео по пути `{video_p.as_posix()}`.
2. Примени кадрирование в чистый 9:16 (1080x1920) с сохранением фокуса на персонажах.
3. Добавь верхнюю плашку с названием аниме: `🎬 {anime_title}`.
4. Добавь нижнюю плашку CTA с призывом перейти в `{clean_tg}`.
5. Экспортируй готовый вирусный ролик в папку `output_shorts/` с суффиксом `_viral_tg.mp4` и выведи ссылку на файл.
"""

    bundle_filename = f"antigravity_task_{video_p.stem}.md"

    return {
        "video_path": str(video_p),
        "anime_title": anime_title,
        "clip_title": clip_title,
        "telegram_channel": clean_tg,
        "hook_strategy": strategy,
        "hashtags": hashtags,
        "prompt_markdown": prompt_markdown,
        "bundle_filename": bundle_filename,
    }


def save_antigravity_bundle(
    video_path: str,
    anime_title: str,
    telegram_channel: str = "@anime_hook",
    hook_type: str = "epic_fight",
    output_dir: str = "output_shorts",
) -> str:
    """
    Saves the Antigravity prompt markdown file directly into the output directory
    alongside the video file so the user can easily reference or run it.
    """
    out_dir = Path(output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    bundle = build_viral_antigravity_prompt(
        video_path=video_path,
        anime_title=anime_title,
        telegram_channel=telegram_channel,
        hook_type=hook_type,
    )

    out_file = out_dir / bundle["bundle_filename"]
    out_file.write_text(bundle["prompt_markdown"], encoding="utf-8")
    return str(out_file)


def get_agentapi_cmd() -> List[str]:
    """Finds available Antigravity language_server / agentapi binary."""
    cands = [
        Path(r"C:\Users\vadim\AppData\Local\Programs\antigravity\resources\bin\language_server.exe"),
        Path(r"C:\Users\vadim\.gemini\antigravity\bin\agentapi.bat"),
    ]
    for c in cands:
        if c.exists():
            if c.suffix.lower() == ".exe":
                return [str(c), "agentapi"]
            return [str(c)]
    return ["agentapi"]


def launch_antigravity_job(
    video_path: str,
    anime_title: str,
    clip_title: str = "",
    telegram_channel: str = "@anime_empire",
    hook_type: str = "epic_fight",
    model: str = "flash",
    output_dir: str = "output_shorts",
) -> Dict[str, Any]:
    """
    1-CLICK AUTONOMOUS LAUNCHER:
    Creates a new conversation in Antigravity Pro, passes the complete viral
    task specification and video file, and returns the conversation ID and metadata.
    """
    task_file_path = save_antigravity_bundle(
        video_path=video_path,
        anime_title=anime_title,
        telegram_channel=telegram_channel,
        hook_type=hook_type,
        output_dir=output_dir,
    )
    cmd = get_agentapi_cmd()
    task_p = Path(task_file_path).resolve()

    import re
    import subprocess
    clean_anime = re.sub(r'[^a-zA-Zа-яА-Я0-9_ -]', '', anime_title)[:30]
    exec_prompt = f"Open and execute all instructions in file {task_p.as_posix()}"
    run_args = cmd + [
        "new-conversation",
        f"--model={model}",
        f"--title=Вирусный ролик: {clean_anime}",
        exec_prompt,
    ]
    res = subprocess.run(run_args, capture_output=True, text=True, encoding="utf-8")
    if res.returncode != 0:
        raise RuntimeError(f"agentapi execution failed: {res.stderr or res.stdout}")

    data = json.loads(res.stdout)
    new_conv = data.get("response", {}).get("newConversation", {})
    conv_id = new_conv.get("conversationId")
    if not conv_id:
        raise RuntimeError(f"Failed to obtain conversationId: {res.stdout}")

    return {
        "status": "ok",
        "conversation_id": conv_id,
        "task_file": str(task_p),
        "anime_title": anime_title,
        "telegram_channel": telegram_channel,
        "model": model,
        "conversation_url": f"conversation://{conv_id}",
    }


def get_antigravity_job_status(conversation_id: str) -> Dict[str, Any]:
    """
    Polls real-time transcript of Antigravity Pro conversation to stream live progress,
    active tool calls, and final response back into VideoHook.
    """
    brain_dir = Path(r"C:\Users\vadim\.gemini\antigravity\brain") / conversation_id
    transcript_file = brain_dir / ".system_generated" / "logs" / "transcript.jsonl"

    if not transcript_file.exists():
        return {
            "conversation_id": conversation_id,
            "status": "starting",
            "message": "Инициализация чата Antigravity Pro...",
            "steps_count": 0,
            "conversation_url": f"conversation://{conversation_id}",
        }

    steps = []
    try:
        lines = transcript_file.read_text(encoding="utf-8", errors="replace").splitlines()
        for line in lines:
            if line.strip():
                steps.append(json.loads(line))
    except Exception as e:
        pass

    if not steps:
        return {
            "conversation_id": conversation_id,
            "status": "starting",
            "message": "Чат Antigravity Pro запущен, ожидание первого шага...",
            "steps_count": 0,
            "conversation_url": f"conversation://{conversation_id}",
        }

    current_action = "Анализ задачи..."
    response_text = ""
    is_finished = False

    for s in reversed(steps):
        if s.get("type") == "PLANNER_RESPONSE":
            t_calls = s.get("tool_calls") or []
            if t_calls:
                t0 = t_calls[0]
                t_name = t0.get("name", "tool")
                raw_sum = t0.get("args", {}).get("toolSummary") or t0.get("args", {}).get("CommandLine", "")
                clean_sum = str(raw_sum).strip('\"\'')
                current_action = f"Выполняется инструмент: {t_name} ({clean_sum})"[:90]
                break
            elif s.get("content"):
                response_text = s.get("content")
                current_action = "Ответ получен"
                is_finished = True
                break
            elif s.get("thinking"):
                current_action = "Агент Antigravity Pro анализирует ролик и проектирует вирусный монтаж..."
                break

    return {
        "conversation_id": conversation_id,
        "status": "completed" if is_finished else "running",
        "message": current_action,
        "steps_count": len(steps),
        "response_text": response_text,
        "conversation_url": f"conversation://{conversation_id}",
    }
