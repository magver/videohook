import os
import shutil
import time
from pathlib import Path
import tkinter.messagebox

import cv2
from clipper import get_ffmpeg_path, render_multi_segment_clip
from test_clipper import generate_sample_video
from editor_window import VideoEditorWindow
from gui import VideoHookApp

# Suppress messageboxes in automated tests
tkinter.messagebox.showinfo = lambda *args, **kwargs: None
tkinter.messagebox.showwarning = lambda *args, **kwargs: None
tkinter.messagebox.showerror = lambda *args, **kwargs: None


def test_multi_segment_render():
    print("Testing multi-segment clipping with cuts and concat...")
    ffmpeg_bin = get_ffmpeg_path()
    sample = "test_multi_sample.mp4"
    output = "test_multi_out.mp4"

    generate_sample_video(sample, ffmpeg_bin, duration=10.0)

    # Cut out [2.0 - 4.0] and [6.0 - 8.0], keeping [0.5 - 2.0], [4.0 - 6.0], [8.0 - 9.5]
    segments = [(0.5, 2.0), (4.0, 6.0), (8.0, 9.5)]
    total_expected = (2.0 - 0.5) + (6.0 - 4.0) + (9.5 - 8.0) # 1.5 + 2.0 + 1.5 = 5.0s

    out_file = render_multi_segment_clip(
        input_path=sample,
        output_path=output,
        segments=segments,
        layout_mode="fullscreen",
        watermark_text="Смотри продолжение",
        hook_header_text="ТЕСТОВЫЙ ХУК 🔥",
        subtitles_text="Русские субтитры работают идеально!",
        bg_music_path="assets/music/phonk_bass.mp3",
        bg_music_volume=0.25,
        speed_factor=1.03,
        preset="ultrafast",
    )

    out_p = Path(out_file)
    assert out_p.exists() and out_p.stat().st_size > 0, "Rendered multi-segment video is missing or empty"

    # Verify duration & geometry with OpenCV
    cap = cv2.VideoCapture(str(out_p))
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frames = cap.get(cv2.CAP_PROP_FRAME_COUNT)
    dur = frames / fps if fps > 0 else 0
    cap.release()

    print(f"Multi-segment rendered: {w}x{h}, {dur:.2f}s (Expected ~{total_expected / 1.03:.2f}s)")
    assert w == 1080 and h == 1920, f"Expected 1080x1920 vertical format, got {w}x{h}"
    assert 3.5 <= dur <= 6.5, f"Unexpected duration {dur}s for combined cuts"

    # Cleanup
    out_p.unlink(missing_ok=True)
    Path(sample).unlink(missing_ok=True)
    print("Multi-segment render test PASSED successfully!\n")


def test_editor_window_headless():
    print("Testing VideoEditorWindow interactive state and scissors logic...")
    ffmpeg_bin = get_ffmpeg_path()
    sample = "test_editor_sample.mp4"
    generate_sample_video(sample, ffmpeg_bin, duration=15.0)

    app = VideoHookApp()
    app.update()

    editor = VideoEditorWindow(
        parent=app,
        video_path=sample,
        initial_subtitles="Тест субтитров",
        initial_hook="ТОП МОМЕНТ",
        output_dir="test_editor_out",
    )
    editor.update()

    assert editor.cap is not None and editor.cap.isOpened(), "Editor video capture not opened"
    assert len(editor.segments) == 1, "Initial segment count should be 1"

    # Test seek
    editor.seek_to(5.0)
    assert abs(editor.current_time - 5.0) < 0.2, "Seek failed to set current time"

    # Test scissors cut at current playhead
    editor._cut_at_current_time()
    assert len(editor.segments) == 2, f"Expected 2 segments after cut, got {len(editor.segments)}"
    print(f"Scissors cut at 5.0s produced: {[(s['start'], s['end']) for s in editor.segments]}")

    # Cut again at 10.0s
    editor.seek_to(10.0)
    editor._cut_at_current_time()
    assert len(editor.segments) == 3, f"Expected 3 segments after 2nd cut, got {len(editor.segments)}"
    print(f"Scissors cut at 10.0s produced: {[(s['start'], s['end']) for s in editor.segments]}")

    # Test CapCut click-and-drag range cut (Stage 2A)
    # Range cut [11.0, 13.0] inside last segment [10.0, 15.0]
    editor._apply_drag_cut_range(11.0, 13.0)
    # Should create sub-segments: [10.0, 11.0] (keep), [11.0, 13.0] (cut/False), [13.0, 15.0] (keep)
    assert any(s["start"] == 11.0 and s["end"] == 13.0 and not s["keep"] for s in editor.segments), "Drag-cut range failed"
    print("CapCut drag-cut range [11.0, 13.0] verified!")

    # Test CapCut double click cut (Stage 2B)
    # Mock double click event at canvas center
    mock_event = type("MockEvent", (), {"x": 280})()
    initial_seg_count = len(editor.segments)
    editor._on_canvas_double_click(mock_event)
    assert len(editor.segments) == initial_seg_count + 1, "Double click pinpoint cut failed"
    print("CapCut double click pinpoint cut verified!")

    # Test CapCut boundary handle detection (Stage 2D)
    boundary_t = editor.segments[0]["end"]
    actual_width = editor.timeline_canvas.winfo_width()
    width = actual_width if actual_width > 1 else 560
    bx = int((boundary_t / editor.duration) * width)
    detected_b = editor._find_boundary_at_x(bx, threshold_px=8)
    assert detected_b is not None and abs(detected_b - boundary_t) < 0.1, "Boundary handle detection failed"
    # Test Stage 3: Zoom and Pan
    assert editor.zoom_factor == 1.0, "Default zoom should be 1.0"
    # Zoom in
    editor._zoom_step(1, center_ratio=0.5)
    assert editor.zoom_factor == 1.5, f"Expected zoom 1.5, got {editor.zoom_factor}"
    editor._zoom_step(1, center_ratio=0.5)
    assert editor.zoom_factor == 2.0, f"Expected zoom 2.0, got {editor.zoom_factor}"
    print(f"Zoom in verified: factor={editor.zoom_factor}")

    # Zoom fit
    editor._zoom_fit()
    assert editor.zoom_factor == 1.0 and editor.view_offset_sec == 0.0, "Zoom fit failed"
    print("Zoom fit verified!")

    # 1:1 Zoom
    editor._zoom_one_to_one()
    assert editor.zoom_factor > 1.0, "1:1 zoom should increase factor"
    print(f"Zoom 1:1 verified: factor={editor.zoom_factor}")

    # Reset back to fit for clean state
    editor._zoom_fit()

    # Test Stage 4: Volume & Audio Controls
    assert editor.video_volume == 0.80, "Default video volume should be 0.80 (80%)"
    editor._on_volume_changed(0.50)
    assert editor.video_volume == 0.50, "Volume change to 0.50 failed"
    assert editor.preview_vol_lbl.cget("text") == "50%", "Preview volume label not updated"

    # Mute toggle test
    assert editor.audio_enabled is True, "Audio should be enabled initially"
    editor._toggle_audio()
    assert editor.audio_enabled is False, "Toggle audio mute failed"
    assert "Без звука" in editor.mute_btn.cget("text"), "Mute button text not updated"
    editor._toggle_audio()
    assert editor.audio_enabled is True, "Toggle audio unmute failed"

    # Audio balance slider test in export params
    editor.dialog_vol_slider.set(0.65)
    editor.music_vol_slider.set(0.35)
    render_params = editor._collect_render_params()
    assert render_params is not None, "Render params should not be None"
    assert abs(render_params["dialog_vol"] - 0.65) < 0.01, f"Expected dialog_vol 0.65, got {render_params.get('dialog_vol')}"
    assert abs(render_params["music_vol"] - 0.35) < 0.01, f"Expected music_vol 0.35, got {render_params.get('music_vol')}"
    print("Stage 4: Volume slider, mute toggle, and audio balance parameters verified!")

    # Close and cleanup
    editor._on_close()
    app.destroy()
    Path(sample).unlink(missing_ok=True)
    shutil.rmtree("test_editor_out", ignore_errors=True)
    print("VideoEditorWindow test PASSED successfully!\n")


def test_render_final_pipeline():
    print("Testing render_final pipeline with amix, concat, progress callback...")
    ffmpeg_bin = get_ffmpeg_path()
    sample = "test_final_sample.mp4"
    output = "test_final_out.mp4"

    generate_sample_video(sample, ffmpeg_bin, duration=8.0)

    segments = [(0.0, 3.0), (4.0, 7.0)]
    progress_recorded = []

    from clipper import render_final
    out_file = render_final(
        input_path=sample,
        output_path=output,
        segments=segments,
        music_path="assets/music/phonk_bass.mp3",
        music_vol=0.28,
        dialog_vol=0.9,
        hook_text="ФИНАЛ ТЕСТ 🔥",
        subtitle_text="Субтитры этапа 1",
        watermark_text="VideoHook v2.0",
        crop_mode="fullscreen",
        speed=1.03,
        preset="ultrafast",
        progress_callback=lambda p: progress_recorded.append(p),
    )

    out_p = Path(out_file)
    assert out_p.exists() and out_p.stat().st_size > 0, "render_final output missing or empty"
    assert len(progress_recorded) > 0, "progress_callback was never invoked"
    assert progress_recorded[-1] >= 0.99, f"Final progress ratio {progress_recorded[-1]} < 0.99"

    cap = cv2.VideoCapture(str(out_p))
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()
    assert w == 1080 and h == 1920, f"Expected 1080x1920, got {w}x{h}"

    out_p.unlink(missing_ok=True)
    Path(sample).unlink(missing_ok=True)
    print("render_final test PASSED successfully!\n")


def test_stage6_ui_convenience():
    print("Testing Stage 6: Hotkeys, Virtualization (>20 segments), Redo/Undo, Selection, and Library search...")
    import hotkeys
    # Test hotkeys dictionary
    assert "S" in hotkeys.HOTKEYS
    assert "Ctrl+Z" in hotkeys.HOTKEYS
    assert "Ctrl+Y" in hotkeys.HOTKEYS
    assert "Ctrl+A" in hotkeys.HOTKEYS
    assert "Delete" in hotkeys.HOTKEYS
    assert "1" in hotkeys.HOTKEYS
    assert "9" in hotkeys.HOTKEYS
    print(f"Hotkeys catalog verified ({len(hotkeys.HOTKEYS)} hotkeys registered)")

    ffmpeg_bin = get_ffmpeg_path()
    sample = "test_stage6_sample.mp4"
    generate_sample_video(sample, ffmpeg_bin, duration=30.0)

    app = VideoHookApp()
    app.update()

    # 1. Test Library Search & Filter in VideoHookApp
    app.files_queue.clear()
    app._add_file_to_queue("C:/Videos/Naruto_Action_ep01.mp4")
    app._add_file_to_queue("C:/Videos/Toradora_Romance_ep05.mp4")
    app._add_file_to_queue("C:/Videos/Another_Horror_ep03.mp4")
    assert len(app.files_queue) == 3
    assert app.files_queue[0]["genre"] == "Action"
    assert app.files_queue[1]["genre"] == "Romance"
    assert app.files_queue[2]["genre"] == "Horror"

    # Search filter test
    app.lib_search_entry.delete(0, "end")
    app.lib_search_entry.insert(0, "naruto")
    app._refresh_queue_display()
    assert "1 из 3" in app.queue_count_lbl.cget("text")

    # Genre filter test
    app.lib_search_entry.delete(0, "end")
    app.lib_genre_var.set("Romance")
    app._refresh_queue_display()
    assert "1 из 3" in app.queue_count_lbl.cget("text")

    # Status filter test
    app.lib_genre_var.set("Все жанры")
    app.files_queue[1]["status"] = "Готово"
    app.lib_status_var.set("Готово")
    app._refresh_queue_display()
    assert "1 из 3" in app.queue_count_lbl.cget("text")

    # Reset filters
    app.lib_status_var.set("Все статусы")
    app._refresh_queue_display()
    assert "Загружено файлов: 3" in app.queue_count_lbl.cget("text")
    print("Library search, genre filter, and status filter verified!")

    # 2. Test VideoEditorWindow Stage 6 features
    editor = VideoEditorWindow(
        parent=app,
        video_path=sample,
        initial_subtitles="Тест субтитров",
        initial_hook="ТОП МОМЕНТ",
        output_dir="test_stage6_out",
    )
    editor.update()

    # Test Undo and Redo
    initial_seg_len = len(editor.segments)
    editor.seek_to(5.0)
    editor._cut_at_current_time()
    assert len(editor.segments) == initial_seg_len + 1
    editor._undo_cut()
    assert len(editor.segments) == initial_seg_len
    editor._redo_cut()
    assert len(editor.segments) == initial_seg_len + 1
    print("Undo (Ctrl+Z) and Redo (Ctrl+Y) verified!")

    # Test Select All (Ctrl+A)
    editor._select_all_segments()
    assert len(editor.selected_segment_indices) == len(editor.segments)
    editor._select_all_segments()  # Toggle deselect
    assert len(editor.selected_segment_indices) == 0
    print("Select All (Ctrl+A) toggle verified!")

    # Test Virtualization (> 20 segments)
    # Generate 25 segments
    test_segs = []
    for i in range(25):
        test_segs.append({"start": float(i), "end": float(i + 1), "keep": True})
    editor.segments = test_segs
    editor.segments_page = 0
    editor._refresh_segments_ui()

    # Verify page nav
    assert editor.segments_page == 0
    editor._change_segments_page(1)
    assert editor.segments_page == 1
    editor._change_segments_page(-1)
    assert editor.segments_page == 0
    print("Segments list virtualization (>20 segments pagination) verified!")

    # Test dynamic filmstrip step density
    editor.zoom_factor = 1.0
    # At zoom >= 10, step is 0.5s
    editor.zoom_factor = 12.0
    editor._generate_filmstrip()
    editor.update()
    assert len(editor.filmstrip_images) > 0
    print(f"Dynamic filmstrip generated ({len(editor.filmstrip_images)} thumbnails)")

    editor._on_close()
    app.destroy()
    Path(sample).unlink(missing_ok=True)
    shutil.rmtree("test_stage6_out", ignore_errors=True)
    print("Stage 6 tests PASSED successfully!\n")


if __name__ == "__main__":
    test_multi_segment_render()
    test_render_final_pipeline()
    test_editor_window_headless()
    test_stage6_ui_convenience()
    print("ALL VideoEditor tests passed 100%!")

