"""Настольное окно VideoHook: интерфейс в собственном окне Windows (Edge WebView2 через pywebview).

Локальный сервер работает внутри того же процесса; закрытие окна завершает программу.
Если WebView2/pywebview недоступны, интерфейс открывается в браузере, как раньше.
"""

from __future__ import annotations

import logging
import threading
import webbrowser

from . import __version__
from .core import WORK_DIR, tasks

log = logging.getLogger("videohook.desktop")


def _active_tasks() -> int:
    return sum(1 for t in tasks.list() if t["status"] in ("queued", "running"))


def run(port: int) -> None:
    from .server import start

    srv, url = start(port=port, open_browser=False, block=False)
    try:
        import webview
    except ImportError as exc:
        log.warning("pywebview недоступен (%s) — открываю интерфейс в браузере", exc)
        _browser_fallback(srv, url)
        return

    window = webview.create_window(
        f"VideoHook {__version__}", url, width=1440, height=920, min_size=(1100, 700),
        background_color="#0B0B10", text_select=True,
    )

    def on_closing():
        n = _active_tasks()
        if not n:
            return True
        return window.create_confirmation_dialog(
            "VideoHook", f"Выполняется задач: {n} (скачивание, монтаж или ИИ). Закрыть программу и прервать их?")

    window.events.closing += on_closing
    try:
        webview.start(gui="edgechromium", private_mode=False, storage_path=str(WORK_DIR / "webview"))
    except Exception as exc:  # noqa: BLE001 — нет WebView2 Runtime и т.п.
        log.warning("Окно приложения не открылось (%s) — открываю интерфейс в браузере", exc)
        _browser_fallback(srv, url)
        return
    srv.shutdown()


def _browser_fallback(srv, url: str) -> None:
    webbrowser.open(url)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        srv.shutdown()
