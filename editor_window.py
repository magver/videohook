import math
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import cv2
import customtkinter as ctk
from PIL import Image, ImageTk
import tkinter as tk
from tkinter import filedialog, messagebox

from clipper import get_ffmpeg_path, render_final, render_multi_segment_clip
from hotkeys import HOTKEYS, show_hotkeys_dialog

try:
    import pygame
    _PYGAME_OK = True
    pygame.mixer.pre_init(44100, -16, 2, 1024)
    pygame.mixer.init()
except Exception:
    _PYGAME_OK = False


def get_asset_path(rel_path: str) -> str:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        p = Path(sys._MEIPASS) / rel_path
        if p.exists():
            return str(p)
    base_dir = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
    p2 = base_dir / rel_path
    if p2.exists():
        return str(p2)
    p3 = Path(rel_path)
    if p3.exists():
        return str(p3.resolve())
    return rel_path


def format_time(seconds: float) -> str:
    mins = int(seconds // 60)
    secs = int(seconds % 60)
    ms = int((seconds - int(seconds)) * 10)
    return f"{mins:02d}:{secs:02d}.{ms}"


def _silent_popen(cmd, **kwargs):
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    return subprocess.Popen(cmd, creationflags=flags, **kwargs)


def _safe_open_video(video_path: str) -> Tuple[Optional[cv2.VideoCapture], str]:
    """
    Safely opens a video file in OpenCV with retry attempts and ffmpeg transcode fallback
    for troublesome containers or codecs (e.g. MKV/AV1/VP9).
    Returns (cap, resolved_path).
    """
    p = Path(video_path).resolve()
    if not p.exists() or p.stat().st_size == 0:
        return None, str(p)

    resolved_path = str(p)
    # 1. Try opening directly with up to 3 retries (in case file is briefly locked by writer/antivirus)
    for attempt in range(3):
        cap = cv2.VideoCapture(resolved_path)
        if cap.isOpened():
            ret, frame = cap.read()
            if ret and frame is not None:
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                return cap, resolved_path
            cap.release()
        time.sleep(0.25)

    # 2. Fallback: Fast remux / transcode to a clean temporary MP4 via ffmpeg
    try:
        ffmpeg_bin = get_ffmpeg_path()
        tmp_mp4 = tempfile.NamedTemporaryFile(suffix="_compat.mp4", delete=False)
        tmp_mp4.close()
        cmd = [
            ffmpeg_bin, "-y",
            "-i", resolved_path,
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "22",
            "-c:a", "aac", "-b:a", "128k",
            tmp_mp4.name
        ]
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=flags)
        if proc.returncode == 0 and Path(tmp_mp4.name).exists() and Path(tmp_mp4.name).stat().st_size > 1024:
            cap = cv2.VideoCapture(tmp_mp4.name)
            if cap.isOpened():
                return cap, tmp_mp4.name
    except Exception as e:
        print(f"Fallback video transcode error: {e}")

    return None, resolved_path


class VideoEditorWindow(ctk.CTkToplevel):
    """
    Interactive Video Editing & Trimming Window.
    Features:
    - Real-time OpenCV video preview with audio playback (pygame).
    - Mute/unmute audio toggle synced with playhead.
    - Improved scissors tool: timeline click-to-cut, undo, keyboard shortcuts,
      visual cut markers, scissors mode toggle.
    - Smart crop with face/body subject tracking.
    - Aspect ratios (9:16 Shorts/Reels, 4:5 Instagram Feed, 1:1 Square).
    - TikTok/Instagram viral video effects (Zoom-Punch, White Flash, Pop CC, Vignette, Sharpen, Bass Boost).
    - Filmstrip storyboard with click-to-seek.
    - Segment manager with keep/cut per-segment.
    - Final multi-segment render pipeline.
    """

    def __init__(
        self,
        parent,
        video_path: str,
        initial_subtitles: str = "",
        initial_hook: str = "",
        output_dir: str = "output_shorts",
        on_render_complete: Optional[Callable[[str], None]] = None,
    ):
        super().__init__(parent)

        self.video_path = str(Path(video_path).resolve())
        self.output_dir = output_dir
        self.on_render_complete = on_render_complete

        self.title("VideoHook — Интерактивный видеомонтаж и ножницы (9:16 / 4:5 / 1:1)")
        self.geometry("1220x920")
        self.minsize(1050, 780)

        self.cap, self.video_path = _safe_open_video(self.video_path)
        if not self.cap or not self.cap.isOpened():
            messagebox.showerror("Ошибка загрузки", f"Не удалось открыть видеофайл:\n{video_path}\nПроверьте целостность файла.")
            self.destroy()
            return

        self.fps = self.cap.get(cv2.CAP_PROP_FPS) or 30.0
        self.total_frames = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        self.duration = (self.total_frames / self.fps) if self.fps > 0 else 120.0
        if self.duration <= 0:
            self.duration = 120.0

        # Player state
        self.current_time = 0.0
        self.is_playing = False
        self.play_thread: Optional[threading.Thread] = None
        self.is_rendering = False

        # Audio state
        self.audio_enabled = True
        self.video_volume: float = 0.80  # 0.0 - 1.0 (default 80%, Stage 4A)
        self._audio_wav_path: Optional[str] = None
        self._audio_play_start_wall: float = 0.0
        self._audio_play_start_t: float = 0.0
        self._audio_ready = False

        # Scissors / segments state
        self.segments: List[Dict[str, Any]] = [
            {"start": 0.0, "end": self.duration, "keep": True}
        ]
        self.selected_segment_idx: Optional[int] = 0
        self.selected_segment_indices: set = set()
        self._last_selected_segment_idx: Optional[int] = None
        self.segments_page: int = 0
        self._scissors_mode = False
        self._cut_undo_stack: List[List[Dict[str, Any]]] = []
        self._cut_redo_stack: List[List[Dict[str, Any]]] = []
        self._canvas_hover_x: Optional[int] = None

        # CapCut style selection & drag handles state (Stage 2)
        self._drag_select_active = False
        self._drag_select_start_x: Optional[int] = None
        self._drag_select_current_x: Optional[int] = None
        self._dragging_handle_boundary: Optional[float] = None
        self._dragging_handle_side: Optional[str] = None
        self._hovered_handle_boundary: Optional[float] = None

        # Zoom & Pan state (Stage 3)
        self.zoom_levels = [1.0, 1.5, 2.0, 3.0, 5.0, 7.0, 10.0, 15.0, 20.0]
        self.zoom_idx = 0
        self.zoom_factor = 1.0
        self.view_offset_sec = 0.0  # leftmost timestamp visible on canvas
        self._is_panning = False
        self._pan_start_x: Optional[int] = None
        self._pan_start_offset: float = 0.0

        # Smart crop state
        self._smart_crop_keyframes: Optional[List[Tuple[float, int]]] = None
        self._smart_crop_src_w: int = 0
        self._smart_crop_src_h: int = 0
        self._smart_crop_analyzing = False

        # Filmstrip cache
        self.filmstrip_images: List[Tuple[float, Any]] = []

        self._build_ui(initial_subtitles, initial_hook)
        self.after(100, lambda: self.seek_to(0.0))
        threading.Thread(target=self._generate_filmstrip, daemon=True).start()
        threading.Thread(target=self._extract_audio_async, daemon=True).start()

        # Keyboard shortcuts (Stage 6E)
        self.bind("<space>", lambda e: self._toggle_play())
        self.bind("<s>", lambda e: self._cut_at_current_time())
        self.bind("<S>", lambda e: self._cut_at_current_time())
        self.bind("<Control-k>", lambda e: self._cut_at_current_time())
        self.bind("<Control-K>", lambda e: self._cut_at_current_time())
        self.bind("<Control-z>", lambda e: self._undo_cut())
        self.bind("<Control-Z>", lambda e: self._undo_cut())
        self.bind("<Control-y>", lambda e: self._redo_cut())
        self.bind("<Control-Y>", lambda e: self._redo_cut())
        self.bind("<Control-a>", lambda e: self._select_all_segments())
        self.bind("<Control-A>", lambda e: self._select_all_segments())
        self.bind("<Control-s>", lambda e: self._on_start_final_render())
        self.bind("<Control-S>", lambda e: self._on_start_final_render())
        self.bind("<Delete>", lambda e: self._delete_selected_segment())
        self.bind("<m>", lambda e: self._toggle_audio())
        self.bind("<M>", lambda e: self._toggle_audio())
        self.bind("<question>", lambda e: show_hotkeys_dialog(self))
        self.bind("<Left>", lambda e: self.seek_relative(-1.0))
        self.bind("<Right>", lambda e: self.seek_relative(1.0))
        # Zoom shortcuts (Stage 3E)
        self.bind("<plus>", lambda e: self._zoom_step(1))
        self.bind("<equal>", lambda e: self._zoom_step(1))
        self.bind("<minus>", lambda e: self._zoom_step(-1))
        self.bind("<0>", lambda e: self._zoom_fit())
        self.bind("<Home>", lambda e: self._scroll_home())
        self.bind("<End>", lambda e: self._scroll_end())

        for num in range(1, 10):
            self.bind(str(num), lambda e, n=num: self._jump_to_segment_num(n))

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # =========================================================================
    # AUDIO EXTRACTION & PLAYBACK (pygame)
    # =========================================================================
    def _extract_audio_async(self):
        """Extract audio track to temp WAV for pygame playback."""
        if not _PYGAME_OK:
            return
        try:
            tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
            tmp.close()
            self._audio_wav_path = tmp.name

            ffmpeg_bin = get_ffmpeg_path()
            cmd = [
                ffmpeg_bin, "-y",
                "-i", self.video_path,
                "-vn",
                "-acodec", "pcm_s16le",
                "-ar", "44100",
                "-ac", "2",
                self._audio_wav_path,
            ]
            proc = _silent_popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            proc.communicate()
            if proc.returncode == 0:
                pygame.mixer.music.load(self._audio_wav_path)
                self._audio_ready = True
        except Exception as e:
            print(f"Audio extraction warning: {e}")
            self._audio_ready = False

    def _audio_play_from(self, t: float):
        """Start pygame audio from given timestamp with current volume setting (Stage 4A)."""
        if not _PYGAME_OK or not self._audio_ready or not self.audio_enabled:
            return
        try:
            pygame.mixer.music.stop()
            pygame.mixer.music.load(self._audio_wav_path)
            pygame.mixer.music.set_volume(self.video_volume)
            pygame.mixer.music.play(start=t)
            self._audio_play_start_wall = time.time()
            self._audio_play_start_t = t
        except Exception as e:
            print(f"Audio play warning: {e}")

    def _audio_stop(self):
        if not _PYGAME_OK:
            return
        try:
            pygame.mixer.music.stop()
        except Exception:
            pass

    def _on_volume_changed(self, value):
        """Instant volume adjustment for preview playback (Stage 4A)."""
        self.video_volume = float(value)
        if hasattr(self, "preview_vol_lbl"):
            self.preview_vol_lbl.configure(text=f"{int(self.video_volume * 100)}%")
        if _PYGAME_OK and self.audio_enabled:
            try:
                pygame.mixer.music.set_volume(self.video_volume)
            except Exception:
                pass

    def _toggle_audio(self):
        """Mute/unmute toggle button (Stage 4C)."""
        self.audio_enabled = not self.audio_enabled
        if self.audio_enabled:
            self.mute_btn.configure(text="🔊 Звук ВКЛ", fg_color="#065f46")
            if hasattr(self, "preview_vol_slider"):
                self.preview_vol_slider.configure(state="normal")
            if self.is_playing:
                self._audio_play_from(self.current_time)
        else:
            self.mute_btn.configure(text="🔇 Без звука", fg_color="#4b5563")
            if hasattr(self, "preview_vol_slider"):
                self.preview_vol_slider.configure(state="disabled")
            self._audio_stop()

    # =========================================================================
    # UI CONSTRUCTION
    # =========================================================================
    def _build_ui(self, initial_subtitles: str, initial_hook: str):
        self.grid_columnconfigure(0, weight=6)
        self.grid_columnconfigure(1, weight=4)
        self.grid_rowconfigure(0, weight=1)

        # =====================================================================
        # LEFT COLUMN
        # =====================================================================
        left_frame = ctk.CTkFrame(self, corner_radius=10)
        left_frame.grid(row=0, column=0, sticky="nsew", padx=(15, 8), pady=15)
        left_frame.grid_columnconfigure(0, weight=1)
        for r in range(6):
            left_frame.grid_rowconfigure(r, weight=0)
        left_frame.grid_rowconfigure(4, weight=1)

        # 1. Video Preview
        preview_box = ctk.CTkFrame(left_frame, fg_color="#18181b", corner_radius=8)
        preview_box.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 6))
        preview_box.grid_columnconfigure(0, weight=1)

        self.preview_lbl = ctk.CTkLabel(
            preview_box,
            text="Загрузка кадра...",
            font=ctk.CTkFont(size=14),
            width=540,
            height=304,
            fg_color="#09090b",
            corner_radius=6,
        )
        self.preview_lbl.grid(row=0, column=0, padx=10, pady=10)

        # 2. Scrubber
        scrub_frame = ctk.CTkFrame(left_frame, fg_color="transparent")
        scrub_frame.grid(row=1, column=0, sticky="ew", padx=12, pady=4)
        scrub_frame.grid_columnconfigure(1, weight=1)

        self.time_lbl = ctk.CTkLabel(
            scrub_frame,
            text=f"00:00.0 / {format_time(self.duration)}",
            font=ctk.CTkFont(family="Consolas", size=13, weight="bold"),
            text_color="#38bdf8",
        )
        self.time_lbl.grid(row=0, column=0, padx=(0, 10))

        self.timeline_slider = ctk.CTkSlider(
            scrub_frame,
            from_=0.0,
            to=self.duration,
            number_of_steps=int(self.duration * 10),
            command=self._on_slider_scrub,
        )
        self.timeline_slider.set(0.0)
        self.timeline_slider.grid(row=0, column=1, sticky="ew")

        # 3. Playback Controls Row
        ctrl_bar = ctk.CTkFrame(left_frame, fg_color="transparent")
        ctrl_bar.grid(row=2, column=0, sticky="ew", padx=12, pady=4)

        self.play_btn = ctk.CTkButton(
            ctrl_bar,
            text="▶ Воспроизвести",
            width=145,
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color="#2563eb",
            hover_color="#1d4ed8",
            command=self._toggle_play,
        )
        self.play_btn.pack(side="left", padx=(0, 4))

        ctk.CTkButton(
            ctrl_bar, text="⏪ −1с", width=60,
            command=lambda: self.seek_relative(-1.0)
        ).pack(side="left", padx=2)

        ctk.CTkButton(
            ctrl_bar, text="+1с ⏩", width=60,
            command=lambda: self.seek_relative(1.0)
        ).pack(side="left", padx=2)

        self.mute_btn = ctk.CTkButton(
            ctrl_bar,
            text="🔊 Звук ВКЛ",
            width=100,
            font=ctk.CTkFont(size=12),
            fg_color="#065f46",
            hover_color="#047857",
            command=self._toggle_audio,
        )
        self.mute_btn.pack(side="left", padx=(6, 4))
        if not _PYGAME_OK:
            self.mute_btn.configure(state="disabled", text="🔇 Нет звука", fg_color="#374151")

        # Video preview volume slider (Stage 4A)
        self.preview_vol_slider = ctk.CTkSlider(
            ctrl_bar, from_=0.0, to=1.0, width=100,
            command=self._on_volume_changed
        )
        self.preview_vol_slider.set(self.video_volume)
        self.preview_vol_slider.pack(side="left", padx=(2, 4))

        self.preview_vol_lbl = ctk.CTkLabel(
            ctrl_bar,
            text=f"{int(self.video_volume * 100)}%",
            font=ctk.CTkFont(family="Consolas", size=11, weight="bold"),
            text_color="#94a3b8",
            width=36,
        )
        self.preview_vol_lbl.pack(side="left")

        # 4. Scissors Toolbar
        scissors_frame = ctk.CTkFrame(left_frame, fg_color="#111827", corner_radius=8)
        scissors_frame.grid(row=3, column=0, sticky="ew", padx=12, pady=(4, 2))
        scissors_frame.grid_columnconfigure(1, weight=1)

        hint_lbl = ctk.CTkLabel(
            scissors_frame,
            text="✂️  Инструмент нарезки:  Поставьте плейхед на точку разреза → нажмите  ✂ РАЗРЕЗАТЬ  (или клавиша S / Ctrl+K).  Отмена: Ctrl+Z",
            font=ctk.CTkFont(size=11),
            text_color="#94a3b8",
            wraplength=480,
            justify="left",
        )
        hint_lbl.grid(row=0, column=0, columnspan=4, sticky="w", padx=10, pady=(6, 2))

        self.scissors_mode_btn = ctk.CTkButton(
            scissors_frame,
            text="✂️ Режим ножниц",
            width=140,
            height=36,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="#374151",
            hover_color="#4b5563",
            command=self._toggle_scissors_mode,
        )
        self.scissors_mode_btn.grid(row=1, column=0, padx=(10, 3), pady=(4, 8))

        self.scissors_btn = ctk.CTkButton(
            scissors_frame,
            text="✂️ РАЗРЕЗАТЬ",
            width=135,
            height=36,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="#dc2626",
            hover_color="#b91c1c",
            command=self._cut_at_current_time,
        )
        self.scissors_btn.grid(row=1, column=1, padx=3, pady=(4, 8))

        self.undo_btn = ctk.CTkButton(
            scissors_frame,
            text="↩ Отмена",
            width=90,
            height=36,
            font=ctk.CTkFont(size=12),
            fg_color="#4b5563",
            hover_color="#374151",
            command=self._undo_cut,
        )
        self.undo_btn.grid(row=1, column=2, padx=3, pady=(4, 8))

        self.redo_btn = ctk.CTkButton(
            scissors_frame,
            text="↷ Повтор",
            width=90,
            height=36,
            font=ctk.CTkFont(size=12),
            fg_color="#4b5563",
            hover_color="#374151",
            command=self._redo_cut,
        )
        self.redo_btn.grid(row=1, column=3, padx=3, pady=(4, 8))

        reset_btn = ctk.CTkButton(
            scissors_frame,
            text="↺ Сброс",
            width=85,
            height=36,
            font=ctk.CTkFont(size=12),
            fg_color="#4b5563",
            hover_color="#374151",
            command=self._reset_segments,
        )
        reset_btn.grid(row=1, column=4, padx=3, pady=(4, 8))

        help_btn = ctk.CTkButton(
            scissors_frame,
            text="⌨️ Справка (?)",
            width=100,
            height=36,
            font=ctk.CTkFont(size=11),
            fg_color="#0284c7",
            hover_color="#0369a1",
            command=lambda: show_hotkeys_dialog(self),
        )
        help_btn.grid(row=1, column=5, padx=(3, 10), pady=(4, 8))

        # 5. Filmstrip
        filmstrip_card = ctk.CTkFrame(left_frame, corner_radius=8)
        filmstrip_card.grid(row=4, column=0, sticky="ew", padx=12, pady=4)
        filmstrip_card.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            filmstrip_card,
            text="🎞 Видеоряд раскадровки (кликните по кадру для перехода):",
            font=ctk.CTkFont(size=12, weight="bold"),
        ).grid(row=0, column=0, sticky="w", padx=8, pady=(6, 2))

        self.filmstrip_scroll = ctk.CTkScrollableFrame(
            filmstrip_card, orientation="horizontal", height=84, fg_color="#111827"
        )
        self.filmstrip_scroll.grid(row=1, column=0, sticky="ew", padx=6, pady=(0, 8))

        self.filmstrip_status_lbl = ctk.CTkLabel(
            self.filmstrip_scroll,
            text="Генерация миниатюр...",
            font=ctk.CTkFont(size=11),
            text_color="gray70",
        )
        self.filmstrip_status_lbl.pack(padx=20, pady=20)

        # 6. Interactive Timeline Canvas with Zoom Toolbar (Stage 3C)
        timeline_card = ctk.CTkFrame(left_frame, corner_radius=8)
        timeline_card.grid(row=5, column=0, sticky="nsew", padx=12, pady=(4, 12))
        timeline_card.grid_columnconfigure(0, weight=1)

        # Header with title & Zoom Toolbar [🔍−] [🔍+] [Fit] [1:1] ×1.0
        tl_header = ctk.CTkFrame(timeline_card, fg_color="transparent")
        tl_header.grid(row=0, column=0, sticky="ew", padx=10, pady=(6, 2))
        tl_header.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            tl_header,
            text="📊 Шкала монтажа (Колёсико=Zoom, Shift+Колёсико=Скролл)",
            font=ctk.CTkFont(size=11, weight="bold"),
        ).grid(row=0, column=0, sticky="w")

        zoom_bar = ctk.CTkFrame(tl_header, fg_color="transparent")
        zoom_bar.grid(row=0, column=1, sticky="e")

        ctk.CTkButton(
            zoom_bar, text="🔍−", width=36, height=24, font=ctk.CTkFont(size=11),
            command=lambda: self._zoom_step(-1)
        ).pack(side="left", padx=2)

        ctk.CTkButton(
            zoom_bar, text="🔍+", width=36, height=24, font=ctk.CTkFont(size=11),
            command=lambda: self._zoom_step(1)
        ).pack(side="left", padx=2)

        ctk.CTkButton(
            zoom_bar, text="Fit", width=36, height=24, font=ctk.CTkFont(size=11),
            command=self._zoom_fit
        ).pack(side="left", padx=2)

        ctk.CTkButton(
            zoom_bar, text="1:1", width=36, height=24, font=ctk.CTkFont(size=11),
            command=self._zoom_one_to_one
        ).pack(side="left", padx=2)

        self.zoom_lbl = ctk.CTkLabel(
            zoom_bar, text="×1.0", font=ctk.CTkFont(family="Consolas", size=11, weight="bold"),
            text_color="#38bdf8", width=42
        )
        self.zoom_lbl.pack(side="left", padx=(4, 0))

        self.timeline_canvas = tk.Canvas(
            timeline_card, height=58, bg="#1f2937", highlightthickness=0
        )
        self.timeline_canvas.grid(row=1, column=0, sticky="ew", padx=10, pady=4)
        self.timeline_canvas.bind("<Button-1>", self._on_canvas_click)
        self.timeline_canvas.bind("<B1-Motion>", self._on_canvas_drag)
        self.timeline_canvas.bind("<ButtonRelease-1>", self._on_canvas_release)
        self.timeline_canvas.bind("<Double-Button-1>", self._on_canvas_double_click)
        self.timeline_canvas.bind("<Motion>", self._on_canvas_hover)
        self.timeline_canvas.bind("<Leave>", self._on_canvas_leave)
        # Mousewheel Zoom & Scroll (Stage 3A & 3B)
        self.timeline_canvas.bind("<MouseWheel>", self._on_canvas_mousewheel)
        self.timeline_canvas.bind("<Shift-MouseWheel>", self._on_canvas_shift_mousewheel)
        self.timeline_canvas.bind("<Button-2>", self._on_pan_start)
        self.timeline_canvas.bind("<B2-Motion>", self._on_pan_motion)
        self.timeline_canvas.bind("<ButtonRelease-2>", self._on_pan_release)

        legend_row = ctk.CTkFrame(timeline_card, fg_color="transparent")
        legend_row.grid(row=2, column=0, sticky="ew", padx=10, pady=(2, 6))

        self.kept_time_lbl = ctk.CTkLabel(
            legend_row,
            text=f"Итоговая длительность: {int(self.duration)} сек (1 фрагмент)",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#10b981",
        )
        self.kept_time_lbl.pack(side="left")

        # =====================================================================
        # RIGHT COLUMN
        # =====================================================================
        right_frame = ctk.CTkScrollableFrame(
            self, corner_radius=10, label_text="Нарезка & Финальный монтаж"
        )
        right_frame.grid(row=0, column=1, sticky="nsew", padx=(8, 15), pady=15)
        right_frame.grid_columnconfigure(0, weight=1)

        # 0. Effects Indicator Panel (Stage 1C)
        effects_card = ctk.CTkFrame(right_frame, corner_radius=8, fg_color="#1e1e24")
        effects_card.grid(row=0, column=0, sticky="ew", padx=5, pady=(2, 6))
        effects_card.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            effects_card,
            text="⚡ Применённые эффекты:",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color="#38bdf8"
        ).grid(row=0, column=0, sticky="w", padx=10, pady=(6, 2))

        self.effects_status_lbl = ctk.CTkLabel(
            effects_card,
            text="Загрузка эффектов...",
            font=ctk.CTkFont(family="Consolas", size=11),
            justify="left",
            text_color="#e2e8f0"
        )
        self.effects_status_lbl.grid(row=1, column=0, sticky="w", padx=12, pady=(0, 6))

        # A. Segments list
        seg_box = ctk.CTkFrame(right_frame, corner_radius=8)
        seg_box.grid(row=1, column=0, sticky="ew", padx=5, pady=5)
        seg_box.grid_columnconfigure(0, weight=1)

        seg_header = ctk.CTkFrame(seg_box, fg_color="transparent")
        seg_header.grid(row=0, column=0, sticky="ew", padx=10, pady=(8, 4))
        seg_header.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            seg_header, text="✂️ Фрагменты:", font=ctk.CTkFont(size=13, weight="bold")
        ).pack(side="left")

        self.batch_export_btn = ctk.CTkButton(
            seg_header,
            text="📦 Экспорт выбранных",
            width=145,
            height=26,
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color="#0284c7",
            hover_color="#0369a1",
            command=self._on_batch_export,
        )
        self.batch_export_btn.pack(side="right", padx=(4, 0))

        self.select_all_btn = ctk.CTkButton(
            seg_header,
            text="✓ Все (Ctrl+A)",
            width=88,
            height=26,
            font=ctk.CTkFont(size=11),
            fg_color="#374151",
            hover_color="#4b5563",
            command=self._select_all_segments,
        )
        self.select_all_btn.pack(side="right")

        self.segments_scroll = ctk.CTkScrollableFrame(seg_box, height=170, label_text="Отрезки сцены")
        self.segments_scroll.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 8))
        self.segments_scroll.grid_columnconfigure(0, weight=1)

        # B. Smart Crop section
        crop_box = ctk.CTkFrame(right_frame, corner_radius=8, fg_color="#0f172a")
        crop_box.grid(row=2, column=0, sticky="ew", padx=5, pady=8)
        crop_box.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            crop_box,
            text="🎯 Умная обрезка 9:16 с привязкой к персонажу",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color="#a78bfa",
        ).grid(row=0, column=0, sticky="w", padx=10, pady=(8, 2))

        ctk.CTkLabel(
            crop_box,
            text="Анализирует позицию лица/тела персонажа покадрово\n"
                 "и автоматически сдвигает окно кадрирования 9:16 за ним.",
            font=ctk.CTkFont(size=11),
            text_color="#94a3b8",
            justify="left",
        ).grid(row=1, column=0, sticky="w", padx=10, pady=(0, 6))

        crop_btn_row = ctk.CTkFrame(crop_box, fg_color="transparent")
        crop_btn_row.grid(row=2, column=0, sticky="ew", padx=10, pady=(0, 6))

        self.smart_crop_btn = ctk.CTkButton(
            crop_btn_row,
            text="🎯 Анализировать и включить умную обрезку",
            height=36,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="#7c3aed",
            hover_color="#6d28d9",
            command=self._start_smart_crop_analysis,
        )
        self.smart_crop_btn.pack(side="left", padx=(0, 6))

        self.smart_crop_clear_btn = ctk.CTkButton(
            crop_btn_row,
            text="✕ Отключить",
            width=90,
            height=36,
            font=ctk.CTkFont(size=11),
            fg_color="#4b5563",
            hover_color="#374151",
            command=self._clear_smart_crop,
        )
        self.smart_crop_clear_btn.pack(side="left")

        self.smart_crop_status = ctk.CTkLabel(
            crop_box,
            text="Статус: не активна (будет использоваться центральная обрезка по умолчанию)",
            font=ctk.CTkFont(size=11),
            text_color="#6b7280",
        )
        self.smart_crop_status.grid(row=3, column=0, sticky="w", padx=10, pady=(0, 8))

        # C. Styling settings
        style_box = ctk.CTkFrame(right_frame, corner_radius=8)
        style_box.grid(row=3, column=0, sticky="ew", padx=5, pady=8)
        style_box.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            style_box, text="🎨 Настройки оформления и формата", font=ctk.CTkFont(size=13, weight="bold")
        ).grid(row=0, column=0, sticky="w", padx=10, pady=(8, 4))

        # Aspect Ratio Row (9:16, 4:5, 1:1)
        ctk.CTkLabel(style_box, text="Соотношение сторон (Формат):", font=ctk.CTkFont(size=11, weight="bold")).grid(
            row=1, column=0, sticky="w", padx=10, pady=(4, 2)
        )
        self.aspect_ratio_var = ctk.StringVar(value="9:16")
        ar_row = ctk.CTkFrame(style_box, fg_color="transparent")
        ar_row.grid(row=2, column=0, sticky="w", padx=10, pady=2)
        ctk.CTkRadioButton(
            ar_row, text="📱 9:16 Shorts/Reels", variable=self.aspect_ratio_var, value="9:16",
            command=self._update_effects_indicator
        ).pack(side="left", padx=(0, 10))
        ctk.CTkRadioButton(
            ar_row, text="📸 4:5 Insta Feed", variable=self.aspect_ratio_var, value="4:5",
            command=self._update_effects_indicator
        ).pack(side="left", padx=(0, 10))
        ctk.CTkRadioButton(
            ar_row, text="🔲 1:1 Квадрат", variable=self.aspect_ratio_var, value="1:1",
            command=self._update_effects_indicator
        ).pack(side="left")

        # Crop / Layout Mode Row
        ctk.CTkLabel(style_box, text="Режим кадрирования:", font=ctk.CTkFont(size=11, weight="bold")).grid(
            row=3, column=0, sticky="w", padx=10, pady=(6, 2)
        )
        self.layout_var = ctk.StringVar(value="fullscreen")
        layout_row = ctk.CTkFrame(style_box, fg_color="transparent")
        layout_row.grid(row=4, column=0, sticky="w", padx=10, pady=2)
        ctk.CTkRadioButton(
            layout_row, text="✂️ Fullscreen (Кроп)", variable=self.layout_var, value="fullscreen",
            command=self._update_effects_indicator
        ).pack(side="left", padx=(0, 10))
        ctk.CTkRadioButton(
            layout_row, text="🌌 Blurscreen (Размытый фон)", variable=self.layout_var, value="blurscreen",
            command=self._update_effects_indicator
        ).pack(side="left")

        ctk.CTkLabel(style_box, text="💬 Русские субтитры:", font=ctk.CTkFont(size=11, weight="bold")).grid(
            row=5, column=0, sticky="w", padx=10, pady=(8, 2)
        )
        self.subs_entry = ctk.CTkEntry(
            style_box, placeholder_text="Текст субтитров внизу кадра", height=34
        )
        if initial_subtitles:
            self.subs_entry.insert(0, initial_subtitles)
        self.subs_entry.grid(row=6, column=0, sticky="ew", padx=10, pady=2)
        self.subs_entry.bind("<KeyRelease>", lambda e: self._update_effects_indicator())

        ctk.CTkLabel(style_box, text="🎵 Фоновая музыка:", font=ctk.CTkFont(size=11, weight="bold")).grid(
            row=7, column=0, sticky="w", padx=10, pady=(8, 2)
        )
        self.music_dropdown = ctk.CTkOptionMenu(
            style_box,
            values=[
                "Без фоновой музыки (Оригинальная озвучка)",
                "Phonk (Вирусный бит)",
                "Epic Orchestral (Кульминация)",
                "Lo-Fi Chill (Атмосферный)",
                "Свой аудиофайл...",
            ],
            command=self._on_music_changed,
        )
        self.music_dropdown.set("Phonk (Вирусный бит)")
        self.music_dropdown.grid(row=8, column=0, sticky="ew", padx=10, pady=2)

        # Dual Audio Balance Sliders (Stage 4B)
        vol_row = ctk.CTkFrame(style_box, fg_color="transparent")
        vol_row.grid(row=9, column=0, sticky="ew", padx=10, pady=(4, 2))
        vol_row.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(vol_row, text="🎵 Музыка:", font=ctk.CTkFont(size=11)).grid(
            row=0, column=0, sticky="w", padx=(0, 6)
        )
        self.vol_lbl = ctk.CTkLabel(vol_row, text="28%", font=ctk.CTkFont(size=11, weight="bold"))
        self.vol_lbl.grid(row=0, column=2, sticky="e", padx=(6, 0))
        self.music_vol_slider = ctk.CTkSlider(
            vol_row, from_=0.05, to=1.0,
            command=lambda val: self.vol_lbl.configure(text=f"{int(val * 100)}%")
        )
        self.music_vol_slider.set(0.28)
        self.music_vol_slider.grid(row=0, column=1, sticky="ew")

        ctk.CTkLabel(vol_row, text="💬 Диалоги:", font=ctk.CTkFont(size=11)).grid(
            row=1, column=0, sticky="w", padx=(0, 6), pady=(4, 0)
        )
        self.dialog_vol_lbl = ctk.CTkLabel(vol_row, text="80%", font=ctk.CTkFont(size=11, weight="bold"))
        self.dialog_vol_lbl.grid(row=1, column=2, sticky="e", padx=(6, 0), pady=(4, 0))
        self.dialog_vol_slider = ctk.CTkSlider(
            vol_row, from_=0.1, to=1.5,
            command=lambda val: self.dialog_vol_lbl.configure(text=f"{int(val * 100)}%")
        )
        self.dialog_vol_slider.set(0.80)
        self.dialog_vol_slider.grid(row=1, column=1, sticky="ew", pady=(4, 0))

        ctk.CTkLabel(style_box, text="🔥 Вирусный заголовок (Сверху):", font=ctk.CTkFont(size=11, weight="bold")).grid(
            row=10, column=0, sticky="w", padx=10, pady=(8, 2)
        )
        self.hook_entry = ctk.CTkEntry(style_box, placeholder_text="САМЫЙ ЭПИЧНЫЙ МОМЕНТ 😱", height=32)
        if initial_hook:
            self.hook_entry.insert(0, initial_hook)
        self.hook_entry.grid(row=11, column=0, sticky="ew", padx=10, pady=2)
        self.hook_entry.bind("<KeyRelease>", lambda e: self._update_effects_indicator())

        ctk.CTkLabel(style_box, text="🏷 Водяной знак / Призыв к действию:", font=ctk.CTkFont(size=11, weight="bold")).grid(
            row=12, column=0, sticky="w", padx=10, pady=(8, 2)
        )
        self.cta_entry = ctk.CTkEntry(style_box, placeholder_text="Смотри полное аниме в профиле", height=32)
        self.cta_entry.insert(0, "Смотри полное аниме в профиле")
        self.cta_entry.grid(row=13, column=0, sticky="ew", padx=10, pady=(2, 10))

        # D. Viral Effects Box (TikTok / Reels / Instagram)
        viral_box = ctk.CTkFrame(right_frame, corner_radius=8, fg_color="#18181b")
        viral_box.grid(row=4, column=0, sticky="ew", padx=5, pady=8)
        viral_box.grid_columnconfigure(0, weight=1)

        v_head = ctk.CTkFrame(viral_box, fg_color="transparent")
        v_head.grid(row=0, column=0, sticky="ew", padx=10, pady=(8, 4))
        ctk.CTkLabel(
            v_head, text="⚡ Вирусные эффекты TikTok / Reels",
            font=ctk.CTkFont(size=13, weight="bold"), text_color="#f59e0b"
        ).pack(side="left")

        self.viral_preset_var = ctk.StringVar(value="🔥 TikTok Ultra")
        self.viral_preset_menu = ctk.CTkOptionMenu(
            v_head,
            values=["🔥 TikTok Ultra", "⚡ Zoom & Flash", "🎨 Vibrant Pop", "❌ Без эффектов"],
            width=135,
            height=26,
            variable=self.viral_preset_var,
            command=self._on_viral_preset_changed,
        )
        self.viral_preset_menu.pack(side="right")

        fx_grid = ctk.CTkFrame(viral_box, fg_color="transparent")
        fx_grid.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 8))
        fx_grid.grid_columnconfigure((0, 1), weight=1)

        self.viral_zoom_var = ctk.BooleanVar(value=True)
        self.viral_zoom_cb = ctk.CTkCheckBox(
            fx_grid, text="🎬 Zoom-Punch (зум-хук 1.5с)", variable=self.viral_zoom_var,
            font=ctk.CTkFont(size=11), command=self._update_effects_indicator
        )
        self.viral_zoom_cb.grid(row=0, column=0, sticky="w", pady=3)

        self.viral_flash_var = ctk.BooleanVar(value=True)
        self.viral_flash_cb = ctk.CTkCheckBox(
            fx_grid, text="⚡ White Flash (вспышка на старте)", variable=self.viral_flash_var,
            font=ctk.CTkFont(size=11), command=self._update_effects_indicator
        )
        self.viral_flash_cb.grid(row=0, column=1, sticky="w", pady=3)

        self.viral_cc_var = ctk.BooleanVar(value=True)
        self.viral_cc_cb = ctk.CTkCheckBox(
            fx_grid, text="🎨 Сочный цветокор (Anime Pop)", variable=self.viral_cc_var,
            font=ctk.CTkFont(size=11), command=self._update_effects_indicator
        )
        self.viral_cc_cb.grid(row=1, column=0, sticky="w", pady=3)

        self.viral_vignette_var = ctk.BooleanVar(value=True)
        self.viral_vignette_cb = ctk.CTkCheckBox(
            fx_grid, text="👁️ Кинематографичная виньетка", variable=self.viral_vignette_var,
            font=ctk.CTkFont(size=11), command=self._update_effects_indicator
        )
        self.viral_vignette_cb.grid(row=1, column=1, sticky="w", pady=3)

        self.viral_sharpen_var = ctk.BooleanVar(value=True)
        self.viral_sharpen_cb = ctk.CTkCheckBox(
            fx_grid, text="🔍 HD Чёткость контуров", variable=self.viral_sharpen_var,
            font=ctk.CTkFont(size=11), command=self._update_effects_indicator
        )
        self.viral_sharpen_cb.grid(row=2, column=0, sticky="w", pady=3)

        self.viral_bass_var = ctk.BooleanVar(value=True)
        self.viral_bass_cb = ctk.CTkCheckBox(
            fx_grid, text="🔊 Phonk/Reels Bass Boost", variable=self.viral_bass_var,
            font=ctk.CTkFont(size=11), command=self._update_effects_indicator
        )
        self.viral_bass_cb.grid(row=2, column=1, sticky="w", pady=3)

        # E. Render & Preview Actions
        render_box = ctk.CTkFrame(right_frame, fg_color="#18181b", corner_radius=8)
        render_box.grid(row=5, column=0, sticky="ew", padx=5, pady=8)
        render_box.grid_columnconfigure(0, weight=1)
        render_box.grid_columnconfigure(1, weight=1)

        self.preview_render_btn = ctk.CTkButton(
            render_box,
            text="👁 Предпросмотр готового",
            height=42,
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color="#2563eb",
            hover_color="#1d4ed8",
            command=self._on_preview_rendered_clip,
        )
        self.preview_render_btn.grid(row=0, column=0, columnspan=2, sticky="ew", padx=10, pady=(10, 4))

        self.render_btn = ctk.CTkButton(
            render_box,
            text="💾 СОХРАНИТЬ РОЛИК (РЕКВ. РЕНДЕР)",
            height=44,
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color="#10b981",
            hover_color="#059669",
            command=self._on_start_final_render,
        )
        self.render_btn.grid(row=1, column=0, columnspan=2, sticky="ew", padx=10, pady=(4, 6))

        self.render_progress = ctk.CTkProgressBar(render_box)
        self.render_progress.set(0.0)
        self.render_progress.grid(row=2, column=0, columnspan=2, sticky="ew", padx=10, pady=4)

        self.render_status_lbl = ctk.CTkLabel(
            render_box,
            text="Готов к созданию шортса",
            font=ctk.CTkFont(size=11),
            text_color="gray70",
        )
        self.render_status_lbl.grid(row=3, column=0, columnspan=2, sticky="w", padx=12, pady=(0, 10))

        self._preview_temp_file: Optional[str] = None
        self._refresh_segments_ui()
        self._update_effects_indicator()

    # =========================================================================
    # PLAYER
    # =========================================================================
    def seek_to(self, timestamp: float):
        self.current_time = max(0.0, min(self.duration, timestamp))
        self.timeline_slider.set(self.current_time)
        self.time_lbl.configure(
            text=f"{format_time(self.current_time)} / {format_time(self.duration)}"
        )
        if self.cap and self.cap.isOpened():
            self.cap.set(cv2.CAP_PROP_POS_MSEC, self.current_time * 1000)
            ret, frame = self.cap.read()
            if ret:
                self._display_frame(frame)
        self._ensure_playhead_visible()
        self._draw_timeline_canvas()

    def seek_relative(self, delta: float):
        self.seek_to(self.current_time + delta)

    def _on_slider_scrub(self, value):
        was_playing = self.is_playing
        if was_playing:
            self._pause()
        self.seek_to(float(value))
        if was_playing:
            self._audio_play_from(self.current_time)

    def _toggle_play(self):
        if self.is_playing:
            self._pause()
        else:
            self._play()

    def _play(self):
        if self.current_time >= self.duration - 0.2:
            self.seek_to(0.0)
        self.is_playing = True
        self.play_btn.configure(text="⏸ Пауза", fg_color="#ea580c", hover_color="#c2410c")
        self._audio_play_from(self.current_time)
        self.play_thread = threading.Thread(target=self._play_loop, daemon=True)
        self.play_thread.start()

    def _pause(self):
        self.is_playing = False
        self._audio_stop()
        self.play_btn.configure(text="▶ Воспроизвести", fg_color="#2563eb", hover_color="#1d4ed8")

    def _play_loop(self):
        frame_interval = 1.0 / max(15.0, min(30.0, self.fps))
        while self.is_playing and self.cap and self.cap.isOpened():
            t_start = time.time()
            ret, frame = self.cap.read()
            if not ret:
                self.after(0, self._pause)
                break

            self.current_time = self.cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
            if self.current_time >= self.duration:
                self.after(0, self._pause)
                break

            self.after(0, lambda f=frame: self._display_frame(f))
            self.after(0, lambda: self.timeline_slider.set(self.current_time))
            self.after(
                0,
                lambda: self.time_lbl.configure(
                    text=f"{format_time(self.current_time)} / {format_time(self.duration)}"
                ),
            )
            self.after(0, self._ensure_playhead_visible)
            self.after(0, self._draw_timeline_canvas)

            elapsed = time.time() - t_start
            time.sleep(max(0.001, frame_interval - elapsed))

    def _display_frame(self, frame_bgr):
        try:
            rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
            pil_img = Image.fromarray(rgb)
            pil_img.thumbnail((540, 304), Image.Resampling.BILINEAR)
            ctk_img = ctk.CTkImage(light_image=pil_img, dark_image=pil_img, size=pil_img.size)
            self.preview_lbl.configure(image=ctk_img, text="")
            self.preview_lbl.image = ctk_img
        except Exception:
            pass

    # =========================================================================
    # FILMSTRIP
    # =========================================================================
    def _generate_filmstrip(self):
        """Generates dynamic thumbnails based on timeline zoom (Stage 6D)."""
        try:
            visible_window = self.duration / max(1.0, self.zoom_factor)
            t_start = max(0.0, self.view_offset_sec)
            t_end = min(self.duration, t_start + visible_window)

            # Determine frame sampling density based on zoom (Stage 6D)
            if self.zoom_factor >= 10.0:
                step = 0.5  # every 0.5s at high zoom
            elif self.zoom_factor >= 5.0:
                step = 1.0  # every 1.0s
            elif self.zoom_factor >= 2.0:
                step = 2.5
            else:
                step = max(2.0, (t_end - t_start) / 14.0)

            timestamps = []
            cur_t = t_start
            while cur_t <= t_end and len(timestamps) < 25:
                timestamps.append(cur_t)
                cur_t += step

            if not timestamps:
                timestamps = [0.0]

            cap_temp = cv2.VideoCapture(self.video_path)
            if not cap_temp.isOpened():
                return
            thumbs = []
            for t in timestamps:
                cap_temp.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
                ret, frame = cap_temp.read()
                if ret:
                    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    pil_thumb = Image.fromarray(rgb).resize((88, 50), Image.Resampling.BILINEAR)
                    thumbs.append((t, pil_thumb))
            cap_temp.release()
            self.after(0, lambda: self._populate_filmstrip(thumbs))
        except Exception as e:
            print(f"Filmstrip warning: {e}")

    def _populate_filmstrip(self, thumbs: List[Tuple[float, Image.Image]]):
        self.filmstrip_images = thumbs
        for widget in self.filmstrip_scroll.winfo_children():
            widget.destroy()
        for t, pil_img in thumbs:
            ctk_img = ctk.CTkImage(light_image=pil_img, dark_image=pil_img, size=(88, 50))
            col_frame = ctk.CTkFrame(self.filmstrip_scroll, fg_color="transparent")
            col_frame.pack(side="left", padx=3)
            btn = ctk.CTkButton(
                col_frame, text="", image=ctk_img, width=88, height=50, corner_radius=4,
                command=lambda ts=t: self.seek_to(ts),
            )
            btn.image = ctk_img
            btn.pack()
            ctk.CTkLabel(
                col_frame, text=format_time(t), font=ctk.CTkFont(size=9), text_color="gray70"
            ).pack(pady=(2, 0))

    # =========================================================================
    # SCISSORS TOOL — Enhanced
    # =========================================================================
    def _toggle_scissors_mode(self):
        self._scissors_mode = not self._scissors_mode
        if self._scissors_mode:
            self.scissors_mode_btn.configure(
                text="✂️ Режим ножниц: ВКЛ", fg_color="#b91c1c", hover_color="#991b1b"
            )
            self.timeline_canvas.configure(cursor="cross")
        else:
            self.scissors_mode_btn.configure(
                text="✂️ Режим ножниц: ВЫКЛ", fg_color="#374151", hover_color="#4b5563"
            )
            self.timeline_canvas.configure(cursor="")

    def _cut_at_current_time(self):
        self._push_undo()
        cut_t = round(self.current_time, 2)
        target_idx = None
        for idx, seg in enumerate(self.segments):
            if seg["start"] + 0.4 < cut_t < seg["end"] - 0.4:
                target_idx = idx
                break

        if target_idx is None:
            self._cut_undo_stack.pop()
            messagebox.showinfo(
                "Ножницы",
                f"Невозможно разрезать в позиции {format_time(cut_t)}.\n"
                "Плейхед слишком близко к краю фрагмента (минимум 0.5 сек от края).",
            )
            return

        old_seg = self.segments[target_idx]
        seg1 = {"start": old_seg["start"], "end": cut_t, "keep": old_seg["keep"]}
        seg2 = {"start": cut_t, "end": old_seg["end"], "keep": old_seg["keep"]}

        self.segments.pop(target_idx)
        self.segments.insert(target_idx, seg2)
        self.segments.insert(target_idx, seg1)

        self._refresh_segments_ui()
        self._draw_timeline_canvas()
        self._flash_new_segment(target_idx)

    def _push_undo(self):
        import copy
        self._cut_undo_stack.append(copy.deepcopy(self.segments))
        self._cut_redo_stack.clear()
        if len(self._cut_undo_stack) > 30:
            self._cut_undo_stack.pop(0)

    def _undo_cut(self):
        if not self._cut_undo_stack:
            return
        import copy
        self._cut_redo_stack.append(copy.deepcopy(self.segments))
        self.segments = self._cut_undo_stack.pop()
        self._refresh_segments_ui()
        self._draw_timeline_canvas()

    def _redo_cut(self):
        if not self._cut_redo_stack:
            return
        import copy
        self._cut_undo_stack.append(copy.deepcopy(self.segments))
        self.segments = self._cut_redo_stack.pop()
        self._refresh_segments_ui()
        self._draw_timeline_canvas()

    def _select_all_segments(self):
        """Select all segments for batch export (Ctrl+A, Stage 6B)."""
        if len(self.selected_segment_indices) == len(self.segments):
            self.selected_segment_indices.clear()
        else:
            self.selected_segment_indices = set(range(len(self.segments)))
        self._refresh_segments_ui()

    def _toggle_select_segment(self, idx: int, shift: bool = False):
        """Toggle or range select segments (Shift+click, Stage 6B)."""
        if shift and self._last_selected_segment_idx is not None:
            low = min(self._last_selected_segment_idx, idx)
            high = max(self._last_selected_segment_idx, idx)
            for i in range(low, high + 1):
                self.selected_segment_indices.add(i)
        else:
            if idx in self.selected_segment_indices:
                self.selected_segment_indices.remove(idx)
            else:
                self.selected_segment_indices.add(idx)
        self._last_selected_segment_idx = idx
        self.selected_segment_idx = idx
        self._refresh_segments_ui()

    def _delete_selected_segment(self):
        """Delete / mark cut for selected segment (Delete hotkey)."""
        target = self.selected_segment_idx
        if target is not None and 0 <= target < len(self.segments):
            self._push_undo()
            self.segments[target]["keep"] = False
            self._refresh_segments_ui()
            self._draw_timeline_canvas()

    def _jump_to_segment_num(self, num: int):
        """Jump to segment 1-9 by number key."""
        idx = num - 1
        if 0 <= idx < len(self.segments):
            self.seek_to(self.segments[idx]["start"])

    def _change_segments_page(self, delta: int):
        self.segments_page += delta
        self._refresh_segments_ui()

    def _on_batch_export(self):
        """Batch exports each selected (or kept) segment into an individual file (Stage 6B)."""
        if self.is_rendering:
            messagebox.showwarning("Рендер", "Уже выполняется рендеринг! Дождитесь завершения.")
            return

        if self.selected_segment_indices:
            target_indices = sorted(self.selected_segment_indices)
        else:
            target_indices = [i for i, s in enumerate(self.segments) if s["keep"]]

        if not target_indices:
            messagebox.showinfo(
                "Пакетный экспорт",
                "Нет выбранных фрагментов для экспорта! Выберите отрезки кликом или нажмите Ctrl+A."
            )
            return

        out_dir = Path(self.output_dir).resolve()
        out_dir.mkdir(parents=True, exist_ok=True)

        if self.is_playing:
            self._pause()

        self.is_rendering = True
        self.batch_export_btn.configure(state="disabled", text="⏳ Экспорт...")
        self.render_progress.set(0.0)

        threading.Thread(
            target=self._batch_export_worker,
            args=(target_indices, str(out_dir)),
            daemon=True
        ).start()

    def _batch_export_worker(self, target_indices: List[int], out_dir_str: str):
        total = len(target_indices)
        params = self._collect_render_params() or {}
        out_dir = Path(out_dir_str)
        exported_files = []

        try:
            for order, idx in enumerate(target_indices, start=1):
                seg = self.segments[idx]
                s_start, s_end = seg["start"], seg["end"]
                start_m, start_s = int(s_start // 60), int(s_start % 60)
                end_m, end_s = int(s_end // 60), int(s_end % 60)
                filename = f"clip_{order:02d}_{start_m:02d}m{start_s:02d}s-{end_m:02d}m{end_s:02d}s.mp4"
                out_path = str(out_dir / filename)

                self.after(0, lambda ord=order, tot=total, fn=filename: self.render_status_lbl.configure(
                    text=f"Пакетный экспорт [{ord}/{tot}]: {fn}...", text_color="#38bdf8"
                ))

                render_final(
                    input_path=self.video_path,
                    output_path=out_path,
                    segments=[(s_start, s_end)],
                    music_path=params.get("bg_music"),
                    music_vol=params.get("music_vol", 0.28),
                    dialog_vol=params.get("dialog_vol", 0.80),
                    hook_text=params.get("hook_text"),
                    subtitle_text=params.get("subtitles"),
                    watermark_text=params.get("cta_text"),
                    crop_mode=params.get("layout_mode", "fullscreen"),
                    aspect_ratio=params.get("aspect_ratio", "9:16"),
                    viral_effects=params.get("viral_effects"),
                    speed=1.03,
                    preset="veryfast",
                )
                exported_files.append(out_path)
                self.after(0, lambda ord=order, tot=total: self.render_progress.set(ord / tot))

            self.after(0, lambda: self.render_status_lbl.configure(
                text=f"✓ Экспортировано {len(exported_files)} клипов!", text_color="#10b981"
            ))
            self.after(0, lambda: messagebox.showinfo(
                "Пакетный экспорт завершён",
                f"Успешно экспортировано {len(exported_files)} клипов!\n\n"
                f"Папка: {out_dir}\n"
                f"Пример: {Path(exported_files[0]).name if exported_files else ''}"
            ))
        except Exception as e:
            self.after(0, lambda err=str(e): self.render_status_lbl.configure(
                text=f"Ошибка пакетного экспорта: {err[:60]}", text_color="#ef4444"
            ))
            self.after(0, lambda err=str(e): messagebox.showerror(
                "Ошибка экспорта", f"Не удалось завершить пакетный экспорт:\n{err}"
            ))
        finally:
            self.is_rendering = False
            self.after(0, lambda: self.batch_export_btn.configure(
                state="normal", text="📦 Экспорт выбранных"
            ))

    def _flash_new_segment(self, idx: int):
        """Flash the two new segments briefly to indicate a successful cut."""
        try:
            cards = list(self.segments_scroll.winfo_children())
            for i in [idx, idx + 1]:
                if i < len(cards):
                    try:
                        cards[i].configure(fg_color="#1e3a5f")
                    except Exception:
                        pass
            def restore():
                for i in [idx, idx + 1]:
                    if i < len(cards):
                        seg = self.segments[i] if i < len(self.segments) else None
                        if seg:
                            try:
                                cards[i].configure(
                                    fg_color="#1f2937" if seg["keep"] else "#3f1a1a"
                                )
                            except Exception:
                                pass
            self.after(500, restore)
        except Exception:
            pass

    def _cut_at_canvas_position(self, canvas_x: int):
        """Cut at a position given by a canvas pixel X coordinate."""
        width = self.timeline_canvas.winfo_width()
        if width <= 0:
            return
        ratio = max(0.0, min(1.0, canvas_x / width))
        t = ratio * self.duration
        old_time = self.current_time
        self.seek_to(t)
        self._cut_at_current_time()

    def _toggle_segment_keep(self, idx: int):
        if 0 <= idx < len(self.segments):
            self.segments[idx]["keep"] = not self.segments[idx]["keep"]
            self._refresh_segments_ui()
            self._draw_timeline_canvas()

    def _reset_segments(self):
        self._push_undo()
        self.segments = [{"start": 0.0, "end": self.duration, "keep": True}]
        self.selected_segment_indices.clear()
        self.segments_page = 0
        self._refresh_segments_ui()
        self._draw_timeline_canvas()

    def _refresh_segments_ui(self):
        """Refreshes segments list with virtualization when > 20 segments (Stage 6A)."""
        for w in self.segments_scroll.winfo_children():
            w.destroy()

        kept_count = 0
        total_kept_duration = 0.0
        for seg in self.segments:
            dur = seg["end"] - seg["start"]
            if seg["keep"]:
                kept_count += 1
                total_kept_duration += dur

        total_segs = len(self.segments)
        page_size = 20

        # Virtualization: if > 20 segments, render in pages of 20 with navigation controls
        if total_segs > page_size:
            max_page = (total_segs - 1) // page_size
            self.segments_page = max(0, min(self.segments_page, max_page))
            start_idx = self.segments_page * page_size
            end_idx = min(start_idx + page_size, total_segs)
            visible_indices = list(range(start_idx, end_idx))

            # Virtual pagination bar
            nav_bar = ctk.CTkFrame(self.segments_scroll, fg_color="#18181b", corner_radius=6)
            nav_bar.pack(fill="x", padx=2, pady=(0, 4))
            ctk.CTkButton(
                nav_bar, text="◀ Пред.", width=55, height=22, font=ctk.CTkFont(size=10),
                state="normal" if self.segments_page > 0 else "disabled",
                command=lambda: self._change_segments_page(-1)
            ).pack(side="left", padx=4, pady=3)

            page_info = f"Показаны #{start_idx + 1}–#{end_idx} из {total_segs} (Стр. {self.segments_page + 1}/{max_page + 1})"
            ctk.CTkLabel(
                nav_bar, text=page_info, font=ctk.CTkFont(size=11, weight="bold"), text_color="#38bdf8"
            ).pack(side="left", fill="x", expand=True, padx=4)

            ctk.CTkButton(
                nav_bar, text="След. ▶", width=55, height=22, font=ctk.CTkFont(size=10),
                state="normal" if self.segments_page < max_page else "disabled",
                command=lambda: self._change_segments_page(1)
            ).pack(side="right", padx=4, pady=3)
        else:
            visible_indices = list(range(total_segs))

        for idx in visible_indices:
            seg = self.segments[idx]
            dur = seg["end"] - seg["start"]
            is_selected = idx in self.selected_segment_indices

            # Highlight for selected segments (Stage 6B)
            border_col = "#38bdf8" if is_selected else ("#374151" if seg["keep"] else "#581c1c")
            border_w = 2 if is_selected else 1

            card = ctk.CTkFrame(
                self.segments_scroll,
                fg_color="#1e293b" if is_selected else ("#1f2937" if seg["keep"] else "#3f1a1a"),
                border_color=border_col,
                border_width=border_w,
                corner_radius=6,
            )
            card.pack(fill="x", padx=2, pady=3)
            card.grid_columnconfigure(0, weight=1)

            status_txt = "✓ ОСТАВИТЬ" if seg["keep"] else "❌ ВЫРЕЗАН"
            status_col = "#10b981" if seg["keep"] else "#ef4444"

            chk_txt = "☑" if is_selected else "☐"
            lbl_txt = f"{chk_txt} #{idx + 1}  [{format_time(seg['start'])} — {format_time(seg['end'])}]  ({dur:.1f}с)  —  {status_txt}"

            t_lbl = ctk.CTkLabel(
                card,
                text=lbl_txt,
                font=ctk.CTkFont(size=11, weight="bold"),
                text_color=status_col if not is_selected else "#38bdf8",
                anchor="w",
                cursor="hand2"
            )
            t_lbl.grid(row=0, column=0, sticky="w", padx=8, pady=4)
            t_lbl.bind("<Button-1>", lambda e, i=idx: self._toggle_select_segment(i, shift=bool(e.state & 0x0001)))

            btn_frame = ctk.CTkFrame(card, fg_color="transparent")
            btn_frame.grid(row=0, column=1, sticky="e", padx=6, pady=4)

            ctk.CTkButton(
                btn_frame, text="👁", width=28, height=24,
                font=ctk.CTkFont(size=11),
                command=lambda s=seg["start"]: self.seek_to(s),
            ).pack(side="left", padx=2)

            ctk.CTkButton(
                btn_frame,
                text="Восстановить" if not seg["keep"] else "✂️ Вырезать",
                width=90, height=24,
                font=ctk.CTkFont(size=11),
                fg_color="#4b5563" if not seg["keep"] else "#b91c1c",
                hover_color="#374151" if not seg["keep"] else "#991b1b",
                command=lambda i=idx: self._toggle_segment_keep(i),
            ).pack(side="left", padx=2)

        sel_count = len(self.selected_segment_indices)
        sel_text = f" | Выбрано: {sel_count}" if sel_count > 0 else ""
        self.kept_time_lbl.configure(
            text=f"Итоговая длительность: {int(total_kept_duration)} сек  ({kept_count} из {len(self.segments)} фрагментов{sel_text})",
            text_color="#10b981" if kept_count > 0 else "#ef4444",
        )
        self._update_effects_indicator()

    # =========================================================================
    # TIMELINE ZOOM & COORDINATE HELPERS (Stage 3)
    # =========================================================================
    def _time_to_canvas_x(self, t: float, width: int) -> float:
        """Converts timestamp to canvas pixel X considering zoom and pan offset."""
        visible_window = self.duration / max(1.0, self.zoom_factor)
        return ((t - self.view_offset_sec) / visible_window) * width

    def _canvas_x_to_time(self, x: float, width: int) -> float:
        """Converts canvas pixel X to timestamp considering zoom and pan offset."""
        visible_window = self.duration / max(1.0, self.zoom_factor)
        t = self.view_offset_sec + (x / max(1, width)) * visible_window
        return max(0.0, min(self.duration, t))

    def _clamp_view_offset(self):
        """Ensures view_offset_sec stays within valid range."""
        visible_window = self.duration / max(1.0, self.zoom_factor)
        max_offset = max(0.0, self.duration - visible_window)
        self.view_offset_sec = max(0.0, min(max_offset, self.view_offset_sec))

    def _ensure_playhead_visible(self):
        """Auto-scroll timeline during playback so playhead is always visible (Stage 3B)."""
        visible_window = self.duration / max(1.0, self.zoom_factor)
        if self.current_time < self.view_offset_sec:
            self.view_offset_sec = max(0.0, self.current_time - 0.1 * visible_window)
            self._clamp_view_offset()
            self._draw_timeline_canvas()
        elif self.current_time > self.view_offset_sec + visible_window:
            self.view_offset_sec = self.current_time - 0.85 * visible_window
            self._clamp_view_offset()
            self._draw_timeline_canvas()

    def _zoom_step(self, direction: int, center_ratio: float = 0.5):
        """Step zoom level in or out around center_ratio (Stage 3A & 3C)."""
        old_zoom = self.zoom_factor
        old_idx = self.zoom_idx
        new_idx = max(0, min(len(self.zoom_levels) - 1, self.zoom_idx + direction))
        if new_idx == old_idx:
            return

        self.zoom_idx = new_idx
        new_zoom = self.zoom_levels[self.zoom_idx]
        self.zoom_factor = new_zoom

        # Center zoom on mouse cursor / focus point
        old_window = self.duration / old_zoom
        new_window = self.duration / new_zoom
        center_t = self.view_offset_sec + center_ratio * old_window
        self.view_offset_sec = center_t - center_ratio * new_window
        self._clamp_view_offset()

        if hasattr(self, "zoom_lbl"):
            self.zoom_lbl.configure(text=f"×{self.zoom_factor:.1f}")
        self._draw_timeline_canvas()

    def _zoom_fit(self):
        """Fit entire video onto timeline width (Stage 3C)."""
        self.zoom_idx = 0
        self.zoom_factor = 1.0
        self.view_offset_sec = 0.0
        if hasattr(self, "zoom_lbl"):
            self.zoom_lbl.configure(text="×1.0")
        self._draw_timeline_canvas()

    def _zoom_one_to_one(self):
        """1:1 zoom: 1 second ~ 1px/scale or standard 10x frame zoom (Stage 3C)."""
        # Find zoom level closest to 1:1 or 10.0
        target = 10.0 if self.duration > 30 else 3.0
        best_idx = min(range(len(self.zoom_levels)), key=lambda i: abs(self.zoom_levels[i] - target))
        self.zoom_idx = best_idx
        self.zoom_factor = self.zoom_levels[self.zoom_idx]
        # Center around playhead
        visible_window = self.duration / self.zoom_factor
        self.view_offset_sec = self.current_time - 0.5 * visible_window
        self._clamp_view_offset()
        if hasattr(self, "zoom_lbl"):
            self.zoom_lbl.configure(text=f"×{self.zoom_factor:.1f}")
        self._draw_timeline_canvas()

    def _scroll_home(self):
        self.view_offset_sec = 0.0
        self.seek_to(0.0)

    def _scroll_end(self):
        visible_window = self.duration / max(1.0, self.zoom_factor)
        self.view_offset_sec = max(0.0, self.duration - visible_window)
        self.seek_to(self.duration)

    def _scroll_relative_percent(self, percent: float):
        """Scrolls timeline view by percentage of visible window (Stage 3B)."""
        visible_window = self.duration / max(1.0, self.zoom_factor)
        self.view_offset_sec += percent * visible_window
        self._clamp_view_offset()
        self._draw_timeline_canvas()

    def _on_canvas_mousewheel(self, event):
        """Mousewheel = zoom in / out centered at mouse position (Stage 3A)."""
        width = self.timeline_canvas.winfo_width()
        ratio = max(0.0, min(1.0, event.x / max(1, width)))
        direction = 1 if event.delta > 0 else -1
        self._zoom_step(direction, center_ratio=ratio)

    def _on_canvas_shift_mousewheel(self, event):
        """Shift + Mousewheel = horizontal scroll (Stage 3B)."""
        direction = -1 if event.delta > 0 else 1
        self._scroll_relative_percent(direction * 0.15)

    def _on_pan_start(self, event):
        """Middle click or drag start for panning empty timeline space (Stage 3B)."""
        self._is_panning = True
        self._pan_start_x = event.x
        self._pan_start_offset = self.view_offset_sec
        self.timeline_canvas.configure(cursor="fleur")

    def _on_pan_motion(self, event):
        if self._is_panning and self._pan_start_x is not None:
            width = self.timeline_canvas.winfo_width()
            visible_window = self.duration / max(1.0, self.zoom_factor)
            dx = event.x - self._pan_start_x
            dt = (dx / max(1, width)) * visible_window
            self.view_offset_sec = self._pan_start_offset - dt
            self._clamp_view_offset()
            self._draw_timeline_canvas()

    def _on_pan_release(self, event):
        self._is_panning = False
        self._pan_start_x = None
        self.timeline_canvas.configure(cursor="")

    def _find_boundary_at_x(self, canvas_x: int, threshold_px: int = 6) -> Optional[float]:
        """Checks if canvas_x is near a segment boundary taking zoom into account."""
        width = self.timeline_canvas.winfo_width()
        if width <= 1:
            width = 560
        if width <= 0 or self.duration <= 0:
            return None
        t_click = self._canvas_x_to_time(canvas_x, width)
        for seg in self.segments:
            for b in (seg["start"], seg["end"]):
                if 0.05 < b < self.duration - 0.05:
                    bx = self._time_to_canvas_x(b, width)
                    if abs(canvas_x - bx) <= threshold_px:
                        return b
        return None

    def _draw_timeline_canvas(self):
        self.timeline_canvas.delete("all")
        width = self.timeline_canvas.winfo_width()
        height = self.timeline_canvas.winfo_height()
        if width <= 1:
            width = 560
        if height <= 1:
            height = 58

        ruler_h = 14
        track_y1 = ruler_h + 2
        track_y2 = height - 4

        visible_window = self.duration / max(1.0, self.zoom_factor)
        t_start_vis = self.view_offset_sec
        t_end_vis = min(self.duration, t_start_vis + visible_window)

        # 1. Timeline Ruler with dynamic interval based on zoom (Stage 2E & 3D)
        if visible_window <= 2.0:
            interval_secs = 0.2
        elif visible_window <= 5.0:
            interval_secs = 0.5
        elif visible_window <= 15.0:
            interval_secs = 1.0
        elif visible_window <= 30.0:
            interval_secs = 2.0
        elif visible_window <= 60.0:
            interval_secs = 5.0
        elif visible_window <= 180.0:
            interval_secs = 10.0
        elif visible_window <= 600.0:
            interval_secs = 30.0
        else:
            interval_secs = 60.0

        first_mark = math.floor(t_start_vis / interval_secs) * interval_secs
        t_mark = max(0.0, first_mark)
        while t_mark <= t_end_vis + interval_secs:
            if t_mark >= t_start_vis - 0.01:
                mx = self._time_to_canvas_x(t_mark, width)
                if -20 <= mx <= width + 20:
                    self.timeline_canvas.create_line(mx, ruler_h - 4, mx, ruler_h, fill="#64748b", width=1)
                    if interval_secs < 1.0:
                        time_str = f"{int(t_mark // 60)}:{t_mark % 60:04.1f}"
                    else:
                        time_str = f"{int(t_mark // 60)}:{int(t_mark % 60):02d}"
                    self.timeline_canvas.create_text(
                        mx + 2, ruler_h - 7, text=time_str, fill="#94a3b8",
                        font=("Consolas", 7), anchor="sw"
                    )
            t_mark += interval_secs

        self.timeline_canvas.create_line(0, ruler_h, width, ruler_h, fill="#334155", width=1)

        # 2. Segment blocks clipped to visible range
        for seg in self.segments:
            if seg["end"] <= t_start_vis or seg["start"] >= t_end_vis:
                continue
            x1 = max(0, self._time_to_canvas_x(seg["start"], width))
            x2 = min(width, self._time_to_canvas_x(seg["end"], width))
            color = "#10b981" if seg["keep"] else "#7f1d1d"
            self.timeline_canvas.create_rectangle(
                x1, track_y1, x2, track_y2, fill=color, outline="#111827", width=1
            )

        # 3. Cut markers & drag handles (Stage 2D & 3D)
        seen = set()
        for seg in self.segments:
            for boundary in (seg["start"], seg["end"]):
                if 0.01 < boundary < self.duration - 0.01 and boundary not in seen:
                    seen.add(boundary)
                    if t_start_vis - 0.5 <= boundary <= t_end_vis + 0.5:
                        bx = self._time_to_canvas_x(boundary, width)
                        is_hovered = (
                            self._hovered_handle_boundary is not None and
                            abs(self._hovered_handle_boundary - boundary) < 0.05
                        )
                        line_color = "#38bdf8" if is_hovered else "white"
                        self.timeline_canvas.create_line(
                            bx, track_y1, bx, track_y2, fill=line_color, width=2 if is_hovered else 1.5, dash=(4, 2)
                        )
                        # Handle pill
                        pill_w = 4 if not is_hovered else 6
                        self.timeline_canvas.create_rectangle(
                            bx - pill_w, track_y1 + 4, bx + pill_w, track_y2 - 4,
                            fill="#38bdf8" if is_hovered else "#e2e8f0", outline="#0f172a", width=1
                        )
                        self.timeline_canvas.create_text(
                            bx, track_y2 - 1, text=format_time(boundary),
                            fill="#cbd5e1", font=("Consolas", 7), anchor="s"
                        )

        # 4. Active Click-and-Drag selection zone (Stage 2A)
        if self._drag_select_active and self._drag_select_start_x is not None and self._drag_select_current_x is not None:
            sx = min(self._drag_select_start_x, self._drag_select_current_x)
            ex = max(self._drag_select_start_x, self._drag_select_current_x)
            self.timeline_canvas.create_rectangle(
                sx, track_y1, ex, track_y2,
                fill="#ef4444", stipple="gray25", outline="#dc2626", width=2
            )
            self.timeline_canvas.create_line(sx, track_y1, sx, track_y2, fill="#38bdf8", width=2)
            self.timeline_canvas.create_line(ex, track_y1, ex, track_y2, fill="#ef4444", width=2)
            st_t = self._canvas_x_to_time(sx, width)
            en_t = self._canvas_x_to_time(ex, width)
            sel_dur = en_t - st_t
            self.timeline_canvas.create_text(
                (sx + ex) / 2, (track_y1 + track_y2) / 2,
                text=f"✂ Вырезать: {sel_dur:.1f}с\n[{format_time(st_t)} - {format_time(en_t)}]",
                fill="white", font=("Segoe UI", 9, "bold"), justify="center"
            )

        # 5. Scissors hover preview & tooltip (Stage 2C)
        if self._canvas_hover_x is not None:
            hx = self._canvas_hover_x
            t = self._canvas_x_to_time(hx, width)
            line_color = "#f87171" if self._scissors_mode else "#94a3b8"
            self.timeline_canvas.create_line(
                hx, ruler_h, hx, track_y2, fill=line_color, width=1, dash=(3, 2)
            )
            tooltip_txt = f"{format_time(t)} ✂" if self._scissors_mode else format_time(t)
            tx = max(4, min(width - 56, hx + 4))
            self.timeline_canvas.create_text(
                tx, ruler_h + 2, text=tooltip_txt, fill=line_color,
                font=("Consolas", 8, "bold"), anchor="nw"
            )

        # 6. Playhead
        if t_start_vis - 0.5 <= self.current_time <= t_end_vis + 0.5:
            px = self._time_to_canvas_x(self.current_time, width)
            self.timeline_canvas.create_line(px, 0, px, height, fill="#fbbf24", width=2.5)
            self.timeline_canvas.create_polygon(px - 6, 0, px + 6, 0, px, 8, fill="#fbbf24")

    def _on_canvas_click(self, event):
        width = self.timeline_canvas.winfo_width()
        if width <= 0 or self.duration <= 0:
            return

        boundary = self._find_boundary_at_x(event.x, threshold_px=7)
        if boundary is not None:
            self._dragging_handle_boundary = boundary
            self._push_undo()
            return

        if self._scissors_mode:
            self._drag_select_active = True
            self._drag_select_start_x = event.x
            self._drag_select_current_x = event.x
            self._draw_timeline_canvas()
        else:
            t = self._canvas_x_to_time(event.x, width)
            self.seek_to(t)

    def _on_canvas_drag(self, event):
        width = self.timeline_canvas.winfo_width()
        if width <= 0 or self.duration <= 0:
            return

        if self._dragging_handle_boundary is not None:
            new_t = self._canvas_x_to_time(event.x, width)
            old_b = self._dragging_handle_boundary
            for seg in self.segments:
                if abs(seg["start"] - old_b) < 0.05:
                    seg["start"] = round(new_t, 2)
                elif abs(seg["end"] - old_b) < 0.05:
                    seg["end"] = round(new_t, 2)
            self._dragging_handle_boundary = round(new_t, 2)
            self._refresh_segments_ui()
            self._draw_timeline_canvas()
            return

        if self._drag_select_active and self._scissors_mode:
            self._drag_select_current_x = max(0, min(width, event.x))
            self._draw_timeline_canvas()

    def _on_canvas_release(self, event):
        width = self.timeline_canvas.winfo_width()
        if width <= 0 or self.duration <= 0:
            return

        if self._dragging_handle_boundary is not None:
            self._dragging_handle_boundary = None
            self.timeline_canvas.configure(cursor="")
            self._refresh_segments_ui()
            self._draw_timeline_canvas()
            return

        if self._drag_select_active and self._scissors_mode:
            self._drag_select_active = False
            start_x = self._drag_select_start_x
            end_x = self._drag_select_current_x
            self._drag_select_start_x = None
            self._drag_select_current_x = None

            if start_x is not None and end_x is not None:
                sx = min(start_x, end_x)
                ex = max(start_x, end_x)
                if ex - sx > 8:
                    t1 = round(self._canvas_x_to_time(sx, width), 2)
                    t2 = round(self._canvas_x_to_time(ex, width), 2)
                    if t2 - t1 >= 0.2:
                        self._apply_drag_cut_range(t1, t2)
                        return

            cut_t = self._canvas_x_to_time(event.x, width)
            self.seek_to(cut_t)
            self._cut_at_current_time()

    def _apply_drag_cut_range(self, cut_start: float, cut_end: float):
        """Splits segments and marks the [cut_start, cut_end] range as keep=False (Stage 2A)."""
        self._push_undo()
        new_segments = []
        for seg in self.segments:
            s_st = seg["start"]
            s_en = seg["end"]
            keep = seg["keep"]

            # No overlap
            if cut_end <= s_st or cut_start >= s_en:
                new_segments.append(seg)
                continue

            # Left part before cut
            if s_st < cut_start:
                new_segments.append({"start": s_st, "end": cut_start, "keep": keep})

            # Overlapping middle part marked as cut (keep=False)
            overlap_st = max(s_st, cut_start)
            overlap_en = min(s_en, cut_end)
            if overlap_en - overlap_st >= 0.1:
                new_segments.append({"start": overlap_st, "end": overlap_en, "keep": False})

            # Right part after cut
            if s_en > cut_end:
                new_segments.append({"start": cut_end, "end": s_en, "keep": keep})

        if new_segments:
            self.segments = new_segments
            self._refresh_segments_ui()
            self._draw_timeline_canvas()

    def _cut_at_canvas_position(self, canvas_x: int):
        width = self.timeline_canvas.winfo_width()
        if width <= 0:
            return
        t = self._canvas_x_to_time(canvas_x, width)
        self.seek_to(t)
        self._cut_at_current_time()

    def _on_canvas_double_click(self, event):
        """Double click anywhere on timeline = pinpoint cut at cursor (Stage 2B)."""
        self._cut_at_canvas_position(event.x)

    def _on_canvas_hover(self, event):
        self._canvas_hover_x = event.x
        boundary = self._find_boundary_at_x(event.x, threshold_px=6)
        if boundary is not None:
            self._hovered_handle_boundary = boundary
            self.timeline_canvas.configure(cursor="sb_h_double_arrow")
        else:
            self._hovered_handle_boundary = None
            if self._scissors_mode:
                self.timeline_canvas.configure(cursor="cross")
            else:
                self.timeline_canvas.configure(cursor="")
        self._draw_timeline_canvas()

    def _on_canvas_leave(self, event):
        self._canvas_hover_x = None
        self._hovered_handle_boundary = None
        self.timeline_canvas.configure(cursor="")
        self._draw_timeline_canvas()

    # =========================================================================
    # SMART CROP
    # =========================================================================
    def _start_smart_crop_analysis(self):
        if self._smart_crop_analyzing:
            return
        self._smart_crop_analyzing = True
        self.smart_crop_btn.configure(state="disabled", text="⏳ Анализирую кадры...")
        self.smart_crop_status.configure(
            text="Анализ кадров для умной обрезки... (это займет несколько секунд)",
            text_color="#fbbf24"
        )
        threading.Thread(target=self._smart_crop_worker, daemon=True).start()

    def _smart_crop_worker(self):
        try:
            from smart_crop import analyze_smart_crop

            def on_progress(p):
                self.after(
                    0,
                    lambda: self.smart_crop_status.configure(
                        text=f"Анализ кадров: {int(p * 100)}%...",
                        text_color="#fbbf24"
                    )
                )

            keyframes, src_w, src_h = analyze_smart_crop(
                self.video_path,
                sample_interval=0.5,
                progress_cb=on_progress,
            )

            self._smart_crop_keyframes = keyframes
            self._smart_crop_src_w = src_w
            self._smart_crop_src_h = src_h

            n_faces = sum(1 for _, x in keyframes if x != (src_w - 1080) // 2)

            self.after(
                0,
                lambda: self.smart_crop_status.configure(
                    text=f"✅ Активна: {len(keyframes)} ключевых точек, {src_w}×{src_h} источник "
                         f"| Персонаж обнаружен в ~{min(n_faces, len(keyframes))} из {len(keyframes)} кадров",
                    text_color="#10b981"
                )
            )
            self.after(
                0,
                lambda: self.smart_crop_btn.configure(
                    text="✅ Умная обрезка АКТИВНА", fg_color="#065f46", state="normal"
                )
            )
            self.after(0, self._update_effects_indicator)
        except Exception as e:
            self._smart_crop_keyframes = None
            self.after(
                0,
                lambda err=str(e): self.smart_crop_status.configure(
                    text=f"Ошибка анализа: {err[:80]}", text_color="#ef4444"
                )
            )
            self.after(
                0,
                lambda: self.smart_crop_btn.configure(
                    text="🎯 Анализировать и включить умную обрезку",
                    fg_color="#7c3aed", state="normal"
                )
            )
            self.after(0, self._update_effects_indicator)
        finally:
            self._smart_crop_analyzing = False

    def _clear_smart_crop(self):
        self._smart_crop_keyframes = None
        self._smart_crop_src_w = 0
        self._smart_crop_src_h = 0
        self.smart_crop_status.configure(
            text="Статус: не активна (будет использоваться центральная обрезка по умолчанию)",
            text_color="#6b7280"
        )
        self.smart_crop_btn.configure(
            text="🎯 Анализировать и включить умную обрезку",
            fg_color="#7c3aed"
        )
        self._update_effects_indicator()

    # =========================================================================
    # MUSIC
    # =========================================================================
    def _update_effects_indicator(self):
        """Updates the applied effects status panel in real time (Stage 1C)."""
        if not hasattr(self, "effects_status_lbl"):
            return

        # 1. Trimming status
        kept_segs = [s for s in self.segments if s.get("keep", True)]
        cut_segs = [s for s in self.segments if not s.get("keep", True)]
        if len(self.segments) > 1 or cut_segs:
            trim_str = f"✅ Обрезка: {len(kept_segs)} сегм. (вырезано: {len(cut_segs)})"
        else:
            trim_str = "✅ Обрезка: 1 сегмент (весь ролик)"

        # 2. Music status
        music_choice = self.music_dropdown.get() if hasattr(self, "music_dropdown") else ""
        if "Без фоновой" in music_choice or not music_choice:
            music_str = "❌ Музыка: не выбрана (оригинал)"
        else:
            short_m = music_choice.split("(")[0].strip()
            music_str = f"✅ Музыка: {short_m}"

        # 3. Subtitles status
        subs = self.subs_entry.get().strip() if hasattr(self, "subs_entry") else ""
        if subs:
            sub_snippet = (subs[:22] + "...") if len(subs) > 22 else subs
            sub_str = f"✅ Субтитры: \"{sub_snippet}\""
        else:
            sub_str = "❌ Субтитры: не введены"

        # 4. Aspect Ratio & Crop status
        ar_txt = self.aspect_ratio_var.get() if hasattr(self, "aspect_ratio_var") else "9:16"
        mode_txt = "Fullscreen" if getattr(self, "layout_var", None) and self.layout_var.get() == "fullscreen" else "Blurscreen"
        if getattr(self, "_smart_crop_keyframes", None):
            crop_str = f"✅ Smart Crop: {ar_txt} (трекинг)"
        else:
            crop_str = f"📐 Формат: {ar_txt} ({mode_txt})"

        # 5. Viral Effects status
        viral_cnt = 0
        for v in ["viral_zoom_var", "viral_flash_var", "viral_cc_var", "viral_vignette_var", "viral_sharpen_var", "viral_bass_var"]:
            if hasattr(self, v) and getattr(self, v).get():
                viral_cnt += 1
        if viral_cnt > 0:
            viral_str = f"🔥 TikTok/Reels: {viral_cnt} эффектов"
        else:
            viral_str = "❌ Вирусные эффекты: выкл"

        # 6. Hook header status
        hook = self.hook_entry.get().strip() if hasattr(self, "hook_entry") else ""
        hook_str = "✅ Хук: активен" if hook else "❌ Хук: не указан"

        panel_text = (
            f"┌──────────────────────────────────────┐\n"
            f"│  {trim_str:<36}│\n"
            f"│  {crop_str:<36}│\n"
            f"│  {viral_str:<36}│\n"
            f"│  {music_str:<36}│\n"
            f"│  {sub_str:<36}│\n"
            f"│  {hook_str:<36}│\n"
            f"└──────────────────────────────────────┘"
        )
        self.effects_status_lbl.configure(text=panel_text)

    def _on_viral_preset_changed(self, preset_name: str):
        if preset_name == "🔥 TikTok Ultra":
            for var in [self.viral_zoom_var, self.viral_flash_var, self.viral_cc_var, self.viral_vignette_var, self.viral_sharpen_var, self.viral_bass_var]:
                var.set(True)
        elif preset_name == "⚡ Zoom & Flash":
            self.viral_zoom_var.set(True)
            self.viral_flash_var.set(True)
            self.viral_cc_var.set(False)
            self.viral_vignette_var.set(False)
            self.viral_sharpen_var.set(True)
            self.viral_bass_var.set(True)
        elif preset_name == "🎨 Vibrant Pop":
            self.viral_zoom_var.set(False)
            self.viral_flash_var.set(False)
            self.viral_cc_var.set(True)
            self.viral_vignette_var.set(True)
            self.viral_sharpen_var.set(True)
            self.viral_bass_var.set(False)
        elif preset_name == "❌ Без эффектов":
            for var in [self.viral_zoom_var, self.viral_flash_var, self.viral_cc_var, self.viral_vignette_var, self.viral_sharpen_var, self.viral_bass_var]:
                var.set(False)
        self._update_effects_indicator()

    def _on_music_changed(self, choice: str):
        if choice == "Свой аудиофайл...":
            f = filedialog.askopenfilename(
                title="Выберите аудиофайл",
                filetypes=[("Аудио файлы", "*.mp3 *.wav *.aac *.m4a *.ogg"), ("Все файлы", "*.*")],
            )
            if f:
                self.custom_music_path = f
                self.music_dropdown.set(f"Свой: {Path(f).name[:20]}")
            else:
                self.music_dropdown.set("Phonk (Вирусный бит)")
        else:
            self.custom_music_path = None
        self._update_effects_indicator()

    # =========================================================================
    # PREVIEW & FINAL RENDER (Stage 1A & 1B)
    # =========================================================================
    def _collect_render_params(self, max_preview_duration: Optional[float] = None):
        """Collects and validates parameters for preview or final export."""
        kept_segments = [(s["start"], s["end"]) for s in self.segments if s["keep"]]
        if not kept_segments:
            return None

        # If previewing, limit total duration to max_preview_duration (e.g. 15s)
        if max_preview_duration and max_preview_duration > 0:
            preview_segs = []
            remaining = max_preview_duration
            for s_start, s_end in kept_segments:
                seg_len = s_end - s_start
                if remaining <= 0:
                    break
                if seg_len <= remaining:
                    preview_segs.append((s_start, s_end))
                    remaining -= seg_len
                else:
                    preview_segs.append((s_start, s_start + remaining))
                    remaining = 0
            kept_segments = preview_segs

        layout_mode = self.layout_var.get()
        aspect_ratio = self.aspect_ratio_var.get() if hasattr(self, "aspect_ratio_var") else "9:16"
        viral_effects = {
            "zoom_punch": self.viral_zoom_var.get() if hasattr(self, "viral_zoom_var") else False,
            "white_flash": self.viral_flash_var.get() if hasattr(self, "viral_flash_var") else False,
            "vibrant_cc": self.viral_cc_var.get() if hasattr(self, "viral_cc_var") else False,
            "vignette": self.viral_vignette_var.get() if hasattr(self, "viral_vignette_var") else False,
            "sharpen": self.viral_sharpen_var.get() if hasattr(self, "viral_sharpen_var") else False,
            "bass_boost": self.viral_bass_var.get() if hasattr(self, "viral_bass_var") else False,
        }
        subtitles = self.subs_entry.get().strip() or None
        hook_text = self.hook_entry.get().strip() or None
        cta_text = self.cta_entry.get().strip() or None
        music_vol = float(self.music_vol_slider.get())
        dialog_vol = float(self.dialog_vol_slider.get()) if hasattr(self, "dialog_vol_slider") else 0.80

        music_choice = self.music_dropdown.get()
        bg_music = None
        if "Phonk" in music_choice:
            bg_music = get_asset_path("assets/music/phonk_bass.mp3")
        elif "Epic" in music_choice:
            bg_music = get_asset_path("assets/music/epic_orchestral.mp3")
        elif "Lo-Fi" in music_choice:
            bg_music = get_asset_path("assets/music/lofi_chill.mp3")
        elif hasattr(self, "custom_music_path") and self.custom_music_path:
            bg_music = self.custom_music_path

        return {
            "segments": kept_segments,
            "layout_mode": layout_mode,
            "aspect_ratio": aspect_ratio,
            "viral_effects": viral_effects,
            "subtitles": subtitles,
            "hook_text": hook_text,
            "cta_text": cta_text,
            "bg_music": bg_music,
            "music_vol": music_vol,
            "dialog_vol": dialog_vol,
        }

    def _on_preview_rendered_clip(self):
        """Renders up to 15 seconds with all effects into a temp file and opens it in player (Stage 1B)."""
        if self.is_rendering:
            return

        params = self._collect_render_params(max_preview_duration=15.0)
        if not params:
            messagebox.showwarning(
                "Пустой ролик",
                "Все фрагменты вырезаны! Оставьте хотя бы один отрезок.",
            )
            return

        if self.is_playing:
            self._pause()

        tmp_out = tempfile.NamedTemporaryFile(suffix="_preview.mp4", delete=False)
        tmp_out.close()
        self._preview_temp_file = tmp_out.name

        self.is_rendering = True
        self.preview_render_btn.configure(state="disabled", text="⏳ СБОРКА ПРЕДПРОСМОТРА...")
        self.render_progress.set(0.1)
        self.render_status_lbl.configure(
            text="Рендеринг быстрого предпросмотра (до 15 сек)...",
            text_color="#38bdf8",
        )

        threading.Thread(
            target=self._preview_worker,
            args=(params, self._preview_temp_file),
            daemon=True,
        ).start()

    def _preview_worker(self, params: Dict[str, Any], preview_file: str):
        try:
            def on_progress(p: float):
                self.after(0, lambda val=p: self.render_progress.set(val))

            render_final(
                input_path=self.video_path,
                output_path=preview_file,
                segments=params["segments"],
                music_path=params["bg_music"],
                music_vol=params["music_vol"],
                dialog_vol=params.get("dialog_vol", 0.80),
                hook_text=params["hook_text"],
                subtitle_text=params["subtitles"],
                watermark_text=params["cta_text"],
                crop_mode=params["layout_mode"],
                aspect_ratio=params.get("aspect_ratio", "9:16"),
                viral_effects=params.get("viral_effects"),
                speed=1.03,
                progress_callback=on_progress,
            )

            # Load preview video directly into this player
            self.after(0, lambda: self._load_preview_file_into_player(preview_file))

        except Exception as e:
            self.after(0, lambda err=str(e): self.render_status_lbl.configure(
                text=f"Ошибка предпросмотра: {err[:60]}", text_color="#ef4444"
            ))
            self.after(0, lambda err=str(e): messagebox.showerror(
                "Ошибка предпросмотра", f"Не удалось отрендерить предпросмотр:\n{err}"
            ))
        finally:
            self.is_rendering = False
            self.after(0, lambda: self.preview_render_btn.configure(
                state="normal", text="👁 Предпросмотр готового"
            ))

    def _load_preview_file_into_player(self, preview_file: str):
        """Loads generated preview into preview player and enables final Save button (Stage 1B)."""
        self.render_progress.set(1.0)
        self.render_status_lbl.configure(
            text="✓ Предпросмотр готов! Воспроизводится в окне редактора.",
            text_color="#10b981",
        )
        self.render_btn.configure(
            state="normal",
            text="💾 СОХРАНИТЬ РОЛИК (ГОТОВО К ЭКСПОРТУ)",
            fg_color="#10b981",
            hover_color="#059669"
        )

        try:
            # Switch player capture to preview file
            if self.cap:
                self.cap.release()
            self.cap = cv2.VideoCapture(preview_file)
            self.fps = self.cap.get(cv2.CAP_PROP_FPS) or 30.0
            total_frames = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            self.duration = (total_frames / self.fps) if self.fps > 0 else 15.0

            # Reload audio for preview
            if _PYGAME_OK and self._audio_wav_path:
                try:
                    ffmpeg_bin = get_ffmpeg_path()
                    cmd = [
                        ffmpeg_bin, "-y",
                        "-i", preview_file,
                        "-vn",
                        "-acodec", "pcm_s16le",
                        "-ar", "44100",
                        "-ac", "2",
                        self._audio_wav_path,
                    ]
                    proc = _silent_popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    proc.communicate()
                    if proc.returncode == 0:
                        pygame.mixer.music.load(self._audio_wav_path)
                        self._audio_ready = True
                except Exception as ex:
                    print(f"Preview audio load error: {ex}")

            self.seek_to(0.0)
            self._play()
        except Exception as e:
            print(f"Error loading preview file: {e}")

    def _on_start_final_render(self):
        if self.is_rendering:
            return

        params = self._collect_render_params()
        if not params:
            messagebox.showwarning(
                "Пустой ролик",
                "Все фрагменты вырезаны! Оставьте хотя бы один отрезок.",
            )
            return

        out_dir = Path(self.output_dir).resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        stem = Path(self.video_path).stem[:25]
        suffix = "_smartcrop" if self._smart_crop_keyframes else ""
        final_output_path = str(out_dir / f"{stem}{suffix}_shorts_9x16.mp4")

        if self.is_playing:
            self._pause()
        self.is_rendering = True
        self.render_btn.configure(state="disabled", text="⏳ ИДЕТ СОХРАНЕНИЕ...")
        self.render_progress.set(0.05)
        self.render_status_lbl.configure(
            text="Склейка фрагментов, наложение музыки, титров и рендеринг...",
            text_color="#38bdf8",
        )

        threading.Thread(
            target=self._render_worker,
            args=(params, final_output_path),
            daemon=True,
        ).start()

    def _render_worker(self, params: Dict[str, Any], output_path: str):
        try:
            def on_progress(p: float):
                self.after(0, lambda val=p: self.render_progress.set(val))

            # Smart crop single segment special path
            if self._smart_crop_keyframes and len(params["segments"]) == 1:
                from smart_crop import render_smart_crop_clip
                s_start, s_end = params["segments"][0]
                rendered_file = render_smart_crop_clip(
                    input_path=self.video_path,
                    output_path=output_path,
                    keyframes=self._smart_crop_keyframes,
                    src_w=self._smart_crop_src_w,
                    src_h=self._smart_crop_src_h,
                    start_time=s_start,
                    duration=s_end - s_start,
                    watermark_text=params["cta_text"],
                    hook_header_text=params["hook_text"],
                    subtitles_text=params["subtitles"],
                    bg_music_path=params["bg_music"],
                    bg_music_volume=params["music_vol"],
                    dialog_volume=params.get("dialog_vol", 0.80),
                    speed_factor=1.03,
                    preset="veryfast",
                )
            else:
                rendered_file = render_final(
                    input_path=self.video_path,
                    output_path=output_path,
                    segments=params["segments"],
                    music_path=params["bg_music"],
                    music_vol=params["music_vol"],
                    dialog_vol=params.get("dialog_vol", 0.80),
                    hook_text=params["hook_text"],
                    subtitle_text=params["subtitles"],
                    watermark_text=params["cta_text"],
                    crop_mode=params["layout_mode"],
                    aspect_ratio=params.get("aspect_ratio", "9:16"),
                    viral_effects=params.get("viral_effects"),
                    speed=1.03,
                    preset="veryfast",
                    progress_callback=on_progress,
                )

            self.after(0, lambda: self.render_progress.set(1.0))
            self.after(0, lambda: self.render_status_lbl.configure(
                text="✓ Финальный ролик успешно готов!", text_color="#10b981"
            ))
            self.after(0, lambda: messagebox.showinfo(
                "Готово!",
                f"Финальный ролик 9:16 успешно сохранен!\n\n"
                f"Файл: {Path(rendered_file).name}\n"
                f"Папка: {Path(rendered_file).parent}"
            ))
            if self.on_render_complete:
                self.after(0, lambda: self.on_render_complete(rendered_file))

        except Exception as e:
            self.after(0, lambda err=str(e): self.render_status_lbl.configure(
                text=f"Ошибка рендера: {err[:60]}", text_color="#ef4444"
            ))
            self.after(0, lambda err=str(e): messagebox.showerror(
                "Ошибка рендеринга", f"Не удалось завершить рендер:\n{err}"
            ))
        finally:
            self.is_rendering = False
            self.after(0, lambda: self.render_btn.configure(
                state="normal", text="💾 СОХРАНИТЬ РОЛИК"
            ))

    # =========================================================================
    # CLEANUP
    # =========================================================================
    def _on_close(self):
        self.is_playing = False
        self._audio_stop()
        if self.cap:
            self.cap.release()
            self.cap = None
        if self._audio_wav_path:
            try:
                Path(self._audio_wav_path).unlink(missing_ok=True)
            except Exception:
                pass
        self.destroy()
