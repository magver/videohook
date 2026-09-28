import os
import sys
import tempfile
from pathlib import Path

import pytest

# изолированная рабочая папка — до первого импорта vh
_HOME = tempfile.mkdtemp(prefix="vh_test_")
os.environ["VIDEOHOOK_HOME"] = _HOME
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(scope="session")
def sample_video():
    from vh.core import SOURCES_DIR, ensure_dirs, run_ffmpeg

    ensure_dirs()
    out = SOURCES_DIR / "sample_src.mp4"
    if not out.exists():
        # 20 c: цветные полосы + меняющийся шум для «динамики», звук с громким участком 8–12 c
        run_ffmpeg([
            "-f", "lavfi", "-i", "testsrc2=s=1280x720:r=30:d=20",
            "-f", "lavfi", "-i", "sine=f=220:d=20",
            "-filter_complex", "[1:a]volume='if(between(t,8,12),1.0,0.1)':eval=frame[a]",
            "-map", "0:v", "-map", "[a]", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(out),
        ])
    return out


@pytest.fixture(autouse=True)
def no_real_ai(monkeypatch):
    """Тесты не должны обращаться к запущенному Antigravity и Gemini API на машине разработчика."""
    from vh import agbridge, gemini

    monkeypatch.setattr(agbridge, "scan", lambda: [])
    monkeypatch.setattr(agbridge, "installed_exe", lambda: "")
    monkeypatch.setattr(gemini, "_key", lambda: "")
