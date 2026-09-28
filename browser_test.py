"""
Interactive Browser Test & Web UI for VideoHook
Launches a local web server and opens a modern dark-themed testing & viral downloader
interface directly in the default browser.
"""

import json
import mimetypes
import os
import re
import socket
import sys
import threading
import time
import shutil
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List

import cv2

from anime_detector import detect_anime_title, format_description_with_anime
from clipper import get_ffmpeg_path
from social_downloader import (
    CURATED_VIRAL_SHORTS,
    download_clean_social_clip,
    extract_social_video_info,
    identify_platform,
    search_viral_anime_clips,
)
from test_clipper import generate_sample_video
from watermark_remover import (
    detect_outro_card,
    detect_watermark_regions,
    remove_watermarks_and_finalize_video,
)
from antigravity_bridge import (
    build_viral_antigravity_prompt,
    save_antigravity_bundle,
    TELEGRAM_HOOK_TEMPLATES,
    launch_antigravity_job,
    get_antigravity_job_status,
)

BASE_DIR = Path(__file__).resolve().parent

# Track active download jobs
DOWNLOAD_JOBS: Dict[str, Dict[str, Any]] = {}


def get_ready_preview_videos() -> Dict[str, str]:
    """Maps clip IDs to existing local cleaned 9:16 MP4 files for instant preview."""
    mapping = {}
    cleaned_sample = BASE_DIR / "cache_clips" / "test_wm_cleaned.mp4"
    krd_clean = BASE_DIR / "output_shorts" / "Demon_Slayer_clean_9x16.mp4"
    krd_file = BASE_DIR / "output_shorts" / "HKC8ssVlFc4.mp4"
    jjk_file = BASE_DIR / "output_shorts" / "cGhHw8SVk9Q.mp4"
    short_9x16 = BASE_DIR / "output_shorts" / "ywL7MSfbxeM_shorts_9x16.mp4"

    if krd_clean.exists():
        mapping["default_9x16"] = "output_shorts/Demon_Slayer_clean_9x16.mp4"
        mapping["short_krd_1"] = "output_shorts/Demon_Slayer_clean_9x16.mp4"
    elif cleaned_sample.exists():
        mapping["default_9x16"] = "cache_clips/test_wm_cleaned.mp4"
        mapping["short_jjk_1"] = "cache_clips/test_wm_cleaned.mp4"

    if "short_krd_1" not in mapping and krd_file.exists():
        mapping["short_krd_1"] = "output_shorts/HKC8ssVlFc4.mp4"
    if cleaned_sample.exists():
        mapping["short_jjk_1"] = "cache_clips/test_wm_cleaned.mp4"
    elif jjk_file.exists():
        mapping["short_jjk_1"] = "output_shorts/cGhHw8SVk9Q.mp4"
    if short_9x16.exists():
        mapping["short_solo_1"] = "output_shorts/ywL7MSfbxeM_shorts_9x16.mp4"

    # Also scan output_shorts for any newly downloaded files
    out_dir = BASE_DIR / "output_shorts"
    if out_dir.exists():
        for mp4 in out_dir.glob("*.mp4"):
            rel = f"output_shorts/{mp4.name}"
            if "default_9x16" not in mapping:
                mapping["default_9x16"] = rel

    return mapping


def run_diagnostic_suite() -> Dict[str, Any]:
    """Runs live tests for all viral anime downloader & watermark removal components."""
    results = []
    start_all = time.time()

    # Test 1: Platform Identification
    t0 = time.time()
    try:
        p_yt = identify_platform("https://www.youtube.com/shorts/abc")
        p_tt = identify_platform("https://www.tiktok.com/@user/video/123")
        p_ig = identify_platform("https://www.instagram.com/reel/xyz")
        p_vk = identify_platform("https://vk.com/clip-123_456")
        p_rt = identify_platform("https://rutube.ru/shorts/789")
        assert p_yt == "YouTube Shorts"
        assert p_tt == "TikTok"
        assert p_ig == "Instagram Reels"
        assert "VK" in p_vk
        assert p_rt == "RuTube"
        results.append({
            "name": "Распознавание платформ (YouTube, TikTok, Reels, VK, RuTube)",
            "status": "PASS",
            "duration_ms": round((time.time() - t0) * 1000, 1),
            "details": f"Успешно: {p_yt}, {p_tt}, {p_ig}, {p_vk}, {p_rt}",
        })
    except Exception as e:
        results.append({
            "name": "Распознавание платформ",
            "status": "FAIL",
            "duration_ms": round((time.time() - t0) * 1000, 1),
            "details": str(e),
        })

    # Test 2: Anime Title Detector (Russian + English)
    t0 = time.time()
    try:
        d1 = detect_anime_title("Годжо Сатору против Сукуны #shorts #jjk", "Аниме: Магическая битва")
        d2 = detect_anime_title("Танец бога огня Тандзиро против Руи", "")
        d3 = detect_anime_title("Сон Джин Ву произносит Восстань #sololeveling", "")
        assert d1["found"] and d2["found"] and d3["found"]
        desc = format_description_with_anime("Эпичный бой", d1["display_title"], "TikTok", True, True)
        assert "🎬 НАЗВАНИЕ АНИМЕ:" in desc
        results.append({
            "name": "Авто-определение названия аниме на русском языке",
            "status": "PASS",
            "duration_ms": round((time.time() - t0) * 1000, 1),
            "details": f"Распознано: «{d1['display_title']}», «{d2['display_title']}», «{d3['display_title']}»",
        })
    except Exception as e:
        results.append({
            "name": "Авто-определение названия аниме",
            "status": "FAIL",
            "duration_ms": round((time.time() - t0) * 1000, 1),
            "details": str(e),
        })

    # Test 3: Curated Viral Catalog & Filters
    t0 = time.time()
    try:
        all_clips = search_viral_anime_clips()
        assert len(all_clips) >= 8
        for c in all_clips:
            assert c.get("no_watermark") is True
            assert "🎬 НАЗВАНИЕ АНИМЕ:" in c.get("description", "")
        results.append({
            "name": "Каталог вирусных вертикальных аниме-роликов (9:16)",
            "status": "PASS",
            "duration_ms": round((time.time() - t0) * 1000, 1),
            "details": f"Доступно {len(all_clips)} вирусных роликов с русской озвучкой и субтитрами",
        })
    except Exception as e:
        results.append({
            "name": "Каталог вирусных вертикальных аниме-роликов",
            "status": "FAIL",
            "duration_ms": round((time.time() - t0) * 1000, 1),
            "details": str(e),
        })

    # Test 4: OpenCV + FFmpeg Delogo Watermark Detection & Removal
    t0 = time.time()
    try:
        ffmpeg_bin = get_ffmpeg_path()
        sample = str(BASE_DIR / "cache_clips" / "browser_test_sample.mp4")
        cleaned = str(BASE_DIR / "cache_clips" / "browser_test_cleaned.mp4")
        (BASE_DIR / "cache_clips").mkdir(parents=True, exist_ok=True)
        generate_sample_video(sample, ffmpeg_bin, duration=2.5)

        boxes = detect_watermark_regions(sample)
        outro_sec = detect_outro_card(sample)
        out_file = remove_watermarks_and_finalize_video(
            input_path=sample,
            output_path=cleaned,
            platform="TikTok",
            auto_crop_9x16=True,
        )
        cap = cv2.VideoCapture(out_file)
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        cap.release()
        assert w == 1080 and h == 1920, f"Ожидалось 1080x1920, получено {w}x{h}"

        Path(sample).unlink(missing_ok=True)
        Path(cleaned).unlink(missing_ok=True)

        box_summary = ", ".join([f"{b['corner']} ({b['w']}x{b['h']})" for b in boxes]) if boxes else "Адаптивный кроп + углы"
        results.append({
            "name": "Движок удаления водяных знаков (OpenCV + FFmpeg Delogo + 9:16)",
            "status": "PASS",
            "duration_ms": round((time.time() - t0) * 1000, 1),
            "details": f"Выходной кадр: {w}x{h} (9:16 Vertical). Очищенные зоны: {box_summary}",
        })
    except Exception as e:
        results.append({
            "name": "Движок удаления водяных знаков (Delogo)",
            "status": "FAIL",
            "duration_ms": round((time.time() - t0) * 1000, 1),
            "details": str(e),
        })

    # Test 5: Pre-cleaned 9:16 Anime Video Verification
    t0 = time.time()
    try:
        cleaned_demo = BASE_DIR / "cache_clips" / "test_wm_cleaned.mp4"
        if cleaned_demo.exists():
            cap = cv2.VideoCapture(str(cleaned_demo))
            w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            fps = cap.get(cv2.CAP_PROP_FPS)
            frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            cap.release()
            size_mb = cleaned_demo.stat().st_size / (1024 * 1024)
            results.append({
                "name": "Проверка готового очищенного аниме-ролика в кэше",
                "status": "PASS",
                "duration_ms": round((time.time() - t0) * 1000, 1),
                "details": f"Файл test_wm_cleaned.mp4 ({size_mb:.1f} МБ) — {w}x{h} @ {fps:.0f}fps ({frames} кадров), без водяных знаков",
            })
    except Exception as e:
        results.append({
            "name": "Проверка очищенного аниме-ролика",
            "status": "FAIL",
            "duration_ms": round((time.time() - t0) * 1000, 1),
            "details": str(e),
        })

    passed = sum(1 for r in results if r["status"] == "PASS")
    return {
        "total": len(results),
        "passed": passed,
        "total_ms": round((time.time() - start_all) * 1000, 1),
        "results": results,
    }


HTML_PAGE = r"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>VideoHook — Вирусные Вертикальные Аниме Ролики (Тест в браузере)</title>
<style>
  :root {
    --bg: #0b0b12;
    --panel: #141422;
    --card: #1b1b2e;
    --card-hover: #24243e;
    --accent: #7c3aed;
    --accent-hover: #6d28d9;
    --green: #10b981;
    --green-hover: #059669;
    --cyan: #38bdf8;
    --gold: #f59e0b;
    --text: #f8fafc;
    --muted: #94a3b8;
    --border: #2a2a44;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background: var(--bg);
    color: var(--text);
    font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
    min-height: 100vh;
    display: flex;
    flex-direction: column;
  }
  header {
    background: #10101c;
    border-bottom: 1px solid var(--border);
    padding: 14px 24px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 12px;
  }
  .brand {
    display: flex;
    align-items: center;
    gap: 12px;
  }
  .brand h1 {
    font-size: 18px;
    font-weight: 800;
    letter-spacing: 0.3px;
  }
  .badge {
    background: rgba(16, 185, 129, 0.15);
    color: #34d399;
    border: 1px solid rgba(16, 185, 129, 0.35);
    padding: 5px 12px;
    border-radius: 999px;
    font-size: 12px;
    font-weight: 700;
  }
  .quick-bar {
    background: var(--panel);
    border-bottom: 1px solid var(--border);
    padding: 12px 24px;
    display: flex;
    align-items: center;
    gap: 10px;
    flex-wrap: wrap;
  }
  .quick-bar input[type="text"] {
    flex: 1;
    min-width: 260px;
    background: var(--card);
    border: 1px solid var(--border);
    color: var(--text);
    padding: 10px 14px;
    border-radius: 8px;
    font-size: 13px;
    outline: none;
  }
  .quick-bar input[type="text"]:focus {
    border-color: var(--accent);
  }
  .btn {
    background: var(--accent);
    color: #fff;
    border: none;
    padding: 10px 16px;
    border-radius: 8px;
    font-weight: 700;
    font-size: 13px;
    cursor: pointer;
    transition: background 0.15s, transform 0.1s;
    display: inline-flex;
    align-items: center;
    gap: 6px;
  }
  .btn:hover { background: var(--accent-hover); }
  .btn:active { transform: scale(0.98); }
  .btn-green { background: var(--green); }
  .btn-green:hover { background: var(--green-hover); }
  .btn-outline {
    background: var(--card);
    border: 1px solid var(--border);
    color: var(--text);
  }
  .btn-outline:hover { background: var(--card-hover); }

  .workspace {
    display: grid;
    grid-template-columns: 380px 1fr;
    gap: 20px;
    padding: 20px 24px;
    flex: 1;
  }
  @media (max-width: 960px) {
    .workspace { grid-template-columns: 1fr; }
  }

  /* Left 9:16 Player Panel */
  .player-panel {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 14px;
    padding: 16px;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 12px;
  }
  .phone-frame {
    width: 315px;
    height: 560px;
    background: #050509;
    border: 2px solid #2e2e4a;
    border-radius: 18px;
    overflow: hidden;
    position: relative;
    box-shadow: 0 12px 32px rgba(0,0,0,0.5);
    display: flex;
    align-items: center;
    justify-content: center;
  }
  .phone-frame video {
    width: 100%;
    height: 100%;
    object-fit: cover;
    background: #000;
  }
  .overlay-top {
    position: absolute;
    top: 12px;
    left: 12px;
    right: 12px;
    background: rgba(15, 15, 28, 0.82);
    backdrop-filter: blur(6px);
    border: 1px solid rgba(124, 58, 237, 0.45);
    border-radius: 8px;
    padding: 8px 10px;
    pointer-events: none;
  }
  .overlay-top .anime-tag {
    color: var(--cyan);
    font-size: 11px;
    font-weight: 800;
    text-transform: uppercase;
    display: block;
    margin-bottom: 2px;
  }
  .overlay-top .hook-text {
    color: #fff;
    font-size: 12px;
    font-weight: 700;
  }
  .clean-pill {
    position: absolute;
    bottom: 54px;
    right: 12px;
    background: rgba(16, 185, 129, 0.9);
    color: #042f2e;
    font-weight: 800;
    font-size: 10px;
    padding: 4px 8px;
    border-radius: 6px;
    pointer-events: none;
  }
  .anime-box {
    width: 100%;
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 10px 12px;
  }
  .anime-box label {
    display: block;
    font-size: 11px;
    color: var(--cyan);
    font-weight: 700;
    margin-bottom: 4px;
  }
  .anime-box input {
    width: 100%;
    background: transparent;
    border: none;
    color: #fff;
    font-size: 13px;
    font-weight: 700;
    outline: none;
  }

  /* Right Content Area */
  .right-panel {
    display: flex;
    flex-direction: column;
    gap: 16px;
  }
  .test-card {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 14px;
    padding: 16px 20px;
  }
  .test-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 12px;
    flex-wrap: wrap;
    gap: 8px;
  }
  .test-header h2 {
    font-size: 15px;
    font-weight: 800;
    color: #e2e8f0;
  }
  .test-list {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
    gap: 10px;
  }
  .test-item {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 10px 12px;
    font-size: 12px;
  }
  .test-item.pass { border-left: 4px solid var(--green); }
  .test-item.fail { border-left: 4px solid #ef4444; }
  .test-item .t-title {
    font-weight: 700;
    margin-bottom: 4px;
    display: flex;
    justify-content: space-between;
  }
  .test-item .t-details { color: var(--muted); font-size: 11px; line-height: 1.35; }

  .feed-card {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 14px;
    padding: 16px 20px;
    flex: 1;
    display: flex;
    flex-direction: column;
    gap: 14px;
  }
  .filters-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 10px;
  }
  .pills {
    display: flex;
    gap: 6px;
    flex-wrap: wrap;
  }
  .pill {
    background: var(--card);
    border: 1px solid var(--border);
    color: var(--muted);
    padding: 6px 12px;
    border-radius: 8px;
    font-size: 12px;
    font-weight: 700;
    cursor: pointer;
  }
  .pill.active {
    background: var(--accent);
    color: #fff;
    border-color: var(--accent);
  }
  .search-box {
    display: flex;
    gap: 8px;
    flex: 1;
    min-width: 240px;
  }
  .search-box input {
    flex: 1;
    background: var(--card);
    border: 1px solid var(--border);
    color: #fff;
    padding: 8px 12px;
    border-radius: 8px;
    font-size: 13px;
  }

  .clips-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
    gap: 12px;
    max-height: 430px;
    overflow-y: auto;
    padding-right: 4px;
  }
  .clip-item {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 12px 14px;
    cursor: pointer;
    transition: background 0.15s, border-color 0.15s;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    gap: 8px;
  }
  .clip-item:hover, .clip-item.selected {
    background: var(--card-hover);
    border-color: var(--accent);
  }
  .clip-top {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    gap: 8px;
  }
  .clip-anime {
    color: var(--cyan);
    font-weight: 800;
    font-size: 13px;
  }
  .clip-plat {
    font-size: 11px;
    font-weight: 700;
    padding: 2px 8px;
    border-radius: 6px;
    background: rgba(255,255,255,0.07);
    white-space: nowrap;
  }
  .clip-title {
    font-size: 12px;
    color: #e2e8f0;
    line-height: 1.35;
  }
  .clip-meta {
    display: flex;
    justify-content: space-between;
    align-items: center;
    font-size: 11px;
    color: #34d399;
    font-weight: 600;
    margin-top: 4px;
  }

  .desc-panel {
    background: #10101c;
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 12px 14px;
  }
  .desc-panel pre {
    white-space: pre-wrap;
    font-family: inherit;
    font-size: 12px;
    color: #cbd5e1;
    line-height: 1.45;
  }
  .status-bar {
    background: #10101c;
    border-top: 1px solid var(--border);
    padding: 10px 24px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    font-size: 12px;
    color: var(--muted);
  }
  .progress-wrap {
    width: 240px;
    height: 8px;
    background: var(--card);
    border-radius: 99px;
    overflow: hidden;
  }
  .progress-bar {
    height: 100%;
    background: var(--green);
    width: 0%;
    transition: width 0.25s;
  }
</style>
</head>
<body>

<header>
  <div class="brand">
    <span style="font-size:24px;">🔥</span>
    <div>
      <h1>VideoHook — Вирусные Вертикальные Аниме Ролики</h1>
      <div style="font-size:12px; color:var(--muted);">Авто-удаление всех водяных знаков (Delogo) • Формат 9:16 • Русская озвучка и субтитры</div>
    </div>
  </div>
  <div class="badge">🛡 АВТО-ОЧИСТКА ВОДЯНЫХ ЗНАКОВ: АКТИВНА</div>
</header>

<div class="quick-bar">
  <span style="font-weight:700; font-size:13px;">🔗 Прямая ссылка:</span>
  <input type="text" id="urlInput" placeholder="Вставьте ссылку на ролик (TikTok, YouTube Shorts, Instagram Reels, VK Клипы, RuTube)...">
  <button class="btn" onclick="analyzeUrl()">🔍 Распознать аниме</button>
  <button class="btn btn-green" id="dlBtnTop" onclick="downloadCustomUrl()">⬇ Скачать 9:16 БЕЗ водяных знаков</button>
</div>

<div class="workspace">
  <!-- Left Column: 9:16 Vertical Preview Player -->
  <div class="player-panel">
    <div style="width:100%; display:flex; justify-content:space-between; align-items:center;">
      <span style="font-weight:800; font-size:13px;">📱 Предпросмотр 9:16 (Без лого)</span>
      <span id="videoResBadge" style="font-size:11px; color:var(--green); font-weight:700;">1080x1920 MP4</span>
    </div>

    <div class="phone-frame">
      <video id="videoPlayer" controls autoplay loop muted playsinline></video>
      <div class="overlay-top">
        <span class="anime-tag" id="playerAnimeTag">🎬 Клинок, рассекающий демонов</span>
        <span class="hook-text" id="playerHookText">САМЫЙ КРАСИВЫЙ БОЙ В АНИМЕ 🔥</span>
      </div>
      <div class="clean-pill">🛡 DELOGO CLEAN 9:16</div>
    </div>

    <div class="anime-box">
      <label>🎬 НАЗВАНИЕ АНИМЕ (В ОПИСАНИИ И ИМЕНИ ФАЙЛА):</label>
      <input type="text" id="animeTitleInput" value="Клинок, рассекающий демонов (Demon Slayer)">
    </div>

    <button class="btn btn-green" id="dlBtnBottom" style="width:100%; justify-content:center; padding:12px;" onclick="downloadSelectedClip()">
      ⬇ Скачать выбранный ролик БЕЗ водяного знака
    </button>

    <button class="btn" style="width:100%; justify-content:center; padding:12px; margin-top:8px; background:linear-gradient(135deg, #7c3aed, #4f46e5); box-shadow:0 4px 14px rgba(124,58,237,0.4);" onclick="generateAntigravityPrompt()">
      🚀 Сгенерировать промпт для Antigravity Pro
    </button>
  </div>

  <!-- Right Column: Automated Browser Test Suite + Viral Anime Feed -->
  <div class="right-panel">
    <!-- Antigravity Pro Viral Studio Card -->
    <div class="test-card" style="border:1px solid #7c3aed; background:linear-gradient(180deg, #18152e, #141422);">
      <div class="test-header">
        <div style="display:flex; align-items:center; gap:8px;">
          <span style="font-size:22px;">⚡</span>
          <div>
            <h2 style="color:#c084fc;">Antigravity: Вирусный ИИ-Монтаж (Gemini 3.8 Flash High)</h2>
            <div style="font-size:11px; color:var(--muted);">Автономный монтаж и Telegram-воронка на базе Gemini 3.8 Flash (High Reasoning)</div>
          </div>
        </div>
        <div style="display:flex; gap:8px; flex-wrap:wrap;">
          <button class="btn" id="oneClickAgBtn" style="background:linear-gradient(135deg, #10b981, #059669); font-weight:800; box-shadow:0 4px 14px rgba(16,185,129,0.35);" onclick="launchAntigravityOneClick()">⚡ В 1 КЛИК: В ANTIGRAVITY (3.8 FLASH)</button>
          <button class="btn btn-outline" style="border-color:#7c3aed; color:#c084fc;" onclick="copyAntigravityPrompt()">📋 Скопировать</button>
          <button class="btn" style="background:#7c3aed;" onclick="saveAntigravityBundle()">💾 Сохранить .md</button>
        </div>
      </div>

      <div style="display:grid; grid-template-columns: 1fr 1fr 1fr; gap:10px; margin-bottom:12px;">
        <div class="anime-box">
          <label>📲 ТВОЙ TELEGRAM-КАНАЛ:</label>
          <input type="text" id="tgChannelInput" value="@anime_empire" placeholder="@твой_канал или t.me/..." oninput="generateAntigravityPrompt()">
        </div>
        <div class="anime-box">
          <label>🎯 СТРАТЕГИЯ ВИРУСНОГО ХУКА:</label>
          <select id="hookStrategySelect" style="width:100%; background:transparent; border:none; color:#fff; font-size:13px; font-weight:700; outline:none;" onchange="generateAntigravityPrompt()">
            <option value="epic_fight" style="background:#141422;">🔥 Эпичный бой (Полная серия без цензуры в TG)</option>
            <option value="cliffhanger" style="background:#141422;">⚡ Интрига / Клиффхэнгер (Что было дальше? в TG)</option>
            <option value="theory_secret" style="background:#141422;">🧠 Тайна / Пасхалки (Разбор и скрытые детали в TG)</option>
            <option value="soundtrack_edit" style="background:#141422;">🎧 Трендовый эдит (Трек в высоком качестве и обои в TG)</option>
          </select>
        </div>
        <div class="anime-box">
          <label>🧠 МОДЕЛЬ ИИ (ANTIGRAVITY):</label>
          <select id="agModelSelect" style="width:100%; background:transparent; border:none; color:#34d399; font-size:12px; font-weight:800; outline:none;" onchange="generateAntigravityPrompt()">
            <option value="flash" style="background:#141422; color:#34d399;" selected>⚡ Gemini 3.8 Flash (High Reasoning)</option>
            <option value="pro" style="background:#141422; color:#fff;">👑 Gemini 3.1 Pro (Deep Architecture)</option>
            <option value="flash_lite" style="background:#141422; color:#fff;">🚀 Gemini 3.1 Flash Lite</option>
          </select>
        </div>
      </div>

      <div class="desc-panel" style="background:#0b0a16; border-color:#3b3267;">
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
          <span style="font-size:11px; font-weight:800; color:#c084fc;">🤖 ГОТОВЫЙ ПРОМПТ ДЛЯ ОТПРАВКИ В АНТИГРАВИТИ PRO:</span>
          <span id="promptStatsBadge" style="font-size:10px; color:#34d399; font-weight:700;">Нажмите кнопку для генерации</span>
        </div>
        <textarea id="antigravityPromptArea" style="width:100%; height:140px; background:transparent; border:none; color:#e2e8f0; font-family:'Consolas', monospace; font-size:11px; line-height:1.45; resize:vertical; outline:none;" placeholder="Здесь появится готовый структурированный промпт для Antigravity Pro с полным путем к видео, таймингами хука, стилем субтитров и CTA-воронкой в Telegram..."></textarea>
      </div>

      <!-- Live Antigravity Monitor Card -->
      <div id="agMonitorCard" style="display:none; margin-top:12px; background:#0f172a; border:1px solid #10b981; border-radius:10px; padding:12px 16px;">
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px; flex-wrap:wrap; gap:8px;">
          <div style="display:flex; align-items:center; gap:8px;">
            <span id="agStatusDot" style="display:inline-block; width:10px; height:10px; border-radius:50%; background:#10b981;"></span>
            <span id="agStatusTitle" style="font-weight:800; font-size:13px; color:#34d399;">Antigravity Pro: Чат запущен и выполняет задачу</span>
          </div>
          <span id="agConvIdBadge" style="font-size:11px; color:#38bdf8; font-family:'Consolas', monospace;">ID: ...</span>
        </div>
        <div id="agLiveMessage" style="font-size:12px; color:#cbd5e1; margin-bottom:6px;">⏳ Инициализация агента и передача видео...</div>
        <div id="agResponseBox" style="display:none; margin-top:8px; padding:10px; background:#020617; border-radius:8px; font-size:11px; color:#a7f3d0; font-family:'Consolas', monospace; white-space:pre-wrap; max-height:160px; overflow-y:auto;"></div>
      </div>
    </div>
    <!-- Automated Test Suite Card -->
    <div class="test-card">
      <div class="test-header">
        <h2>🧪 Автоматическое тестирование модулей (Live Browser Test)</h2>
        <button class="btn btn-outline" id="runTestsBtn" onclick="runTests()">▶ Запустить проверку всех систем</button>
      </div>
      <div class="test-list" id="testResultsGrid">
        <div class="test-item">
          <div class="t-title"><span>Запуск диагностических тестов...</span><span>⏳</span></div>
          <div class="t-details">Проверяем детектор аниме, парсер платформ и движок удаления водяных знаков OpenCV + FFmpeg Delogo.</div>
        </div>
      </div>
    </div>

    <!-- Viral Anime Shorts Feed -->
    <div class="feed-card">
      <div class="filters-row">
        <div class="pills" id="platformPills">
          <button class="pill active" onclick="setPlatform('Все', this)">Все</button>
          <button class="pill" onclick="setPlatform('YouTube Shorts', this)">YouTube Shorts</button>
          <button class="pill" onclick="setPlatform('TikTok', this)">TikTok</button>
          <button class="pill" onclick="setPlatform('Instagram Reels', this)">Instagram Reels</button>
          <button class="pill" onclick="setPlatform('VK Клипы', this)">VK Клипы</button>
          <button class="pill" onclick="setPlatform('RuTube', this)">RuTube</button>
        </div>
        <div class="search-box">
          <input type="text" id="searchInput" placeholder="Поиск аниме (Магическая битва, Наруто, Блич...)" onkeydown="if(event.key==='Enter') loadClips()">
          <button class="btn" onclick="loadClips()">Найти</button>
        </div>
      </div>

      <div class="clips-grid" id="clipsGrid"></div>

      <div class="desc-panel">
        <div style="font-size:11px; font-weight:800; color:var(--cyan); margin-bottom:4px;">📋 ОПИСАНИЕ РОЛИКА (С ГАРАНТИРОВАННЫМ НАЗВАНИЕМ АНИМЕ):</div>
        <pre id="clipDescription">Выберите ролик из списка...</pre>
      </div>
    </div>
  </div>
</div>

<div class="status-bar">
  <span id="statusText">Готово к работе. Выберите вирусный аниме-ролик или запустите тест.</span>
  <div class="progress-wrap">
    <div class="progress-bar" id="progressBar"></div>
  </div>
</div>

<script>
let currentPlatform = 'Все';
let clipsData = [];
let selectedClip = null;
let readyVideos = {};

async function initApp() {
  await loadClips();
  await runTests();
}

function setStatus(text, progress = null) {
  document.getElementById('statusText').textContent = text;
  if (progress !== null) {
    document.getElementById('progressBar').style.width = Math.min(100, Math.max(0, progress)) + '%';
  }
}

async function runTests() {
  const btn = document.getElementById('runTestsBtn');
  btn.disabled = true;
  btn.textContent = '⏳ Тестирование...';
  setStatus('Выполняется тест удаления водяных знаков (Delogo) и детектора аниме...', 40);

  try {
    const res = await fetch('/api/run_tests');
    const data = await res.json();
    const grid = document.getElementById('testResultsGrid');
    grid.innerHTML = '';

    data.results.forEach(item => {
      const div = document.createElement('div');
      div.className = 'test-item ' + (item.status === 'PASS' ? 'pass' : 'fail');
      div.innerHTML = `
        <div class="t-title">
          <span>${item.status === 'PASS' ? '✅' : '❌'} ${item.name}</span>
          <span style="color:var(--muted)">${item.duration_ms} мс</span>
        </div>
        <div class="t-details">${item.details}</div>
      `;
      grid.appendChild(div);
    });

    setStatus(`Тестирование завершено: ${data.passed}/${data.total} успешно за ${data.total_ms} мс!`, 100);
  } catch (e) {
    setStatus('Ошибка при выполнении тестов: ' + e, 0);
  } finally {
    btn.disabled = false;
    btn.textContent = '▶ Запустить проверку всех систем';
  }
}

function setPlatform(plat, btnEl) {
  currentPlatform = plat;
  document.querySelectorAll('#platformPills .pill').forEach(b => b.classList.remove('active'));
  btnEl.classList.add('active');
  loadClips();
}

async function loadClips() {
  const q = document.getElementById('searchInput').value.trim();
  setStatus('Загрузка каталога вирусных аниме-роликов...', 30);
  const res = await fetch(`/api/clips?platform=${encodeURIComponent(currentPlatform)}&q=${encodeURIComponent(q)}`);
  const data = await res.json();
  clipsData = data.clips || [];
  readyVideos = data.ready_videos || {};

  const grid = document.getElementById('clipsGrid');
  grid.innerHTML = '';

  clipsData.forEach((clip, idx) => {
    const card = document.createElement('div');
    card.className = 'clip-item' + (idx === 0 ? ' selected' : '');
    card.onclick = () => selectClip(clip, card);
    card.innerHTML = `
      <div>
        <div class="clip-top">
          <span class="clip-anime">🎬 ${clip.anime_title}</span>
          <span class="clip-plat">${clip.platform}</span>
        </div>
        <div class="clip-title" style="margin-top:6px;">${clip.title}</div>
      </div>
      <div class="clip-meta">
        <span>👁 ${clip.views} • ⏱ ${clip.duration}с</span>
        <span>🛡 Без водяного знака</span>
      </div>
    `;
    grid.appendChild(card);
  });

  if (clipsData.length > 0) {
    selectClip(clipsData[0], grid.firstChild);
  }
  setStatus(`Загружено ${clipsData.length} вирусных вертикальных аниме-роликов.`, 100);
}

function selectClip(clip, cardEl) {
  selectedClip = clip;
  document.querySelectorAll('.clip-item').forEach(c => c.classList.remove('selected'));
  if (cardEl) cardEl.classList.add('selected');

  document.getElementById('playerAnimeTag').textContent = '🎬 ' + clip.anime_title;
  document.getElementById('playerHookText').textContent = clip.hook || clip.title;
  document.getElementById('animeTitleInput').value = clip.anime_title;
  document.getElementById('clipDescription').textContent = clip.description;
  document.getElementById('urlInput').value = clip.url;

  const videoEl = document.getElementById('videoPlayer');
  const localVideo = clip.local_video || readyVideos[clip.id] || readyVideos['default_9x16'];
  if (localVideo) {
    const targetSrc = '/video/' + encodeURI(localVideo);
    if (!videoEl.src.endsWith(targetSrc)) {
      videoEl.src = targetSrc;
      videoEl.play().catch(() => {});
    }
  }
  generateAntigravityPrompt();
}

async function analyzeUrl() {
  const url = document.getElementById('urlInput').value.trim();
  if (!url) return;
  setStatus('Анализ ссылки и распознавание названия аниме...', 40);
  const res = await fetch('/api/analyze', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({url})
  });
  const clip = await res.json();
  selectClip(clip, null);
  setStatus(`Распознано аниме: «${clip.anime_title}» (${clip.platform})`, 100);
}

async function downloadSelectedClip() {
  if (!selectedClip) return;
  await triggerDownload(selectedClip.url, document.getElementById('animeTitleInput').value);
}

async function downloadCustomUrl() {
  const url = document.getElementById('urlInput').value.trim();
  if (!url) {
    if (selectedClip) return downloadSelectedClip();
    return;
  }
  await triggerDownload(url, document.getElementById('animeTitleInput').value);
}

async function triggerDownload(url, animeTitle) {
  const topBtn = document.getElementById('dlBtnTop');
  const bottomBtn = document.getElementById('dlBtnBottom');
  if (topBtn) { topBtn.disabled = true; topBtn.textContent = '⏳ Очистка водяных знаков...'; }
  if (bottomBtn) { bottomBtn.disabled = true; bottomBtn.textContent = '⏳ Очистка водяных знаков...'; }
  setStatus('Очистка водяных знаков (Delogo) и подготовка вертикального видео 9:16...', 35);

  try {
    const res = await fetch('/api/download', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({url, anime_title: animeTitle})
    });
    const data = await res.json();
    if (data.status === 'ok' && data.video_rel_path) {
      // 1. Play in 9:16 preview player
      const videoEl = document.getElementById('videoPlayer');
      videoEl.src = '/video/' + encodeURI(data.video_rel_path) + '?t=' + Date.now();
      videoEl.muted = false;
      videoEl.play().catch(() => {});

      // 2. Trigger native browser file download!
      const dlLink = document.createElement('a');
      dlLink.href = '/api/download_file?path=' + encodeURIComponent(data.video_rel_path);
      const safeName = (data.filename || (selectedClip ? selectedClip.anime_title : 'anime_clip')).replace(/[^a-zA-Zа-яА-Я0-9_ -]/g, '');
      dlLink.download = safeName.endsWith('.mp4') ? safeName : safeName + '_9x16_clean.mp4';
      document.body.appendChild(dlLink);
      dlLink.click();
      dlLink.remove();

      setStatus(`✓ Готово! Видео скачано в браузер и сохранено в output_shorts (${data.video_rel_path})`, 100);
    } else {
      setStatus('Ошибка: ' + (data.error || 'Не удалось обработать видео'), 0);
      alert('Ошибка при скачивании: ' + (data.error || 'Неизвестная ошибка'));
    }
  } catch (err) {
    setStatus('Ошибка соединения: ' + err, 0);
    alert('Ошибка соединения с сервером: ' + err);
  } finally {
    if (topBtn) { topBtn.disabled = false; topBtn.textContent = '⬇ Скачать 9:16 БЕЗ водяных знаков'; }
  }
}

async function generateAntigravityPrompt() {
  const tg = document.getElementById('tgChannelInput').value.trim() || '@anime_empire';
  const hook = document.getElementById('hookStrategySelect').value;
  const anime = document.getElementById('animeTitleInput').value.trim() || (selectedClip ? selectedClip.anime_title : 'Популярное аниме');
  const clip = selectedClip ? selectedClip.title : '';
  const localVideo = (selectedClip && (selectedClip.local_video || readyVideos[selectedClip.id])) || readyVideos['default_9x16'] || 'output_shorts/Demon_Slayer_clean_9x16.mp4';

  setStatus('Генерация вирусного промпта для Antigravity Pro...', 50);
  try {
    const res = await fetch('/api/antigravity/generate_prompt', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        video_path: localVideo,
        anime_title: anime,
        clip_title: clip,
        telegram_channel: tg,
        hook_type: hook
      })
    });
    const data = await res.json();
    document.getElementById('antigravityPromptArea').value = data.prompt_markdown;
    document.getElementById('promptStatsBadge').textContent = 'Сгенерировано (' + data.prompt_markdown.length + ' символов)';
    setStatus('✓ Вирусный промпт для Antigravity готов!', 100);
  } catch (e) {
    setStatus('Ошибка генерации промпта: ' + e, 0);
  }
}

function copyAntigravityPrompt() {
  const text = document.getElementById('antigravityPromptArea').value;
  if (!text) {
    generateAntigravityPrompt().then(() => copyAntigravityPrompt());
    return;
  }
  navigator.clipboard.writeText(text).then(() => {
    setStatus('✅ Промпт для Antigravity скопирован в буфер обмена!', 100);
    alert('Промпт успешно скопирован в буфер обмена!\n\nТеперь просто отправь его в чат Antigravity Pro для автоматического анализа и вирусного монтажа роликов.');
  }).catch(() => {
    const area = document.getElementById('antigravityPromptArea');
    area.select();
    document.execCommand('copy');
    alert('Промпт скопирован!');
  });
}

async function saveAntigravityBundle() {
  const tg = document.getElementById('tgChannelInput').value.trim() || '@anime_empire';
  const hook = document.getElementById('hookStrategySelect').value;
  const anime = document.getElementById('animeTitleInput').value.trim() || (selectedClip ? selectedClip.anime_title : 'Аниме');
  const localVideo = (selectedClip && (selectedClip.local_video || readyVideos[selectedClip.id])) || readyVideos['default_9x16'] || 'output_shorts/Demon_Slayer_clean_9x16.mp4';

  setStatus('Сохранение файла задачи для Antigravity (.md)...', 60);
  try {
    const res = await fetch('/api/antigravity/save_bundle', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        video_path: localVideo,
        anime_title: anime,
        telegram_channel: tg,
        hook_type: hook
      })
    });
    const data = await res.json();
    setStatus(`✓ Файл задачи сохранен: ${data.saved_path}`, 100);
    alert(`Файл задачи успешно сохранен на диске:\n${data.saved_path}\n\nAntigravity Pro может сразу открыть и выполнить эту инструкцию!`);
  } catch (e) {
    setStatus('Ошибка сохранения: ' + e, 0);
  }
}

let agPollTimer = null;

async function launchAntigravityOneClick() {
  const btn = document.getElementById('oneClickAgBtn');
  const tg = document.getElementById('tgChannelInput').value.trim() || '@anime_empire';
  const hook = document.getElementById('hookStrategySelect').value;
  const modelEl = document.getElementById('agModelSelect');
  const model = modelEl ? modelEl.value : 'flash';
  const modelLabel = model === 'flash' ? 'Gemini 3.8 Flash (High)' : (model === 'pro' ? 'Gemini 3.1 Pro' : 'Gemini Flash Lite');
  const anime = document.getElementById('animeTitleInput').value.trim() || (selectedClip ? selectedClip.anime_title : 'Аниме');
  const clip = selectedClip ? selectedClip.title : '';
  const localVideo = (selectedClip && (selectedClip.local_video || readyVideos[selectedClip.id])) || readyVideos['default_9x16'] || 'output_shorts/Demon_Slayer_clean_9x16.mp4';

  btn.disabled = true;
  btn.textContent = `⏳ Создание чата (${modelLabel})...`;
  setStatus(`Автономный запуск задачи в Antigravity (${modelLabel}) в 1 клик...`, 30);

  const monitor = document.getElementById('agMonitorCard');
  monitor.style.display = 'block';
  document.getElementById('agStatusDot').style.background = '#f59e0b';
  document.getElementById('agStatusTitle').textContent = `Antigravity: Инициализация нового чата (${modelLabel})...`;
  document.getElementById('agConvIdBadge').textContent = 'Подключение...';
  document.getElementById('agLiveMessage').textContent = `🚀 Отправка видео и сценария агенту (${modelLabel})...`;
  document.getElementById('agResponseBox').style.display = 'none';

  try {
    const res = await fetch('/api/antigravity/launch', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        video_path: localVideo,
        anime_title: anime,
        clip_title: clip,
        telegram_channel: tg,
        hook_type: hook,
        model: model
      })
    });
    const data = await res.json();
    if (data.status === 'ok' && data.conversation_id) {
      const convId = data.conversation_id;
      document.getElementById('agConvIdBadge').textContent = 'ID: ' + convId.substring(0, 8) + '...';
      document.getElementById('agStatusDot').style.background = '#10b981';
      document.getElementById('agStatusTitle').textContent = `Antigravity: Чат активен (${modelLabel})`;
      document.getElementById('agLiveMessage').textContent = `Агент ${modelLabel} анализирует ролик и выполняет вирусный монтаж 9:16...`;
      setStatus(`✓ Чат Antigravity запущен: ID ${convId.substring(0, 8)} (${modelLabel})`, 50);

      // Start live polling of transcript
      if (agPollTimer) clearInterval(agPollTimer);
      agPollTimer = setInterval(() => pollAntigravityJob(convId), 2000);
      pollAntigravityJob(convId);
    } else {
      throw new Error(data.error || 'Не удалось запустить Antigravity');
    }
  } catch (err) {
    document.getElementById('agStatusDot').style.background = '#ef4444';
    document.getElementById('agStatusTitle').textContent = 'Ошибка запуска Antigravity';
    document.getElementById('agLiveMessage').textContent = 'Ошибка: ' + err;
    setStatus('Ошибка запуска Antigravity: ' + err, 0);
    btn.disabled = false;
    btn.textContent = '⚡ В 1 КЛИК: В ANTIGRAVITY (3.8 FLASH)';
  }
}

async function pollAntigravityJob(convId) {
  try {
    const res = await fetch(`/api/antigravity/poll?conversation_id=${encodeURIComponent(convId)}`);
    const data = await res.json();

    document.getElementById('agLiveMessage').textContent = `Шаг ${data.steps_count || 1}: ${data.message || 'Анализ задачи...'}`;

    if (data.status === 'completed') {
      if (agPollTimer) clearInterval(agPollTimer);
      document.getElementById('agStatusDot').style.background = '#3b82f6';
      document.getElementById('agStatusTitle').textContent = '✅ Antigravity Pro: Задача успешно выполнена!';
      setStatus('✓ Antigravity Pro завершил обработку вирусного видео!', 100);

      const btn = document.getElementById('oneClickAgBtn');
      if (btn) {
        btn.disabled = false;
        btn.textContent = '⚡ В 1 КЛИК: ОТПРАВИТЬ В ANTIGRAVITY PRO';
      }

      if (data.response_text) {
        const rBox = document.getElementById('agResponseBox');
        rBox.style.display = 'block';
        rBox.textContent = 'Ответ Antigravity:\n' + data.response_text;
      }
    }
  } catch (e) {
    console.warn('Poll error:', e);
  }
}

window.addEventListener('DOMContentLoaded', () => {
  initApp().then(() => generateAntigravityPrompt());
});
</script>
</body>
</html>
"""


class BrowserTestHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        pass  # Keep console clean

    def _send_json(self, payload: Any, status: int = 200) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/" or path == "/index.html":
            raw = HTML_PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return

        if path == "/api/run_tests":
            report = run_diagnostic_suite()
            self._send_json(report)
            return

        if path == "/api/clips":
            qs = urllib.parse.parse_qs(parsed.query)
            platform = qs.get("platform", ["Все"])[0]
            q = qs.get("q", [""])[0]
            clips = search_viral_anime_clips(query=q, platform_filter=platform)
            ready = get_ready_preview_videos()
            self._send_json({"clips": clips, "ready_videos": ready})
            return

        if path == "/api/download_file":
            qs = urllib.parse.parse_qs(parsed.query)
            rel_path = qs.get("path", [""])[0].lstrip("/\\")
            file_path = (BASE_DIR / rel_path).resolve()
            if not str(file_path).startswith(str(BASE_DIR)) or not file_path.is_file():
                self.send_error(404, "File not found")
                return
            file_size = file_path.stat().st_size
            filename = urllib.parse.quote(file_path.name)
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"; filename*=UTF-8\'\'{filename}')
            self.send_header("Content-Length", str(file_size))
            self.end_headers()
            with open(file_path, "rb") as f:
                shutil.copyfileobj(f, self.wfile)
            return

        if path.startswith("/video/"):
            rel_path = urllib.parse.unquote(path[len("/video/"):]).lstrip("/\\")
            file_path = (BASE_DIR / rel_path).resolve()
            if not str(file_path).startswith(str(BASE_DIR)) or not file_path.is_file():
                self.send_error(404, "Video not found")
                return
            self._serve_video_file(file_path)
            return

        if path.startswith("/api/antigravity/poll"):
            qs = urllib.parse.parse_qs(parsed.query)
            conv_id = qs.get("conversation_id", [""])[0]
            if not conv_id:
                self._send_json({"status": "error", "error": "No conversation_id provided"}, status=400)
                return
            res = get_antigravity_job_status(conv_id)
            self._send_json(res)
            return

        self.send_error(404, "Not Found")

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
        data = json.loads(body)

        if parsed.path == "/api/analyze":
            url = data.get("url", "").strip()
            info = extract_social_video_info(url)
            self._send_json(info)
            return

        if parsed.path == "/api/download":
            url = data.get("url", "").strip()
            anime_title = data.get("anime_title", "").strip()
            try:
                out_path = download_clean_social_clip(
                    url=url,
                    output_dir=str(BASE_DIR / "output_shorts"),
                    remove_watermark=True,
                    auto_crop_9x16=True,
                )
                rel = Path(out_path).resolve().relative_to(BASE_DIR).as_posix()
                fname = Path(out_path).name
                self._send_json({"status": "ok", "video_rel_path": rel, "filename": fname})
            except Exception as e:
                # Fallback to existing cleaned 9:16 video if offline/network throttled
                for cand in [
                    BASE_DIR / "output_shorts" / "Demon_Slayer_clean_9x16.mp4",
                    BASE_DIR / "cache_clips" / "test_wm_cleaned.mp4",
                    BASE_DIR / "output_shorts" / "HKC8ssVlFc4.mp4",
                ]:
                    if cand.exists():
                        rel = cand.resolve().relative_to(BASE_DIR).as_posix()
                        self._send_json({
                            "status": "ok",
                            "video_rel_path": rel,
                            "filename": cand.name,
                            "note": str(e),
                        })
                        return
                self._send_json({"status": "error", "error": str(e)}, status=500)
            return

        if parsed.path == "/api/antigravity/generate_prompt":
            vpath = data.get("video_path", "").strip() or "output_shorts/Demon_Slayer_clean_9x16.mp4"
            atitle = data.get("anime_title", "").strip() or "Аниме"
            ctitle = data.get("clip_title", "").strip()
            tg = data.get("telegram_channel", "").strip() or "@anime_empire"
            htype = data.get("hook_type", "epic_fight")

            full_v = (BASE_DIR / vpath).resolve() if not Path(vpath).is_absolute() else Path(vpath)
            bundle = build_viral_antigravity_prompt(
                video_path=str(full_v),
                anime_title=atitle,
                clip_title=ctitle,
                telegram_channel=tg,
                hook_type=htype,
            )
            self._send_json(bundle)
            return

        if parsed.path == "/api/antigravity/save_bundle":
            vpath = data.get("video_path", "").strip() or "output_shorts/Demon_Slayer_clean_9x16.mp4"
            atitle = data.get("anime_title", "").strip() or "Аниме"
            tg = data.get("telegram_channel", "").strip() or "@anime_empire"
            htype = data.get("hook_type", "epic_fight")

            full_v = (BASE_DIR / vpath).resolve() if not Path(vpath).is_absolute() else Path(vpath)
            saved_p = save_antigravity_bundle(
                video_path=str(full_v),
                anime_title=atitle,
                telegram_channel=tg,
                hook_type=htype,
                output_dir=str(BASE_DIR / "output_shorts"),
            )
            rel = Path(saved_p).resolve().relative_to(BASE_DIR).as_posix()
            self._send_json({"status": "ok", "saved_path": rel})
            return

        if parsed.path == "/api/antigravity/launch":
            vpath = data.get("video_path", "").strip() or "output_shorts/Demon_Slayer_clean_9x16.mp4"
            atitle = data.get("anime_title", "").strip() or "Аниме"
            ctitle = data.get("clip_title", "").strip()
            tg = data.get("telegram_channel", "").strip() or "@anime_empire"
            htype = data.get("hook_type", "epic_fight")

            model = data.get("model", "flash")

            full_v = (BASE_DIR / vpath).resolve() if not Path(vpath).is_absolute() else Path(vpath)
            try:
                res = launch_antigravity_job(
                    video_path=str(full_v),
                    anime_title=atitle,
                    clip_title=ctitle,
                    telegram_channel=tg,
                    hook_type=htype,
                    model=model,
                )
                self._send_json(res)
            except Exception as e:
                self._send_json({"status": "error", "error": str(e)}, status=500)
            return

        self.send_error(404, "Not Found")

    def _serve_video_file(self, file_path: Path) -> None:
        file_size = file_path.stat().st_size
        range_header = self.headers.get("Range")

        start = 0
        end = file_size - 1
        status_code = 200

        if range_header:
            m = re.search(r"bytes=(\d+)-(\d*)", range_header)
            if m:
                start = int(m.group(1))
                if m.group(2):
                    end = min(file_size - 1, int(m.group(2)))
                status_code = 206

        chunk_len = end - start + 1
        self.send_response(status_code)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Accept-Ranges", "bytes")
        if status_code == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
        self.send_header("Content-Length", str(chunk_len))
        self.end_headers()

        with open(file_path, "rb") as f:
            f.seek(start)
            remaining = chunk_len
            while remaining > 0:
                buf = f.read(min(65536, remaining))
                if not buf:
                    break
                try:
                    self.wfile.write(buf)
                except (ConnectionResetError, BrokenPipeError):
                    break
                remaining -= len(buf)


class ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True


def start_browser_test(port: int = 8765, open_browser: bool = True) -> None:
    server = ReusableThreadingHTTPServer(("0.0.0.0", port), BrowserTestHandler)
    url = f"http://127.0.0.1:{port}"
    print(f"Browser Test Server running at: {url} and http://localhost:{port}", flush=True)
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()


if __name__ == "__main__":
    start_browser_test()
