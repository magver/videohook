import os
import shutil
from pathlib import Path
import tkinter.messagebox

import cv2
from clipper import get_ffmpeg_path, render_vertical_clip
from test_clipper import generate_sample_video
from trends import fetch_top_anime, get_iconic_moments_for_anime
from gui import VideoHookApp

tkinter.messagebox.showinfo = lambda *args, **kwargs: None
tkinter.messagebox.showwarning = lambda *args, **kwargs: None


def test_trends_api():
    print("Testing AniList trends fetch...")
    items = fetch_top_anime(sort_type="POPULARITY_DESC", limit=5)
    assert len(items) > 0, "No anime returned from trends fetch!"
    print(f"Fetched {len(items)} popular anime titles:")
    for item in items[:3]:
        print(f" - {item['title']} (Score: {item['score']}%, Ep: {item['episodes']})")

    # Test curated iconic moments
    print("\nTesting iconic moments lookup...")
    ds_moments = get_iconic_moments_for_anime("Demon Slayer: Kimetsu no Yaiba")
    assert len(ds_moments) >= 2, "Failed to get curated moments for Demon Slayer"
    print(f"Demon Slayer moments: {[m['title'] for m in ds_moments]}")
    assert any("19" in m["title"] for m in ds_moments), "Episode 19 not found in Demon Slayer moments"

    custom_moments = get_iconic_moments_for_anime("Frieren: Beyond Journey's End")
    assert len(custom_moments) >= 1, "Failed to generate dynamic moments for unknown anime"
    print(f"Dynamic fallback moments: {[m['title'] for m in custom_moments]}")

    print("\nTesting AniList search by title...")
    from trends import search_anime_by_title
    search_results = search_anime_by_title("Naruto", limit=3)
    assert len(search_results) > 0, "Search by title returned no results"
    safe_titles = [s['title'].encode('ascii', 'replace').decode('ascii') for s in search_results]
    print(f"Search results for 'Naruto': {safe_titles}")


def test_hook_render():
    print("\nTesting 9:16 viral hook banner render...")
    ffmpeg_bin = get_ffmpeg_path()
    sample = "test_viral_sample.mp4"
    output = "test_viral_hook.mp4"
    generate_sample_video(sample, ffmpeg_bin, duration=4.0)

    out_file = render_vertical_clip(
        input_path=sample,
        output_path=output,
        start_time=0.5,
        duration=2.5,
        watermark_text="Смотри продолжение в профиле",
        hook_header_text="САМЫЙ ЭПИЧНЫЙ БОЙ В АНИМЕ 🔥",
        speed_factor=1.03
    )

    out_p = Path(out_file)
    assert out_p.exists() and out_p.stat().st_size > 0, "Rendered viral short is missing or empty"
    print(f"Rendered viral hook short successfully: {out_file} ({out_p.stat().st_size} bytes)")

    out_p.unlink(missing_ok=True)
    Path(sample).unlink(missing_ok=True)


def test_overhauled_gui():
    print("\nTesting Overhauled Dedicated GUI initialization...")
    app = VideoHookApp()
    app.update()
    
    # Check that dedicated viral downloader UI elements are initialized
    assert hasattr(app, "preview_canvas"), "Preview canvas missing"
    assert hasattr(app, "play_btn"), "Play button missing"
    assert hasattr(app, "social_anime_entry"), "Anime title entry missing"
    assert hasattr(app, "social_scroll"), "Scrollable viral feed missing"
    assert hasattr(app, "social_dl_btn"), "Direct download button missing"
    assert hasattr(app, "quick_download_btn"), "Quick URL download button missing"
    
    # Check social viral state and card generation
    assert hasattr(app, "social_clips_data") and len(app.social_clips_data) > 0, "Social clips not loaded"
    assert app.selected_social_clip is not None, "Initial social clip not selected"
    assert "anime_title" in app.selected_social_clip, "Anime title missing from selected clip"
    print(f"Overhauled GUI initialized successfully! Selected anime: {app.selected_social_clip['anime_title']}")
    app.destroy()


def test_social_downloader_and_anime_detector():
    print("\nTesting Anime Detector and Social Downloader...")
    from anime_detector import detect_anime_title, format_description_with_anime
    from social_downloader import identify_platform, CURATED_VIRAL_SHORTS, search_viral_anime_clips

    # 1. Platform identification
    assert identify_platform("https://www.youtube.com/shorts/abc") == "YouTube Shorts"
    assert identify_platform("https://www.tiktok.com/@user/video/123") == "TikTok"
    assert identify_platform("https://www.instagram.com/reel/xyz") == "Instagram Reels"
    assert "VK" in identify_platform("https://vk.com/clip-123_456")
    assert identify_platform("https://rutube.ru/shorts/789") == "RuTube"
    print("Platform identification passed for YouTube, TikTok, Instagram, VK, RuTube!")

    # 2. Anime Title Detection
    d1 = detect_anime_title("Годжо Сатору против Сукуны #shorts #jjk", "Аниме: Магическая битва")
    assert d1["found"] and "Jujutsu Kaisen" in d1["display_title"]
    
    d2 = detect_anime_title("Танец бога огня Тандзиро против Руи", "")
    assert d2["found"] and ("Клинок" in d2["display_title"] or "Demon Slayer" in d2["display_title"])

    d3 = detect_anime_title("Сон Джин Ву произносит Восстань #sololeveling", "")
    assert d3["found"] and ("Solo Leveling" in d3["display_title"] or "Поднятие уровня" in d3["display_title"])

    d4 = detect_anime_title("Лайт Ягами вернул память в вертолете #deathnote", "")
    assert d4["found"] and ("Death Note" in d4["display_title"] or "Тетрадь смерти" in d4["display_title"])
    print("Anime title detection patterns passed!")

    # 3. Description format with anime title guarantee
    desc = format_description_with_anime("Эпичный бой", "Клинок, рассекающий демонов", "YouTube Shorts", True, True)
    assert "🎬 НАЗВАНИЕ АНИМЕ: Клинок, рассекающий демонов" in desc
    print("Description formatting with anime title guarantee passed!")

    # 4. Curated viral shorts database
    assert len(CURATED_VIRAL_SHORTS) >= 8, f"Expected at least 8 curated clips, got {len(CURATED_VIRAL_SHORTS)}"
    for item in CURATED_VIRAL_SHORTS:
        assert item.get("anime_title"), f"Clip {item['id']} missing anime_title"
        assert item.get("no_watermark") is True, f"Clip {item['id']} not marked watermark-free"
        assert "🎬 НАЗВАНИЕ АНИМЕ:" in item.get("description", ""), f"Clip {item['id']} description missing anime title"

    # 5. Search filtering
    yt_clips = search_viral_anime_clips(platform_filter="YouTube Shorts")
    assert len(yt_clips) > 0 and any("YouTube" in c["platform"] for c in yt_clips)
    
    tt_clips = search_viral_anime_clips(platform_filter="TikTok")
    assert len(tt_clips) > 0 and any("TikTok" in c["platform"] for c in tt_clips)
    print("Social viral catalog and filters passed!")


def test_watermark_removal_engine():
    print("\nTesting Watermark Removal Engine (Delogo & 9:16 vertical)...")
    from watermark_remover import detect_watermark_regions, detect_outro_card, remove_watermarks_and_finalize_video
    from test_clipper import generate_sample_video

    ffmpeg_bin = get_ffmpeg_path()
    sample = "test_wm_sample.mp4"
    cleaned = "test_wm_sample_cleaned.mp4"
    generate_sample_video(sample, ffmpeg_bin, duration=3.0)

    # Test detection
    boxes = detect_watermark_regions(sample)
    print(f"Detected watermark regions on sample: {boxes}")

    # Test outro detection
    outro = detect_outro_card(sample)
    print(f"Detected outro duration: {outro}s")

    # Test full cleaning pipeline
    out_file = remove_watermarks_and_finalize_video(
        input_path=sample,
        output_path=cleaned,
        platform="TikTok",
        auto_crop_9x16=True,
    )
    assert Path(out_file).exists() and Path(out_file).stat().st_size > 0, "Cleaned video file not created"

    # Verify 9:16 vertical geometry
    cap = cv2.VideoCapture(out_file)
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()
    assert w == 1080 and h == 1920, f"Expected 1080x1920, got {w}x{h}"
    print(f"Watermark remover verified: output is clean 1080x1920 MP4!")

    Path(sample).unlink(missing_ok=True)
    Path(cleaned).unlink(missing_ok=True)


def test_aspect_ratios_and_viral_effects():
    import cv2
    from clipper import render_final, get_aspect_dims

    print("\nTesting Aspect Ratios (9:16, 4:5, 1:1) and TikTok/Reels Viral Effects...")
    assert get_aspect_dims("9:16") == (1080, 1920)
    assert get_aspect_dims("4:5") == (1080, 1350)
    assert get_aspect_dims("4х5") == (1080, 1350)
    assert get_aspect_dims("1:1") == (1080, 1080)
    assert get_aspect_dims("1х1") == (1080, 1080)
    print("Aspect ratio geometry mappings verified!")

    ffmpeg_bin = get_ffmpeg_path()
    sample = "test_ar_sample.mp4"
    generate_sample_video(sample, ffmpeg_bin, duration=4.0)

    viral_fx = {
        "zoom_punch": True,
        "white_flash": True,
        "vibrant_cc": True,
        "vignette": True,
        "sharpen": True,
        "bass_boost": True,
    }

    # 1. Test 9:16 with viral effects
    out_9x16 = "test_out_9x16.mp4"
    render_final(
        input_path=sample,
        output_path=out_9x16,
        segments=[(0.0, 2.5)],
        aspect_ratio="9:16",
        viral_effects=viral_fx,
        hook_text="ТОП ХУК 9:16 🔥",
        subtitle_text="Субтитры",
        preset="ultrafast",
    )
    cap = cv2.VideoCapture(out_9x16)
    w9 = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h9 = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()
    assert w9 == 1080 and h9 == 1920, f"Expected 1080x1920 for 9:16, got {w9}x{h9}"
    Path(out_9x16).unlink(missing_ok=True)
    print("9:16 TikTok/Reels render with full viral effects verified (1080x1920)!")

    # 2. Test 4:5 aspect ratio
    out_4x5 = "test_out_4x5.mp4"
    render_final(
        input_path=sample,
        output_path=out_4x5,
        segments=[(0.0, 2.5)],
        aspect_ratio="4:5",
        viral_effects=viral_fx,
        hook_text="INSTA PORTRAIT 4:5 📸",
        subtitle_text="Субтитры 4:5",
        preset="ultrafast",
    )
    cap = cv2.VideoCapture(out_4x5)
    w45 = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h45 = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()
    assert w45 == 1080 and h45 == 1350, f"Expected 1080x1350 for 4:5, got {w45}x{h45}"
    Path(out_4x5).unlink(missing_ok=True)
    print("4:5 Instagram Portrait render verified (1080x1350)!")

    # 3. Test 1:1 square aspect ratio
    out_1x1 = "test_out_1x1.mp4"
    render_final(
        input_path=sample,
        output_path=out_1x1,
        segments=[(0.0, 2.5)],
        aspect_ratio="1:1",
        viral_effects=viral_fx,
        hook_text="INSTA SQUARE 1:1 🔲",
        subtitle_text="Субтитры 1:1",
        preset="ultrafast",
    )
    cap = cv2.VideoCapture(out_1x1)
    w11 = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h11 = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()
    assert w11 == 1080 and h11 == 1080, f"Expected 1080x1080 for 1:1, got {w11}x{h11}"
    Path(out_1x1).unlink(missing_ok=True)
    print("1:1 Instagram Square render verified (1080x1080)!")

    # 4. Test safe video open in editor_window
    from editor_window import _safe_open_video
    cap_safe, path_safe = _safe_open_video(sample)
    assert cap_safe is not None and cap_safe.isOpened(), "Safe video open failed"
    cap_safe.release()
    print("Robust video open (_safe_open_video) verified!")

    Path(sample).unlink(missing_ok=True)


if __name__ == "__main__":
    test_trends_api()
    test_hook_render()
    test_aspect_ratios_and_viral_effects()
    test_social_downloader_and_anime_detector()
    test_watermark_removal_engine()
    test_overhauled_gui()
    print("\nAll Overhauled Viral Downloader & Watermark Removal tests passed with 100% success!")

