"""VideoHook — запуск локального приложения (веб-интерфейс в браузере).

    python main.py               # открыть в браузере
    python main.py --no-browser  # только сервер
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
                    "psutil": "psutil"}


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


def main() -> None:
    parser = argparse.ArgumentParser(description="VideoHook — вирусные аниме-шортсы 9:16")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ensure_dependencies()
    from vh.server import start

    try:
        start(port=args.port, open_browser=not args.no_browser)
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
