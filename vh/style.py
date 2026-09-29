"""Память стиля канала: правила, выученные на примерах и оценках автора.

Дообучить саму модель через Antigravity нельзя, поэтому «обучение» устроено как постоянная память:
  * style_guide.md — живой файл правил. Раздел «Мои правила» пишет только автор; остальные разделы
    ИИ пересобирает из примеров и оценок. Файл добавляется во все промпты (моменты, анализ, описания, монтаж);
  * примеры — ролики, которые нравятся автору (ссылка + что нравится): скачиваются, измеряются
    (длина, склейки в минуту, средний план) и разбираются Gemini на конкретные правила;
  * оценки — 👍/👎 с комментарием к своим роликам, вместе с параметрами ролика (длина, части, эффекты, хук).
"""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .core import DATA_DIR, JsonStore, new_id, probe, rel_to_work, tasks

log = logging.getLogger("videohook.style")

GUIDE_FILE = DATA_DIR / "style_guide.md"
USER_HEADER = "## Мои правила"
AI_HEADER = "## Выучено на примерах и оценках"
FEEDBACK_BATCH = 5        # после скольких новых оценок пересобирать правила
MAX_PROMPT_CHARS = 7000

_store = JsonStore(DATA_DIR / "style_examples.json", {"examples": [], "feedback": [], "since_rebuild": 0})

DEFAULT_GUIDE = f"""# Стиль канала

{USER_HEADER}
<!-- Этот раздел ИИ не меняет. Пишите свои правила по одному в строке, например:
- Хук всегда вопросом к зрителю
- Не брать сцены с кровью крупным планом -->

{AI_HEADER}
<!-- Раздел пересобирается автоматически из примеров и оценок. Можно править — правки учтутся при следующей сборке. -->
- Ролик — законченная мини-история: завязка, кульминация, развязка; реплики не обрываются.
- Родной звук сцены важнее музыки; голоса не глушить.
- Склейки внутри сцены — незаметные (кроссфейд 0.4–0.6 c), движение в кадре не реже чем раз в 3 c.
"""


# ---------------------------------------------------------------------------
# Файл правил
# ---------------------------------------------------------------------------
def read_guide() -> str:
    try:
        return GUIDE_FILE.read_text(encoding="utf-8")
    except OSError:
        return DEFAULT_GUIDE


def write_guide(text: str) -> str:
    GUIDE_FILE.parent.mkdir(parents=True, exist_ok=True)
    GUIDE_FILE.write_text(text.strip() + "\n", encoding="utf-8")
    return read_guide()


def split_guide(text: str) -> Dict[str, str]:
    """Разделы «Мои правила» (только автор) и остальное (пересобирает ИИ)."""
    if USER_HEADER in text:
        head, rest = text.split(USER_HEADER, 1)
        user, _, ai = rest.partition(AI_HEADER)
        return {"user": user.strip(), "ai": ai.strip()}
    return {"user": "", "ai": text.strip()}


def join_guide(user: str, ai: str) -> str:
    return f"# Стиль канала\n\n{USER_HEADER}\n{user.strip()}\n\n{AI_HEADER}\n{ai.strip()}\n"


def prompt_block() -> str:
    """Правила стиля для вставки в промпт (без служебных комментариев)."""
    text = re.sub(r"<!--.*?-->", "", read_guide(), flags=re.S)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) > MAX_PROMPT_CHARS:
        text = text[:MAX_PROMPT_CHARS] + "\n…"
    if not text:
        return ""
    return ("\n\nСТИЛЬ КАНАЛА — правила, выученные на примерах и оценках автора. Следуй им; раздел «Мои правила» "
            "важнее всего остального:\n" + text + "\n")


# ---------------------------------------------------------------------------
# Примеры
# ---------------------------------------------------------------------------
def list_examples() -> List[Dict[str, Any]]:
    with _store.lock:
        return list(reversed(_store.load().get("examples", [])))


def delete_example(ex_id: str) -> None:
    with _store.lock:
        data = _store.load()
        data["examples"] = [e for e in data.get("examples", []) if e["id"] != ex_id]
        _store.save()


def measure(path: str) -> Dict[str, Any]:
    """Объективные метрики ролика: длина, склейки, темп, громкость."""
    from .discovery import audio_envelope, scene_cuts

    info = probe(path)
    dur = info["duration"] or 0.0
    cuts = scene_cuts(path, threshold=0.3)
    env = audio_envelope(path, hop=0.5) if info["has_audio"] else []
    loud = sorted(env)
    dyn = round((loud[int(len(loud) * 0.9)] / (loud[int(len(loud) * 0.5)] or 1.0)), 2) if len(loud) > 4 else None
    return {"duration": round(dur, 1), "width": info["width"], "height": info["height"],
            "vertical": info["height"] > info["width"], "cuts": len(cuts),
            "cuts_per_min": round(len(cuts) / (dur / 60), 1) if dur else 0,
            "avg_shot": round(dur / (len(cuts) + 1), 2) if dur else 0, "audio_dynamics": dyn}


def study_example(url: str, note: str = "", task=None) -> Dict[str, Any]:
    """Скачивает пример, измеряет, отдаёт Gemini на разбор и обновляет правила."""
    from . import discovery, gemini
    from .core import RENDERS_DIR
    from .render import extract_audio, storyboards

    t = task.update if task else (lambda *a, **k: None)
    t(0.03, "Скачиваю пример…")
    info = {}
    try:
        info = discovery.video_info(url)
    except Exception as exc:  # noqa: BLE001
        log.warning("example info: %s", exc)
    dur = float(info.get("duration") or 0)
    section = (0.0, 240.0) if dur > 300 else None   # длинные ролики — первые 4 минуты
    dl = discovery.download(url, section=section, name_hint="example",
                            progress=lambda p, m: t(0.05 + 0.4 * p, m))
    t(0.5, "Измеряю темп, склейки, звук…")
    metrics = measure(dl["path"])
    ex_id = new_id("ex_")
    work = RENDERS_DIR / f"{ex_id}_sb"
    sheets = storyboards(dl["path"], str(work), every=1.0 if metrics["duration"] <= 90 else 2.0)
    audio = extract_audio(dl["path"], str(RENDERS_DIR / f"{ex_id}_audio.wav"))
    t(0.6, "Gemini разбирает, чем цепляет пример…")
    res = gemini.study_example(dl["path"], sheets, audio, note, metrics, dl["info"].get("title") or info.get("title", ""),
                               progress=lambda m: t(None, m))
    ex = {"id": ex_id, "url": url, "note": note, "title": dl["info"].get("title") or info.get("title", ""),
          "channel": dl["info"].get("channel") or info.get("channel", ""), "views": dl["info"].get("views") or 0,
          "file": rel_to_work(dl["path"]), "metrics": metrics, "summary": res.get("summary", ""),
          "lessons": res.get("lessons", []), "avoid": res.get("avoid", []), "t": time.time()}
    with _store.lock:
        _store.load().setdefault("examples", []).append(ex)
        _store.save()
    t(0.85, "Обновляю правила стиля…")
    rebuild_guide(task=None)
    t(1.0, f"Пример изучен: {len(ex['lessons'])} правил")
    return ex


# ---------------------------------------------------------------------------
# Оценки своих роликов
# ---------------------------------------------------------------------------
def add_feedback(clip: Dict[str, Any], rating: int, comment: str = "") -> Dict[str, Any]:
    """👍/👎 к ролику вместе с его параметрами — чтобы ИИ видел, что именно понравилось или нет."""
    render = (clip.get("render") or {}).get("params") or {}
    segs = render.get("segments") or []
    fb = {
        "id": new_id("fb_"), "clip_id": clip["id"], "rating": 1 if rating > 0 else -1, "comment": comment.strip()[:500],
        "anime": clip.get("anime_name") or clip.get("anime"), "title": clip.get("title"), "mood": clip.get("mood"),
        "hook": clip.get("hook"), "duration": (clip.get("render") or {}).get("duration"),
        "parts": len(segs), "template": render.get("template"), "transition": render.get("transition"),
        "effects": {k: v for k, v in (render.get("effects") or {}).items() if v},
        "music": render.get("music"), "subtitles": bool(clip.get("subtitles")),
        "removed": [r.get("what", "") for r in clip.get("plan_removed") or []][:6], "t": time.time(),
    }
    with _store.lock:
        data = _store.load()
        data["feedback"] = [f for f in data.get("feedback", []) if f["clip_id"] != clip["id"]] + [fb]
        data["since_rebuild"] = int(data.get("since_rebuild", 0)) + 1
        due = data["since_rebuild"] >= FEEDBACK_BATCH
        _store.save()
    from . import library

    library.update_clip(clip["id"], {"feedback": {"rating": fb["rating"], "comment": fb["comment"]}},
                        note=f"Оценка: {'👍' if fb['rating'] > 0 else '👎'} {fb['comment'][:60]}")
    if due:
        rebuild_async()
    return fb


def list_feedback() -> List[Dict[str, Any]]:
    with _store.lock:
        return list(reversed(_store.load().get("feedback", [])))


def feedback_for(clip_id: str) -> Optional[Dict[str, Any]]:
    return next((f for f in list_feedback() if f["clip_id"] == clip_id), None)


# ---------------------------------------------------------------------------
# Сборка правил
# ---------------------------------------------------------------------------
def rebuild_guide(task=None) -> str:
    """ИИ пересобирает раздел правил из всех примеров и оценок. «Мои правила» не трогает."""
    from . import gemini

    with _store.lock:
        data = _store.load()
        examples = list(data.get("examples", []))[-30:]
        feedback = list(data.get("feedback", []))[-60:]
    if not examples and not feedback:
        return read_guide()
    if task:
        task.update(0.2, "Gemini сводит примеры и оценки в правила…")
    parts = split_guide(read_guide())
    new_ai = gemini.build_style_guide(parts["user"], parts["ai"], examples, feedback,
                                      progress=(lambda m: task.update(None, m)) if task else None)
    text = write_guide(join_guide(parts["user"], new_ai))
    with _store.lock:
        _store.load()["since_rebuild"] = 0
        _store.save()
    if task:
        task.update(1.0, "Правила стиля обновлены")
    return text


def rebuild_async() -> Optional[str]:
    from . import gemini

    if not gemini.available():
        return None
    return tasks.submit("style", "Обучение: обновляю правила стиля", lambda tk: rebuild_guide(tk)).id


def stats() -> Dict[str, Any]:
    with _store.lock:
        data = _store.load()
        fb = data.get("feedback", [])
        return {"examples": len(data.get("examples", [])), "likes": sum(1 for f in fb if f["rating"] > 0),
                "dislikes": sum(1 for f in fb if f["rating"] < 0), "since_rebuild": data.get("since_rebuild", 0),
                "guide_path": str(Path(GUIDE_FILE))}
