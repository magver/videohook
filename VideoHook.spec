# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = [('assets', 'assets')]
binaries = []
hiddenimports = [
    'pygame',
    'pygame.mixer',
    'anime_detector',
    'social_downloader',
    'watermark_remover',
    'antigravity_bridge',
    'clipper',
    'editor_window',
    'hotkeys',
    'smart_crop',
    'trends',
    'viral_tg',
]

packages_to_collect = [
    'customtkinter',
    'imageio_ffmpeg',
    'yt_dlp',
    'darkdetect',
    'certifi',
    'cv2',
    'PIL',
    'requests',
    'moviepy',
]

for pkg in packages_to_collect:
    tmp_ret = collect_all(pkg)
    datas += tmp_ret[0]
    binaries += tmp_ret[1]
    hiddenimports += tmp_ret[2]


a = Analysis(
    ['main.py'],
    pathex=['D:/videohook'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='VideoHook',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
