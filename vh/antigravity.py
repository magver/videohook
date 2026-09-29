"""Мост VideoHook ⇄ Google Antigravity (агент на Gemini 3.8 Flash).

Протокол обмена — «папка задачи» (надёжно, прозрачно, работает и без API):

  workspace/antigravity/<job>/
      BRIEF.md              ← задание для агента (промпт)
      job.json              ← машиночитаемые параметры
      input/source_cut.mp4  ← чистый фрагмент без надписей (исходное соотношение сторон)
      input/draft_9x16.mp4  ← черновик VideoHook (раскладка, тексты, музыка) — ориентир
      input/storyboard.jpg  ← раскадровка с таймкодами
      input/music/*.mp3, input/fonts/*.ttf
      output/               → агент кладёт сюда: final.mp4, cover.jpg, result.json, status.txt

Запуск: agentapi `new-conversation` через мост agbridge (находит запущенный Antigravity сам);
если Antigravity недоступен — вручную, промпт копируется в буфер.
Сторож (watcher) каждые несколько секунд проверяет output/ и импортирует результат в библиотеку.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import agbridge, library
from .core import (FONTS_DIR, JOBS_DIR, MUSIC_DIR, PUBLISH_DIR, get_settings, new_id,
                   rel_to_work, slugify)
from .render import contact_sheet, extract_thumbnail

log = logging.getLogger("videohook.antigravity")

MOOD_DIRECTION = {
    "epic": "Нарастающий ритм: резкие склейки по битам, панч-зумы 1.0→1.15 на ударах, короткий стоп-кадр + вспышка на кульминации, басовый drop под главный удар.",
    "dark": "Мрачная атмосфера: приглушённые холодные тона, медленный наезд камеры, тяжёлый фонк/бас, глитч-переход на кульминации, пауза тишины перед ударом.",
    "twist": "Интрига: держать зрителя в неведении, медленный зум на лицо в момент осознания, резкая тишина → удар звука на разгадке, стоп-кадр с вопросом.",
    "emotional": "Эмоции: мягкие переходы, замедление 0.8x в пиковый момент, тёплая цветокоррекция, лёгкая виньетка, музыка тише, реплики персонажей на первом плане.",
    "romantic": "Нежность: пастельная цветокоррекция, плавные наезды, мягкое размытие фона, lo-fi трек.",
    "funny": "Комедия: быстрый темп, зум-панчи на реакции (1.0→1.3 за 0.15 c), звуковые акценты, стоп-кадр на смешном лице, без фоновой музыки поверх диалога.",
}


def _brain_dir() -> Path:
    return agbridge.brain_dir()


def find_agentapi() -> Optional[List[str]]:
    """Команда agentapi запущенного Antigravity (None — Antigravity не запущен и не установлен)."""
    servers = agbridge.scan()
    if servers:
        return [servers[0]["exe"], "agentapi"]
    return ["antigravity"] if agbridge.available() else None


def status() -> Dict[str, Any]:
    return agbridge.status()


# ---------------------------------------------------------------------------
# Сборка задачи
# ---------------------------------------------------------------------------
def build_brief(job: Dict[str, Any], job_dir: Path) -> str:
    s = get_settings()
    mood = job.get("mood", "epic")
    lines_txt = "\n".join(f"  - {kl['t']:.1f} c: «{kl['text']}»" for kl in job.get("key_lines", [])) or "  - (нет данных)"
    ideas = "\n".join(f"  - {i}" for i in job.get("edit_ideas", [])) or "  - на твоё усмотрение по направлению ниже"
    music_files = ", ".join(p.name for p in (job_dir / "input" / "music").glob("*.mp3")) or "нет"
    parts = job.get("parts_in_cut") or []
    parts_txt = ("; части для склейки: " + ", ".join(f"{a:.1f}–{b:.1f} c" for a, b in parts)) if len(parts) > 1 else ""
    accents_txt = ", ".join(f"{a:.1f} c" for a in job.get("accents_in_cut") or []) or "найди сам по звуку и действию"
    sm = job.get("slowmo_in_cut")
    slowmo_txt = (f"замедление 0.5x на {sm['t'] - sm['dur'] / 2:.1f}–{sm['t'] + sm['dur'] / 2:.1f} c (кульминация)"
                  if sm else "замедление — только если усиливает кульминацию, не в диалоге")
    return f"""# Задача VideoHook: вирусный вертикальный ролик — {job['anime_name']}

Ты — монтажёр вирусных аниме-шортсов. Работай полностью автономно в папке:
`{job_dir.as_posix()}`
Модель: {s.get('antigravity_model')}. Используй свои мультимодальные возможности: посмотри видео и раскадровку.

## Входные данные (папка `input/`)
- `source_cut.mp4` — чистый фрагмент сцены без надписей ({job['source_duration']:.1f} c).
- `draft_9x16.mp4` — черновик VideoHook: раскладка «{job['template']}», тексты, музыка. Это ориентир, улучши его.
- `storyboard.jpg` — раскадровка 4×4 по времени.
- `music/`: {music_files}. {('Выбранный трек: `' + job['music'] + '` — тихо, под родным звуком.') if job.get('music') else 'Музыка НЕ нужна: основа — родной звук сцены (голоса, удары, саундтрек аниме).'}
- `subtitles.srt` — русские субтитры по времени `source_cut.mp4` ({len(job.get('subtitles') or [])} реплик).
- `fonts/Rubik-ExtraBold.ttf` — фирменный шрифт с кириллицей.
- `job.json` — все параметры в машиночитаемом виде.

## Сцена
- Аниме: **{job['anime_name']}** (© {job.get('studio') or 'правообладатель'})
- Момент: {job['title']} {('— ' + job['episode']) if job.get('episode') else ''}
- Хук на экране: «{job['hook']}»
- Подпись: «{job.get('caption') or ''}»
- Ключевые реплики:
{lines_txt}
- Идеи монтажа от анализа:
{ideas}

## Творческое направление ({mood})
{MOOD_DIRECTION.get(mood, MOOD_DIRECTION['epic'])}

## Стиль канала
Прочитай `STYLE.md` в папке задачи — это правила, выученные на примерах и оценках автора канала.
Раздел «Мои правила» — обязателен; остальные правила применяй, если они не противоречат требованиям ниже.

## Сюжет сцены
{job.get('story') or 'Определи сам: завязка → кульминация → развязка.'}
Отрезок в `source_cut.mp4`: {job['segment_in_cut']['start']:.1f}–{job['segment_in_cut']['end']:.1f} c{parts_txt}.

## Обязательная структура ролика
1. **Принцип: максимум, потом сокращаем только пустое.** Черновик уже собран по смысловой карте (`job.json` → `beats`):
   это ВСЯ сцена нужной длины. Твоя задача — сделать её динамичной, а не короче.
   - НЕЛЬЗЯ вырезать реплики и биты со `speech: true`, завязку перед кульминацией, кульминацию, развязку и реакцию.
   - Сокращать можно только биты без речи с низкими `importance`/`intensity` (пустые проходы, паузы > 0.8 c) — и только если
     ролик длиннее {s.get('clip_max_seconds', 58)} c. Итог не короче {s.get('clip_min_seconds', 35)} c, если исходник позволяет.
   - Не начинай посреди фразы и НЕ обрывай финал: последняя реплика и реакция должны прозвучать полностью.
2. **0–1.5 c — хук**: сильный первый кадр, панч-зум, текст-хук сверху. Можно дать 1–2 c кульминации как тизер,
   но затем сцена идёт с завязки.
3. **Динамика по всему ролику, а не в одной точке**: панч-зумы 1.0→1.12 и короткие вспышки на ударных моментах
   ({accents_txt}); медленный наезд камеры 1.00→1.06 на спокойных кусках; 2–4 смены крупности на ключевых репликах
   (зум-ин на лицо говорящего); лёгкая тряска на самых сильных ударах; {slowmo_txt}. Кадр не должен стоять без движения > 3 c.
4. **Переходы незаметные**: внутри сцены — кроссфейд картинки 0.4–0.6 c и звука той же длины (J/L-cut: звук следующего
   куска начинается чуть раньше картинки). Никаких вспышек и «вжухов» на склейках внутри одной сцены — это режет глаз.
5. **Звук**: основа — РОДНОЙ звук сцены (голоса, удары, оригинальный саундтрек). Голоса не глушить и не вырезать.
   Компрессия и лёгкий подъём низа для «плотности»; низкий «удар» — только поверх родного звука на акцентах.
6. **Субтитры**: русские, из `input/subtitles.srt` (проверь тайминги по речи), по 1–2 строки, белый, чёрная обводка,
   ударное слово — #FACC15; держи их выше нижних 320 px.
7. **Финал**: дай кадру «подышать» 0.4–0.8 c после последней реплики; мягкое затухание картинки и звука 0.5–0.8 c.
8. Кадр 1080×1920, 30 или 60 fps.

## Авторский слой и авторские права (обязательно)
- Ролик — трансформирующий фан-обзор, а не перезалив: сохраняй авторскую подложку — заголовок-контекст, подпись/комментарий, прогресс-бар, подпись канала `{s.get('brand_handle')}`.
- Внизу мелко всегда: «{job['credit']}».
- Используй только короткий фрагмент (не более {s.get('clip_max_seconds', 45)} c), не вырезай и не маскируй чужие логотипы/водяные знаки, не зеркаль и не ускоряй ради обхода Content ID.
- Дополнительную музыку бери только из `input/music/` (лицензированная библиотека VideoHook).

## Технические требования
- H.264 High, yuv420p, CRF ≤ 20, AAC 44.1 кГц 192 кбит/с, громкость −14 LUFS (true peak ≤ −1.5 dB), `-movflags +faststart`.
- Безопасные зоны: не ставь важный текст в нижние 320 px и правые 120 px (интерфейс TikTok/Reels).
- FFmpeg доступен как `{job.get('ffmpeg', 'ffmpeg')}`.

## Результат (строго)
Во время работы дописывай короткие статусы в `output/status.txt` (одна строка на шаг).
В конце положи:
- `output/final.mp4` — готовый ролик;
- `output/cover.jpg` — обложка 1080×1920 (самый яркий кадр + хук крупно);
- `output/result.json` (записывай последним — это сигнал VideoHook, что всё готово):
```json
{{"status": "done", "hook": "…", "duration": 0,
  "captions": {{"youtube": {{"title": "… #shorts", "description": "…", "tags": ["…"]}},
               "tiktok": {{"caption": "…"}}, "instagram": {{"caption": "…"}}}},
  "pinned_comment": "вопрос зрителям", "edits": ["что сделано"], "notes": "…"}}
```
Если что-то пошло не так — `result.json` с `"status": "error"` и описанием.
"""


def to_srt(items: List[Dict[str, Any]]) -> str:
    def ts(t: float) -> str:
        t = max(0.0, t)
        return f"{int(t // 3600):02d}:{int(t % 3600 // 60):02d}:{int(t % 60):02d},{int(round(t % 1 * 1000)) % 1000:03d}"
    return "\n".join(f"{i}\n{ts(x['start'])} --> {ts(x['end'])}\n{x['text']}\n" for i, x in enumerate(items, 1))


def create_job(clip_id: str) -> Dict[str, Any]:
    """Готовит папку задачи из клипа (нужны source_file и render.file)."""
    from .core import ffmpeg_bin, probe, resolve_work
    from .core import run_ffmpeg

    clip = library.require_clip(clip_id)
    s = get_settings()
    if not clip.get("source_file"):
        raise RuntimeError("У клипа нет исходника — сначала скачайте его")
    job_id = new_id("ag_")
    job_dir = JOBS_DIR / f"{job_id}_{slugify(clip.get('anime') or 'anime', 24)}"
    inp = job_dir / "input"
    (inp / "music").mkdir(parents=True, exist_ok=True)
    (inp / "fonts").mkdir(parents=True, exist_ok=True)
    (job_dir / "output").mkdir(parents=True, exist_ok=True)

    src = resolve_work(clip["source_file"])
    seg = clip.get("segment") or {"start": 0, "end": min(probe(src)["duration"], s.get("clip_max_seconds", 45))}
    cut = inp / "source_cut.mp4"
    margin = 6.0   # запас до и после, чтобы агент видел контекст и мог сдвинуть границы к фразам
    st = max(0.0, float(seg["start"]) - margin)
    run_ffmpeg(["-ss", f"{st:.2f}", "-i", str(src), "-t", f"{float(seg['end']) - st + margin:.2f}",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-c:a", "aac", "-b:a", "192k", str(cut)])
    draft = clip.get("render", {}).get("file")
    if draft:
        shutil.copy2(resolve_work(draft), inp / "draft_9x16.mp4")
    try:
        contact_sheet(str(cut), str(inp / "storyboard.jpg"))
    except Exception as exc:  # noqa: BLE001
        log.warning("storyboard: %s", exc)
    for mp3 in MUSIC_DIR.glob("*.mp3"):
        shutil.copy2(mp3, inp / "music" / mp3.name)
    for ttf in FONTS_DIR.glob("*.ttf"):
        shutil.copy2(ttf, inp / "fonts" / ttf.name)

    render_params = clip.get("render", {}).get("params", {})
    job = {
        "job_id": job_id,
        "clip_id": clip_id,
        "anime_name": clip.get("anime_name") or clip.get("anime"),
        "studio": clip.get("studio", ""),
        "title": clip.get("title", ""),
        "episode": clip.get("episode", ""),
        "hook": clip.get("hook", ""),
        "caption": clip.get("caption", ""),
        "mood": clip.get("mood", "epic"),
        "key_lines": [{"t": max(0.0, float(k["t"]) - st), "text": k["text"]} for k in clip.get("key_lines", [])],
        "edit_ideas": clip.get("edit_ideas", []),
        "credit": clip.get("credit", ""),
        "template": render_params.get("template", "cinema"),
        "music": f"music/{render_params.get('music')}.mp3" if render_params.get("music") not in (None, "none", "auto") else "",
        "source_url": clip.get("source_url", ""),
        "source_duration": probe(cut)["duration"],
        "segment_in_cut": {"start": float(seg["start"]) - st, "end": float(seg["end"]) - st},
        "parts_in_cut": [[float(a) - st, float(b) - st] for a, b in clip.get("parts") or []],
        "accents_in_cut": [round(float(a) - st, 2) for a in clip.get("accents") or [] if float(a) >= st],
        "slowmo_in_cut": ({"t": float(clip["slowmo"]["t"]) - st, "dur": float(clip["slowmo"].get("dur", 1.2))}
                          if clip.get("slowmo") else None),
        "transition": clip.get("transition", ""),
        "story": (clip.get("ai") or {}).get("story", ""),
        "beats": [{**b, "start": round(float(b["start"]) - st, 2), "end": round(float(b["end"]) - st, 2)}
                  for b in clip.get("beats") or [] if float(b["end"]) > st],
        "subtitles": [{"start": round(float(x["start"]) - st, 2), "end": round(float(x["end"]) - st, 2), "text": x["text"]}
                      for x in clip.get("subtitles") or [] if float(x["end"]) > st],
        "handle": s.get("brand_handle"),
        "ffmpeg": ffmpeg_bin(),
        "output_contract": ["output/final.mp4", "output/cover.jpg", "output/result.json", "output/status.txt"],
        "created": time.time(),
    }
    (job_dir / "job.json").write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
    (inp / "subtitles.srt").write_text(to_srt(job["subtitles"]), encoding="utf-8")
    from .style import read_guide

    (job_dir / "STYLE.md").write_text(read_guide(), encoding="utf-8")
    brief = build_brief(job, job_dir)
    (job_dir / "BRIEF.md").write_text(brief, encoding="utf-8")

    library.update_clip(clip_id, {"ag": {"job_id": job_id, "dir": rel_to_work(job_dir), "status": "prepared",
                                         "message": "Задача подготовлена", "conversation_id": ""}},
                        note="Задача для Antigravity подготовлена")
    return {"job_id": job_id, "dir": str(job_dir), "brief": brief}


def launch_prompt(job_dir: Path) -> str:
    return (f"Открой и выполни все инструкции из файла {(job_dir / 'BRIEF.md').as_posix()} . "
            f"Работай автономно до появления output/result.json.")


def launch(clip_id: str) -> Dict[str, Any]:
    """Создаёт диалог в Antigravity через agentapi. Если agentapi нет — режим ручной вставки."""
    from .core import resolve_work

    clip = library.require_clip(clip_id)
    ag = clip.get("ag") or {}
    if not ag.get("dir"):
        create_job(clip_id)
        clip = library.require_clip(clip_id)
        ag = clip["ag"]
    job_dir = resolve_work(ag["dir"])
    prompt = launch_prompt(job_dir)
    cmd = find_agentapi()
    if not cmd:
        library.update_clip(clip_id, {"ag": {"status": "manual", "message": "Вставьте промпт в Antigravity",
                                             "prompt": prompt}}, note="Antigravity: ручной запуск")
        library.advance_stage(clip_id, "in_antigravity")
        return {"mode": "manual", "prompt": prompt, "dir": str(job_dir)}
    title = f"VideoHook: {clip.get('anime', '')[:30]} — {clip.get('title', '')[:30]}"
    chat = library.clip_chat(clip)
    try:
        conv_id = chat["id"]
        if conv_id:
            try:
                agbridge.send_message(conv_id, prompt, title)   # тот же чат, где шёл анализ клипа
            except agbridge.AntigravityError:
                conv_id = ""
        if not conv_id:
            conv_id = agbridge.new_conversation(prompt, title)
            agbridge._register(conv_id, clip_id)
            chat["id"] = conv_id
            library.save_chat(clip_id, chat)
    except (agbridge.AntigravityError, OSError, subprocess.TimeoutExpired) as exc:
        library.update_clip(clip_id, {"ag": {"status": "manual", "prompt": prompt,
                                             "message": f"Antigravity не ответил ({str(exc)[:160]}). Вставьте промпт вручную"}})
        library.advance_stage(clip_id, "in_antigravity")
        return {"mode": "manual", "prompt": prompt, "dir": str(job_dir), "error": str(exc)[:400]}
    library.update_clip(clip_id, {"ag": {"status": "running", "conversation_id": conv_id, "prompt": prompt,
                                         "message": "Агент запущен", "started": time.time()}},
                        note=f"Antigravity: диалог {conv_id}")
    library.advance_stage(clip_id, "in_antigravity")
    return {"mode": "agentapi", "conversation_id": conv_id, "dir": str(job_dir)}


# ---------------------------------------------------------------------------
# Отслеживание и импорт результата
# ---------------------------------------------------------------------------
def _transcript_status(conv_id: str) -> Optional[str]:
    f = _brain_dir() / conv_id / ".system_generated" / "logs" / "transcript.jsonl"
    if not f.exists():
        return None
    try:
        rows = [json.loads(line) for line in f.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()]
    except ValueError:
        return None
    for r in reversed(rows):
        if r.get("type") != "PLANNER_RESPONSE":
            continue
        calls = r.get("tool_calls") or []
        if calls:
            args = calls[0].get("args", {})
            summary = args.get("toolSummary") or args.get("CommandLine") or calls[0].get("name", "")
            return f"Агент: {str(summary).strip()[:110]}"
        if r.get("thinking"):
            return "Агент анализирует видео и планирует монтаж…"
    return f"Шагов агента: {len(rows)}"


def check_job(clip: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Проверяет папку output/. Возвращает обновлённый клип, если что-то изменилось."""
    from .core import resolve_work

    ag = clip.get("ag") or {}
    if not ag.get("dir") or ag.get("status") in ("done", "error"):
        return None
    job_dir = resolve_work(ag["dir"])
    out = job_dir / "output"
    result_f = out / "result.json"
    final = out / "final.mp4"
    status_f = out / "status.txt"

    msg = ag.get("message", "")
    if status_f.exists():
        tail = [ln for ln in status_f.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()]
        if tail:
            msg = tail[-1][:160]
    elif ag.get("conversation_id"):
        msg = _transcript_status(ag["conversation_id"]) or msg

    result: Dict[str, Any] = {}
    if result_f.exists():
        try:
            result = json.loads(result_f.read_text(encoding="utf-8"))
        except ValueError:
            result = {}
    done_by_file = final.exists() and time.time() - final.stat().st_mtime > 20 and final.stat().st_size > 100_000
    if result.get("status") == "error":
        return library.update_clip(clip["id"], {"ag": {"status": "error", "message": result.get("notes") or "Агент сообщил об ошибке"}},
                                   note="Antigravity: ошибка")
    if (result.get("status") == "done" and final.exists()) or (done_by_file and not result):
        return import_result(clip["id"], result)
    if msg != ag.get("message"):
        return library.update_clip(clip["id"], {"ag": {"message": msg,
                                                       "status": "running" if ag.get("status") != "manual" else "manual"}})
    return None


def import_result(clip_id: str, result: Dict[str, Any]) -> Dict[str, Any]:
    from .core import resolve_work

    clip = library.require_clip(clip_id)
    job_dir = resolve_work(clip["ag"]["dir"])
    PUBLISH_DIR.mkdir(parents=True, exist_ok=True)
    base = f"{slugify(clip.get('anime') or 'anime', 24)}_{slugify(clip.get('title') or 'clip', 30)}_{clip_id[-4:]}"
    final_dst = PUBLISH_DIR / f"{base}.mp4"
    shutil.copy2(job_dir / "output" / "final.mp4", final_dst)
    cover_src = job_dir / "output" / "cover.jpg"
    cover_dst = PUBLISH_DIR / f"{base}.jpg"
    if cover_src.exists():
        shutil.copy2(cover_src, cover_dst)
    else:
        try:
            extract_thumbnail(str(final_dst), str(cover_dst))
        except Exception:  # noqa: BLE001
            cover_dst = None  # type: ignore[assignment]
    patch: Dict[str, Any] = {
        "final_file": rel_to_work(final_dst),
        "cover": rel_to_work(cover_dst) if cover_dst else "",
        "ag": {"status": "done", "message": "Готово — ролик импортирован", "finished": time.time(),
               "edits": result.get("edits", []), "notes": result.get("notes", "")},
    }
    if result.get("captions"):
        from .publish import apply_channel

        patch["publish"] = {"captions": apply_channel(dict(result["captions"]), get_settings())}
    if result.get("pinned_comment"):
        patch["pinned_comment"] = result["pinned_comment"]
    library.update_clip(clip_id, patch, note="Antigravity: результат импортирован")
    return library.advance_stage(clip_id, "ready")


def use_draft_as_final(clip_id: str) -> Dict[str, Any]:
    """Пропустить Antigravity: опубликовать локальный рендер как финальный."""
    from .core import resolve_work

    clip = library.require_clip(clip_id)
    draft = (clip.get("render") or {}).get("file")
    if not draft:
        raise RuntimeError("Нет локального рендера")
    PUBLISH_DIR.mkdir(parents=True, exist_ok=True)
    base = f"{slugify(clip.get('anime') or 'anime', 24)}_{slugify(clip.get('title') or 'clip', 30)}_{clip_id[-4:]}"
    dst = PUBLISH_DIR / f"{base}.mp4"
    shutil.copy2(resolve_work(draft), dst)
    cover = PUBLISH_DIR / f"{base}.jpg"
    try:
        extract_thumbnail(str(dst), str(cover))
    except Exception:  # noqa: BLE001
        cover = None  # type: ignore[assignment]
    library.update_clip(clip_id, {"final_file": rel_to_work(dst), "cover": rel_to_work(cover) if cover else ""},
                        note="Локальный рендер отправлен в публикацию")
    return library.advance_stage(clip_id, "ready")


class Watcher(threading.Thread):
    def __init__(self, interval: float = 5.0):
        super().__init__(daemon=True, name="ag-watcher")
        self.interval = interval
        self.on_ready = None  # callback(clip) — автопубликация

    def run(self) -> None:
        while True:
            try:
                for clip in library.list_clips("in_antigravity"):
                    updated = check_job(clip)
                    if updated and updated.get("stage") == "ready" and self.on_ready:
                        self.on_ready(updated)
            except Exception as exc:  # noqa: BLE001
                log.warning("watcher: %s", exc)
            time.sleep(self.interval)
