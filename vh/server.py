"""Локальный HTTP-сервер: REST API + веб-интерфейс. Слушает только 127.0.0.1."""

from __future__ import annotations

import json
import logging
import mimetypes
import re
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, Tuple

from . import __version__, agbridge, antigravity, gemini, library, moments, pipeline, publish, style
from .core import (WEB_DIR, WORK_DIR, ensure_dirs, ffmpeg_bin, public_settings, resolve_work, tasks,
                   update_settings)
from .render import MUSIC_TRACKS, TEMPLATES, TRANSITIONS

log = logging.getLogger("videohook.server")
PORT = 8765


def health() -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    try:
        out["ffmpeg"] = {"ok": True, "path": ffmpeg_bin()}
    except Exception as exc:  # noqa: BLE001
        out["ffmpeg"] = {"ok": False, "error": str(exc)}
    try:
        import yt_dlp

        out["ytdlp"] = {"ok": True, "version": yt_dlp.version.__version__}
    except Exception as exc:  # noqa: BLE001
        out["ytdlp"] = {"ok": False, "error": str(exc)}
    out["gemini"] = gemini.status()
    out["antigravity"] = antigravity.status()
    out["youtube"] = publish.youtube_status()
    out["tiktok"] = publish.tiktok_status()
    return out


_health_cache: Dict[str, Any] = {}


def state() -> Dict[str, Any]:
    if not _health_cache:
        _health_cache.update(health())
    return {
        "version": __version__,
        "stages": library.STAGES,
        "counts": library.stage_counts(),
        "moments": moments.stats(),
        "health": _health_cache,
        "settings": public_settings(),
        "templates": TEMPLATES,
        "transitions": TRANSITIONS,
        "music": MUSIC_TRACKS,
        "tasks": tasks.list(active_only=True)[:20],
        "work_dir": str(WORK_DIR),
    }


def _task(kind: str, title: str, fn: Callable, clip_id: str = "") -> Dict[str, Any]:
    return {"task": tasks.submit(kind, title, fn, clip_id).as_dict()}


# ---------------------------------------------------------------------------
# Маршруты
# ---------------------------------------------------------------------------
def api_get(path: str, q: Dict[str, str], handler: "Handler") -> Any:
    if path == "/api/state":
        return state()
    if path == "/api/health":
        _health_cache.clear()
        _health_cache.update(health())
        return _health_cache
    if path == "/api/tasks":
        return tasks.list()
    if path.startswith("/api/task/"):
        t = tasks.get(path.rsplit("/", 1)[1])
        return t.as_dict() if t else {"error": "not found"}
    if path == "/api/trending":
        return moments.trending(q.get("sort", "TRENDING_DESC"), int(q.get("limit", 30)), q.get("fresh") != "1")
    if path == "/api/anime/search":
        return moments.search_anime(q.get("q", ""))
    if path == "/api/catalog":
        return moments.catalog(q.get("q", ""), q.get("used", "1") == "1")
    if path == "/api/clips":
        return library.list_clips(q.get("stage") or None)
    if path.startswith("/api/clip/"):
        return library.require_clip(path.split("/")[3])
    if path == "/api/settings":
        return public_settings()
    if path == "/api/style":
        return {"guide": style.read_guide(), "examples": style.list_examples(), "feedback": style.list_feedback(),
                "stats": style.stats()}
    if path == "/api/youtube/connect":
        url = publish.youtube_auth_url(f"http://127.0.0.1:{handler.server.server_address[1]}/oauth/youtube")
        handler.redirect(url)
        return None
    raise KeyError(path)


def api_post(path: str, body: Dict[str, Any]) -> Any:
    # --- моменты
    if path == "/api/anime/register":
        key = moments.register_anime(body)
        return {"key": key}
    if path == "/api/moments/refill":
        tid = moments.ensure_fresh(body["anime_key"], force=True)
        return {"task_id": tid}
    if path == "/api/moments/add":
        n = moments.add_moments(body["anime_key"], [body["moment"]], source="manual")
        return {"added": n}
    if path == "/api/moments/reset":
        moments.reset_usage(body["moment_id"])
        return {"ok": True}
    if path == "/api/moments/rate":
        return _task("rate", "Шансы на успех", lambda tk: moments.rate_anime(body["anime_key"], tk,
                                                                              bool(body.get("only_missing"))))
    # --- обучение стилю
    if path == "/api/style/example":
        url = body["url"].strip()
        return _task("study", "Обучение: разбираю пример", lambda tk: style.study_example(url, body.get("note", ""), tk))
    if path == "/api/style/example/delete":
        style.delete_example(body["id"])
        return {"ok": True}
    if path == "/api/style/guide":
        return {"guide": style.write_guide(body["guide"])}
    if path == "/api/style/rebuild":
        return _task("style", "Обучение: обновляю правила стиля", lambda tk: style.rebuild_guide(tk))
    if path == "/api/moments/maintain":
        return {"tasks": moments.maintain_all()}

    # --- создание клипов
    if path in ("/api/clip/from_moment", "/api/clip/from_url"):
        if path.endswith("from_moment"):
            clip = pipeline.create_from_moment(body["anime_key"], body["moment_id"])
        else:
            clip = pipeline.create_from_url(body["url"].strip(), body.get("title", ""))
        run = body.get("run", "source")
        if run == "full":
            t = tasks.submit("pipeline", f"Конвейер: {clip['title'][:40]}", lambda tk: pipeline.full_pipeline(clip["id"], tk),
                             clip["id"])
        elif run == "source":
            t = tasks.submit("source", f"Источник: {clip['title'][:40]}", lambda tk: pipeline.fetch_source(clip["id"], tk),
                             clip["id"])
        else:
            t = None
        return {"clip": clip, "task": t.as_dict() if t else None}

    if path == "/api/autopilot":
        count = max(1, min(10, int(body.get("count", 3))))
        return _task("autopilot", f"Автопилот ×{count}",
                     lambda tk: pipeline.autopilot(count, body.get("anime_keys") or None, tk))

    if path == "/api/settings":
        update_settings(body)
        _health_cache.clear()
        return public_settings()

    m = re.match(r"^/api/clip/([\w]+)/(\w+)(?:/(\w+))?$", path)
    if m:
        cid, action, sub = m.group(1), m.group(2), m.group(3)
        clip = library.require_clip(cid)
        name = clip.get("title", "")[:40]
        if action == "update":
            allowed = {"hook", "caption", "commentary", "title", "mood", "segment", "music", "key_lines", "anime",
                       "subtitles", "transition", "accents"}
            return library.update_clip(cid, {k: v for k, v in body.items() if k in allowed})
        if action == "source":
            return _task("source", f"Источник: {name}", lambda tk: pipeline.fetch_source(cid, tk, body.get("url", "")), cid)
        if action == "analyze":
            return _task("analyze", f"Gemini-анализ: {name}", lambda tk: pipeline.ai_analyze(cid, tk), cid)
        if action == "render":
            return _task("render", f"Монтаж: {name}", lambda tk: pipeline.make_render(cid, dict(body), tk), cid)
        if action == "antigravity":
            if sub == "check":
                return antigravity.check_job(clip) or library.require_clip(cid)
            return _task("antigravity", f"Antigravity: {name}", lambda tk: pipeline.send_to_antigravity(cid, tk), cid)
        if action == "skip_ag":
            antigravity.use_draft_as_final(cid)
            return pipeline.finalize(cid)
        if action == "full":
            return _task("pipeline", f"Конвейер: {name}", lambda tk: pipeline.full_pipeline(cid, tk), cid)
        if action == "captions":
            if body.get("captions"):
                return library.update_clip(cid, {"publish": {"captions": body["captions"]}})
            return publish.prepare_captions(cid, body.get("use_ai", True))
        if action == "publish":
            platform = body["platform"]
            if body.get("mode") == "assisted":
                return publish.assisted(cid, platform)
            if platform == "youtube":
                return _task("publish", f"YouTube: {name}", lambda tk: publish.youtube_upload(cid, tk), cid)
            if platform == "tiktok":
                return _task("publish", f"TikTok: {name}", lambda tk: publish.tiktok_upload(cid, tk), cid)
            return publish.assisted(cid, platform)
        if action == "feedback":
            return style.add_feedback(clip, int(body.get("rating", 1)), body.get("comment", ""))
        if action == "mark_published":
            return publish.mark_published(cid, body["platform"], body.get("url", ""))
        if action == "open":
            target = {"source": clip.get("source_file"), "render": (clip.get("render") or {}).get("file"),
                      "final": clip.get("final_file"), "job": (clip.get("ag") or {}).get("dir")}.get(body.get("what", ""))
            if not target:
                raise FileNotFoundError("Файл ещё не создан")
            p = resolve_work(target)
            publish.reveal_file(p / "BRIEF.md" if p.is_dir() else p)
            return {"ok": True, "path": str(p)}
        if action == "delete":
            library.delete_clip(cid)
            return {"ok": True}
    raise KeyError(path)


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    server_version = f"VideoHook/{__version__}"

    def log_message(self, fmt: str, *args: Any) -> None:  # тихий лог
        log.debug(fmt, *args)

    def _json(self, payload: Any, status: int = 200) -> None:
        raw = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def redirect(self, url: str) -> None:
        self.send_response(302)
        self.send_header("Location", url)
        self.end_headers()

    def _check_origin(self) -> bool:
        """Защита от запросов с чужих сайтов (CSRF) к локальному API."""
        origin = self.headers.get("Origin") or ""
        if not origin:
            return True
        host = urllib.parse.urlparse(origin).hostname or ""
        return host in ("127.0.0.1", "localhost")

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        q = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
        try:
            if path.startswith("/api/"):
                res = api_get(path, q, self)
                if res is not None:
                    self._json(res)
                return
            if path == "/oauth/youtube":
                publish.youtube_finish_auth(q.get("code", ""), q.get("state", ""))
                _health_cache.clear()
                self._static_html("<h2>YouTube подключён ✅</h2><p>Можно закрыть вкладку и вернуться в VideoHook.</p>")
                return
            if path.startswith("/media/"):
                self._serve_file(resolve_work(urllib.parse.unquote(path[len("/media/"):])))
                return
            self._serve_static(path)
        except KeyError as exc:
            self._json({"error": f"Не найдено: {exc}"}, 404)
        except PermissionError as exc:
            self._json({"error": str(exc)}, 403)
        except Exception as exc:  # noqa: BLE001
            log.exception("GET %s", path)
            self._json({"error": str(exc)}, 500)

    def do_POST(self) -> None:  # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        if not self._check_origin():
            self._json({"error": "forbidden origin"}, 403)
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length).decode("utf-8") or "{}") if length else {}
            self._json(api_post(path, body))
        except KeyError as exc:
            self._json({"error": f"Не найдено: {exc}"}, 404)
        except Exception as exc:  # noqa: BLE001
            log.exception("POST %s", path)
            self._json({"error": str(exc)}, 500)

    def _static_html(self, html: str) -> None:
        raw = f"<!doctype html><meta charset=utf-8><body style='font-family:sans-serif;background:#0b0b10;color:#eee;padding:40px'>{html}".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _serve_static(self, path: str) -> None:
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        f = (WEB_DIR / rel).resolve()
        if not str(f).startswith(str(WEB_DIR.resolve())) or not f.is_file():
            self.send_error(404)
            return
        raw = f.read_bytes()
        ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript",):
            ctype += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _serve_file(self, f: Path) -> None:
        if not f.is_file():
            self.send_error(404)
            return
        size = f.stat().st_size
        start, end, status = 0, size - 1, 200
        rng = self.headers.get("Range")
        if rng:
            m = re.match(r"bytes=(\d*)-(\d*)", rng)
            if m:
                if m.group(1):
                    start = int(m.group(1))
                    if m.group(2):
                        end = min(size - 1, int(m.group(2)))
                elif m.group(2):
                    start = max(0, size - int(m.group(2)))
                status = 206
        length = end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", mimetypes.guess_type(f.name)[0] or "application/octet-stream")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        with open(f, "rb") as fh:
            fh.seek(start)
            remaining = length
            try:
                while remaining > 0:
                    buf = fh.read(min(256 * 1024, remaining))
                    if not buf:
                        break
                    self.wfile.write(buf)
                    remaining -= len(buf)
            except (ConnectionResetError, BrokenPipeError):
                pass


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def make_server(port: int = PORT) -> Tuple[Server, int]:
    for p in range(port, port + 20):
        try:
            return Server(("127.0.0.1", p), Handler), p
        except OSError:
            continue
    raise RuntimeError("Нет свободного порта")


def start(port: int = PORT, open_browser: bool = True, block: bool = True) -> Tuple[Server, str]:
    ensure_dirs()
    watcher = antigravity.Watcher()
    watcher.on_ready = pipeline.on_ready
    watcher.start()
    # фоновая проверка запаса моментов по трендам
    threading.Timer(3.0, lambda: _safe(moments.maintain_all)).start()
    # разовые диалоги Antigravity, которые не удалились сразу (Antigravity был закрыт и т.п.)
    threading.Timer(20.0, lambda: _safe(agbridge.cleanup_stale)).start()
    srv, real_port = make_server(port)
    url = f"http://127.0.0.1:{real_port}"
    print(f"VideoHook {__version__}: {url}  (рабочая папка: {WORK_DIR})", flush=True)
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    if block:
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            srv.server_close()
    else:
        threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, url


def _safe(fn: Callable) -> None:
    try:
        fn()
    except Exception as exc:  # noqa: BLE001
        log.warning("%s: %s", getattr(fn, "__name__", "bg"), exc)

