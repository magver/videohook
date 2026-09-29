"""Библиотека клипов: единая карточка ролика проходит стадии конвейера."""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from .core import DATA_DIR, JsonStore, new_id

STAGES = [
    ("found", "Найден"),
    ("downloaded", "Скачан"),
    ("rendered", "Смонтирован"),
    ("in_antigravity", "В Antigravity"),
    ("ready", "Готов к публикации"),
    ("published", "Опубликован"),
]
STAGE_ORDER = {k: i for i, (k, _) in enumerate(STAGES)}

_store = JsonStore(DATA_DIR / "library.json", {"clips": []})


def _clips() -> List[Dict[str, Any]]:
    return _store.load().setdefault("clips", [])


def list_clips(stage: Optional[str] = None) -> List[Dict[str, Any]]:
    with _store.lock:
        items = [c for c in _clips() if not stage or c.get("stage") == stage]
        return sorted(items, key=lambda c: c.get("updated", 0), reverse=True)


def get_clip(clip_id: str) -> Optional[Dict[str, Any]]:
    with _store.lock:
        for c in _clips():
            if c["id"] == clip_id:
                return c
    return None


def require_clip(clip_id: str) -> Dict[str, Any]:
    clip = get_clip(clip_id)
    if not clip:
        raise KeyError(f"Клип {clip_id} не найден")
    return clip


def add_clip(data: Dict[str, Any]) -> Dict[str, Any]:
    now = time.time()
    clip = {
        "id": new_id("c_"),
        "created": now,
        "updated": now,
        "stage": "found",
        "anime": "",
        "title": "",
        "hook": "",
        "caption": "",
        "mood": "epic",
        "source_url": "",
        "query": "",
        "segment": None,
        "suggestions": [],
        "publish": {"captions": {}, "status": {}},
        "log": [],
    }
    clip.update({k: v for k, v in data.items() if v is not None})
    with _store.lock:
        # не дублируем один и тот же источник+момент
        for c in _clips():
            if clip.get("source_url") and c.get("source_url") == clip["source_url"] and c.get("title") == clip.get("title"):
                return c
        _clips().append(clip)
        _store.save()
    return clip


def update_clip(clip_id: str, patch: Dict[str, Any], note: Optional[str] = None) -> Dict[str, Any]:
    with _store.lock:
        clip = require_clip(clip_id)
        for k, v in patch.items():
            if isinstance(v, dict) and v and isinstance(clip.get(k), dict) and k in ("publish", "ag", "render"):
                merged = dict(clip[k])
                for kk, vv in v.items():
                    if isinstance(vv, dict) and isinstance(merged.get(kk), dict):
                        merged[kk] = {**merged[kk], **vv}
                    else:
                        merged[kk] = vv
                clip[k] = merged
            else:
                clip[k] = v
        clip["updated"] = time.time()
        if note:
            clip.setdefault("log", []).append({"t": clip["updated"], "msg": note})
            clip["log"] = clip["log"][-30:]
        _store.save()
        return clip


def advance_stage(clip_id: str, stage: str, note: Optional[str] = None) -> Dict[str, Any]:
    clip = require_clip(clip_id)
    prev = clip.get("stage", "found")   # запоминаем до update_clip: он меняет тот же объект
    if STAGE_ORDER.get(stage, 0) >= STAGE_ORDER.get(prev, 0):
        updated = update_clip(clip_id, {"stage": stage}, note)
        if stage == "published" and prev != "published":
            drop_chats(clip_id)   # 1 клип = 1 чат: после публикации чат в Antigravity больше не нужен
        return updated
    return update_clip(clip_id, {}, note) if note else clip


def delete_clip(clip_id: str) -> None:
    with _store.lock:
        data = _store.load()
        data["clips"] = [c for c in data.get("clips", []) if c["id"] != clip_id]
        _store.save()
    drop_chats(clip_id)


# ---------------------------------------------------------------------------
# Чат клипа в Antigravity: все ИИ-задачи клипа идут в один диалог
# ---------------------------------------------------------------------------
def clip_chat(clip: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": clip.get("ai_chat_id", ""), "owner": clip["id"],
            "title": f"{(clip.get('anime') or '')[:30]} — {(clip.get('title') or '')[:30]}"}


def save_chat(clip_id: str, chat: Dict[str, Any]) -> None:
    clip = get_clip(clip_id)
    if clip and chat.get("id") and chat["id"] != clip.get("ai_chat_id"):
        update_clip(clip_id, {"ai_chat_id": chat["id"]})


def drop_chats(clip_id: str) -> None:
    """Удаляет диалоги клипа в Antigravity в фоне (не задерживая публикацию)."""
    import threading

    from . import agbridge

    def job():
        try:
            n = agbridge.delete_owner_chats(clip_id)
            if n and get_clip(clip_id):
                update_clip(clip_id, {"ai_chat_id": ""}, note=f"Antigravity: удалено чатов — {n}")
        except Exception:  # noqa: BLE001 — удаление чата не должно ломать публикацию
            pass

    threading.Thread(target=job, daemon=True, name="ag-cleanup").start()


def stage_counts() -> Dict[str, int]:
    counts = {k: 0 for k, _ in STAGES}
    for c in list_clips():
        counts[c.get("stage", "found")] = counts.get(c.get("stage", "found"), 0) + 1
    return counts
