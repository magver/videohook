import os
import subprocess
import sys

# Globally suppress any command prompt / terminal popups on Windows
if os.name == "nt":
    _OrigPopen = subprocess.Popen

    class _SilentPopen(_OrigPopen):
        def __init__(self, *args, **kwargs):
            kwargs["creationflags"] = kwargs.get("creationflags", 0) | subprocess.CREATE_NO_WINDOW
            super().__init__(*args, **kwargs)

    subprocess.Popen = _SilentPopen

from gui import run_gui


def main():
    try:
        run_gui()
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
