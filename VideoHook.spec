# -*- mode: python ; coding: utf-8 -*-
# Сборка: pyinstaller VideoHook.spec  →  dist/VideoHook.exe
from PyInstaller.utils.hooks import collect_all

datas = [("assets", "assets"), ("web", "web"), ("data/moments.json", "data")]
binaries = []
hiddenimports = ["vh", "vh.server", "vh.pipeline", "vh.moments", "vh.discovery", "vh.gemini",
                 "vh.render", "vh.antigravity", "vh.publish", "vh.library", "vh.core"]
for pkg in ["imageio_ffmpeg", "yt_dlp", "certifi", "cv2", "requests"]:
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(["main.py"], pathex=["."], binaries=binaries, datas=datas, hiddenimports=hiddenimports,
             hookspath=[], runtime_hooks=[], excludes=["tkinter", "customtkinter", "pygame", "moviepy"],
             noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="VideoHook", debug=False, strip=False, upx=True,
          console=True, disable_windowed_traceback=False)
