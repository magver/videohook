"""VideoHook — запуск настольного приложения.

    python main.py               # окно программы
    python main.py --browser     # интерфейс в браузере
    python main.py --no-browser  # только сервер (для отладки и тестов)
    python main.py --port 9000
"""

import argparse
import logging
import os
import subprocess
import sys

# Windows: не мигать консолью ffmpeg/yt-dlp в собранном .exe
if os.name == "nt":
    _OrigPopen = subprocess.Popen

    class _SilentPopen(_OrigPopen):  # type: ignore[misc, valid-type]
        def __init__(self, *args, **kwargs):
            kwargs["creationflags"] = kwargs.get("creationflags", 0) | subprocess.CREATE_NO_WINDOW
            super().__init__(*args, **kwargs)

    subprocess.Popen = _SilentPopen  # type: ignore[misc]


REQUIRED_MODULES = {"yt_dlp": "yt-dlp", "requests": "requests", "imageio_ffmpeg": "imageio-ffmpeg",
                    "psutil": "psutil", "webview": "pywebview"}


def ensure_dependencies() -> None:
    """Без yt-dlp поиск источников молча ничего не находит — доустанавливаем недостающее при старте."""
    if getattr(sys, "frozen", False):
        return
    import importlib.util

    missing = [m for m in REQUIRED_MODULES if importlib.util.find_spec(m) is None]
    if not missing:
        return
    req = os.path.join(os.path.dirname(os.path.abspath(__file__)), "requirements.txt")
    print(f"Не хватает модулей: {', '.join(missing)} — устанавливаю зависимости…", flush=True)
    res = subprocess.run([sys.executable, "-m", "pip", "install", "-r", req])
    if res.returncode != 0:
        print("Не удалось установить зависимости. Выполните вручную: "
              f"{sys.executable} -m pip install -r requirements.txt", flush=True)


def setup_logging(debug: bool) -> None:
    """Лог в файл рабочей папки (у оконной сборки нет консоли) и в консоль, если она есть."""
    from logging.handlers import RotatingFileHandler

    from vh.core import DATA_DIR

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    handlers: list = [RotatingFileHandler(DATA_DIR / "videohook.log", maxBytes=2_000_000, backupCount=2,
                                          encoding="utf-8")]
    if sys.stderr:
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.DEBUG if debug else logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def main() -> None:
    parser = argparse.ArgumentParser(description="VideoHook — вирусные аниме-шортсы 9:16")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--browser", action="store_true", help="открыть интерфейс в браузере вместо окна")
    parser.add_argument("--no-browser", action="store_true", help="только сервер, без окна")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()
    ensure_dependencies()
    setup_logging(args.debug)

    try:
        if args.browser or args.no_browser:
            from vh.server import start

            start(port=args.port, open_browser=args.browser)
        else:
            from vh.desktop import run

            run(args.port)
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
