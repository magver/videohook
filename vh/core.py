"""Базовая инфраструктура: пути, настройки, JSON-хранилище, FFmpeg, фоновые задачи."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

log = logging.getLogger("videohook")

# ---------------------------------------------------------------------------
# Пути
# ---------------------------------------------------------------------------
if getattr(sys, "frozen", False):
    # PyInstaller: ресурсы внутри _MEIPASS, рабочие данные — рядом с .exe
    RES_DIR = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    APP_DIR = Path(sys.executable).resolve().parent
else:
    RES_DIR = Path(__file__).resolve().parent.parent
    APP_DIR = RES_DIR

WORK_DIR = Path(os.environ.get("VIDEOHOOK_HOME", APP_DIR / "workspace")).resolve()
DATA_DIR = WORK_DIR / "data"
SOURCES_DIR = WORK_DIR / "sources"      # скачанные исходники
RENDERS_DIR = WORK_DIR / "renders"      # локальные рендеры 9:16
JOBS_DIR = WORK_DIR / "antigravity"     # папки задач для Antigravity
PUBLISH_DIR = WORK_DIR / "ready"        # финальные ролики к публикации
TMP_DIR = WORK_DIR / "tmp"

ASSETS_DIR = RES_DIR / "assets"
WEB_DIR = RES_DIR / "web"
MUSIC_DIR = ASSETS_DIR / "music"
FONTS_DIR = ASSETS_DIR / "fonts"
SEED_MOMENTS = RES_DIR / "data" / "moments.json"

SUBPROCESS_FLAGS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def ensure_dirs() -> None:
    for d in (DATA_DIR, SOURCES_DIR, RENDERS_DIR, JOBS_DIR, PUBLISH_DIR, TMP_DIR):
        d.mkdir(parents=True, exist_ok=True)


def slugify(text: str, limit: int = 40) -> str:
    keep = []
    for ch in text.strip():
        if ch.isalnum():
            keep.append(ch)
        elif ch in " -_":
            keep.append("_")
    slug = "".join(keep).strip("_")
    while "__" in slug:
        slug = slug.replace("__", "_")
    return (slug[:limit] or "clip").strip("_")


def new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:10]}"


def rel_to_work(path: str | Path) -> str:
    p = Path(path).resolve()
    try:
        return p.relative_to(WORK_DIR).as_posix()
    except ValueError:
        return str(p)


def resolve_work(rel: str) -> Path:
    """Путь из API → абсолютный путь внутри рабочей папки (защита от выхода наружу)."""
    p = (WORK_DIR / rel.lstrip("/\\")).resolve() if not Path(rel).is_absolute() else Path(rel).resolve()
    if not str(p).startswith(str(WORK_DIR)):
        raise PermissionError("Путь вне рабочей папки")
    return p


# ---------------------------------------------------------------------------
# JSON-хранилище с блокировкой
# ---------------------------------------------------------------------------
class JsonStore:
    def __init__(self, path: Path, default: Any):
        self.path = path
        self.default = default
        self.lock = threading.RLock()
        self._data: Any = None

    def load(self) -> Any:
        with self.lock:
            if self._data is None:
                try:
                    self._data = json.loads(self.path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    self._data = json.loads(json.dumps(self.default))
            return self._data

    def save(self) -> None:
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, self.path)


# ---------------------------------------------------------------------------
# Настройки
# ---------------------------------------------------------------------------
DEFAULT_SETTINGS: Dict[str, Any] = {
    "brand_handle": "@anime_hook",          # подпись канала на ролике
    "telegram": "",                          # опциональная ссылка на Telegram в описании
    "language": "ru",
    # Google AI Pro: ИИ-задачи идут в Antigravity (приоритет), Gemini API — запасной канал
    "ai_via_antigravity": True,              # подбор моментов, анализ видео, описания — через Antigravity
    "gemini_api_key": "",                    # ключ AI Studio (aistudio.google.com/apikey), запасной канал
    "gemini_model": "gemini-flash-latest",   # модель Gemini API
    # Antigravity
    "antigravity_model": "Gemini 3.8 Flash",
    "antigravity_cmd": "",                   # путь к Antigravity.exe для автозапуска (автопоиск, если пусто)
    "antigravity_brain_dir": "",             # ~/.gemini/antigravity/brain (автопоиск, если пусто)
    "antigravity_model_flag": "flash",      # значение --model для agentapi new-conversation
    "antigravity_auto_launch": True,         # запускать Antigravity, если он закрыт
    "use_antigravity": True,                 # False — публиковать локальный рендер без агента
    # Рендер
    "default_template": "cinema",
    "default_music": "auto",
    "music_volume": 0.22,
    "clip_max_seconds": 45,
    "loop_friendly": True,
    # Публикация
    "youtube_client_secret": "",             # путь к client_secret.json (Google Cloud OAuth, desktop)
    "youtube_privacy": "private",
    "tiktok_access_token": "",               # TikTok Content Posting API (scope video.upload)
    "auto_publish": False,
}

_settings_store = JsonStore(DATA_DIR / "settings.json", DEFAULT_SETTINGS)


def get_settings() -> Dict[str, Any]:
    data = _settings_store.load()
    merged = dict(DEFAULT_SETTINGS)
    merged.update(data or {})
    return merged


def update_settings(patch: Dict[str, Any]) -> Dict[str, Any]:
    with _settings_store.lock:
        data = _settings_store.load()
        for k, v in patch.items():
            if k in DEFAULT_SETTINGS:
                data[k] = v
        _settings_store.save()
    return get_settings()


def public_settings() -> Dict[str, Any]:
    """Настройки для UI без секретов в открытом виде."""
    s = get_settings()
    out = dict(s)
    for key in ("gemini_api_key", "tiktok_access_token"):
        val = s.get(key) or ""
        out[key] = ("•" * 8 + val[-4:]) if val else ""
        out[key + "_set"] = bool(val)
    return out


# ---------------------------------------------------------------------------
# FFmpeg
# ---------------------------------------------------------------------------
_ffmpeg_cache: Optional[str] = None


def ffmpeg_bin() -> str:
    global _ffmpeg_cache
    if _ffmpeg_cache:
        return _ffmpeg_cache
    candidates: List[str] = []
    try:
        import imageio_ffmpeg  # type: ignore

        candidates.append(imageio_ffmpeg.get_ffmpeg_exe())
    except Exception:
        pass
    sys_ff = shutil.which("ffmpeg")
    if sys_ff:
        candidates.insert(0, sys_ff)
    for c in candidates:
        if c and Path(c).exists() and _has_filter(c, "ass"):
            _ffmpeg_cache = c
            return c
    if candidates:
        _ffmpeg_cache = candidates[0]
        return candidates[0]
    raise RuntimeError("FFmpeg не найден. Установите ffmpeg или выполните: pip install imageio-ffmpeg")


def _has_filter(binary: str, name: str) -> bool:
    try:
        res = subprocess.run([binary, "-hide_banner", "-filters"], capture_output=True, text=True,
                             timeout=15, creationflags=SUBPROCESS_FLAGS)
        return any(line.split()[1:2] == [name] for line in res.stdout.splitlines() if line.strip())
    except Exception:
        return False


def ffmpeg_dir_for_ytdlp() -> str:
    """yt-dlp ищет бинарник по имени ffmpeg(.exe) — обеспечиваем его наличие."""
    ff = Path(ffmpeg_bin())
    wanted = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    if ff.name == wanted:
        return str(ff)
    link_dir = TMP_DIR / "bin"
    link_dir.mkdir(parents=True, exist_ok=True)
    target = link_dir / wanted
    if not target.exists():
        try:
            shutil.copy2(ff, target)
            target.chmod(0o755)
        except OSError:
            return str(ff)
    return str(target)


def run_ffmpeg(args: List[str], progress: Optional[Callable[[float], None]] = None,
               duration: float = 0.0, cwd: Optional[Path] = None) -> None:
    cmd = [ffmpeg_bin(), "-hide_banner", "-y", *args]
    if progress and duration > 0:
        cmd[3:3] = ["-progress", "pipe:1", "-nostats"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            encoding="utf-8", errors="replace", cwd=str(cwd) if cwd else None,
                            creationflags=SUBPROCESS_FLAGS)
    err_lines: List[str] = []

    def _drain_err():
        for line in proc.stderr:  # type: ignore[union-attr]
            err_lines.append(line)
            if len(err_lines) > 200:
                del err_lines[:100]

    t = threading.Thread(target=_drain_err, daemon=True)
    t.start()
    for line in proc.stdout:  # type: ignore[union-attr]
        if progress and duration > 0 and line.startswith("out_time_us="):
            try:
                us = int(line.split("=", 1)[1])
                progress(max(0.0, min(1.0, us / 1e6 / duration)))
            except ValueError:
                pass
    proc.wait()
    t.join(timeout=2)
    if proc.returncode != 0:
        raise RuntimeError("FFmpeg: " + "".join(err_lines[-25:]).strip())
    if progress:
        progress(1.0)


def probe(path: str | Path) -> Dict[str, Any]:
    """Длительность, размер кадра, fps, наличие звука — через баннер ffmpeg (без ffprobe)."""
    import re

    res = subprocess.run([ffmpeg_bin(), "-hide_banner", "-i", str(path)], capture_output=True, text=True,
                         encoding="utf-8", errors="replace", creationflags=SUBPROCESS_FLAGS)
    txt = res.stderr
    info: Dict[str, Any] = {"duration": 0.0, "width": 0, "height": 0, "fps": 30.0, "has_audio": False}
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", txt)
    if m:
        info["duration"] = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    m = re.search(r"Video:.*?(\d{2,5})x(\d{2,5})", txt)
    if m:
        info["width"], info["height"] = int(m.group(1)), int(m.group(2))
    m = re.search(r"(\d+(?:\.\d+)?) fps", txt)
    if m:
        info["fps"] = float(m.group(1))
    info["has_audio"] = "Audio:" in txt
    return info


# ---------------------------------------------------------------------------
# Фоновые задачи с прогрессом
# ---------------------------------------------------------------------------
class Task:
    def __init__(self, kind: str, title: str):
        self.id = new_id("t_")
        self.kind = kind
        self.title = title
        self.status = "queued"  # queued | running | done | error
        self.progress = 0.0
        self.message = "В очереди"
        self.result: Any = None
        self.error = ""
        self.created = time.time()
        self.updated = self.created

    def update(self, progress: Optional[float] = None, message: Optional[str] = None) -> None:
        if progress is not None:
            self.progress = max(0.0, min(1.0, progress))
        if message is not None:
            self.message = message
        self.updated = time.time()

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id, "kind": self.kind, "title": self.title, "status": self.status,
            "progress": round(self.progress, 3), "message": self.message, "error": self.error,
            "result": self.result, "created": self.created, "updated": self.updated,
        }


class TaskManager:
    def __init__(self, workers: int = 2):
        self.tasks: Dict[str, Task] = {}
        self.lock = threading.Lock()
        self.sem = threading.Semaphore(workers)

    def submit(self, kind: str, title: str, fn: Callable[[Task], Any]) -> Task:
        task = Task(kind, title)
        with self.lock:
            self.tasks[task.id] = task
            # не копим бесконечно
            if len(self.tasks) > 300:
                for tid in sorted(self.tasks, key=lambda k: self.tasks[k].created)[:100]:
                    if self.tasks[tid].status in ("done", "error"):
                        del self.tasks[tid]

        def runner():
            with self.sem:
                task.status = "running"
                task.update(message="Выполняется…")
                try:
                    task.result = fn(task)
                    task.status = "done"
                    task.update(1.0, task.message if task.message != "Выполняется…" else "Готово")
                except Exception as exc:  # noqa: BLE001 — показываем любую ошибку в UI
                    log.error("Task %s failed: %s\n%s", task.title, exc, traceback.format_exc())
                    task.status = "error"
                    task.error = str(exc)[:2000]
                    task.update(message="Ошибка")

        threading.Thread(target=runner, daemon=True, name=f"task-{kind}").start()
        return task

    def get(self, task_id: str) -> Optional[Task]:
        return self.tasks.get(task_id)

    def list(self, active_only: bool = False) -> List[Dict[str, Any]]:
        with self.lock:
            items = list(self.tasks.values())
        if active_only:
            items = [t for t in items if t.status in ("queued", "running") or time.time() - t.updated < 20]
        return [t.as_dict() for t in sorted(items, key=lambda t: t.created, reverse=True)]


tasks = TaskManager()
