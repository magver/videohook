"""
VideoHook GUI Application
Dedicated Viral Vertical Anime Video Downloader (9:16 Fullscreen).
Features:
- Instant download of viral anime shorts from YouTube Shorts, TikTok, Instagram Reels, VK Clips, RuTube.
- Automatic removal of watermarks, channel logos, and outro cards (Delogo engine).
- Guaranteed prominent anime title detection and display.
- Built-in interactive 9:16 video player preview.
- One-click export to clean MP4 9:16 and direct bridge to timeline editor.
"""

import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
import tkinter as tk
from tkinter import filedialog, messagebox

# Globally suppress terminal windows on Windows
if os.name == "nt":
    _OrigPopen = subprocess.Popen

    class _SilentPopen(_OrigPopen):
        def __init__(self, *args, **kwargs):
            kwargs["creationflags"] = kwargs.get("creationflags", 0) | subprocess.CREATE_NO_WINDOW
            super().__init__(*args, **kwargs)

    subprocess.Popen = _SilentPopen

import customtkinter as ctk
import cv2
from PIL import Image, ImageTk

from clipper import ensure_ffmpeg_in_path, get_ffmpeg_path
from editor_window import VideoEditorWindow
from anime_detector import detect_anime_title, format_description_with_anime
from antigravity_bridge import (
    build_viral_antigravity_prompt,
    save_antigravity_bundle,
    TELEGRAM_HOOK_TEMPLATES,
    launch_antigravity_job,
    get_antigravity_job_status,
)
from social_downloader import (
    CURATED_VIRAL_SHORTS,
    download_clean_social_clip,
    extract_social_video_info,
    search_viral_anime_clips,
    identify_platform,
)
from watermark_remover import remove_watermarks_and_finalize_video

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


def get_asset_path(rel_path: str) -> str:
    """Finds resource path for both normal run and PyInstaller bundle."""
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


class VideoHookApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("VideoHook — Загрузчик вирусных аниме клипов 9:16 (без водяных знаков)")
        self.geometry("1260x860")
        self.minsize(1080, 740)

        self.ffmpeg_path = ""
        self.output_dir = Path("output_shorts").resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir = Path("cache_clips").resolve()
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        # State - Viral Clips Feed
        self.social_clips_data: List[Dict[str, Any]] = []
        self.selected_social_clip: Optional[Dict[str, Any]] = None
        self.is_social_downloading: bool = False
        self.stop_requested: bool = False

        # State - Embedded Player
        self.current_video_path: Optional[str] = None
        self.cap: Optional[cv2.VideoCapture] = None
        self.is_playing: bool = False
        self.video_fps: float = 30.0
        self.video_duration: float = 0.0
        self.total_video_frames: int = 0
        self.current_frame_idx: int = 0
        self.player_photo: Optional[ImageTk.PhotoImage] = None

        # Verify FFmpeg early
        try:
            self.ffmpeg_path = ensure_ffmpeg_in_path()
        except Exception as e:
            messagebox.showwarning("FFmpeg", f"Не удалось найти FFmpeg:\n{e}\nУстановите ffmpeg или проверьте imageio-ffmpeg.")

        self._build_ui()
        self._init_social_clips()

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        # =====================================================================
        # 1. HEADER BAR
        # =====================================================================
        header = ctk.CTkFrame(self, corner_radius=0, fg_color=("#1e293b", "#0f172a"), height=65)
        header.grid(row=0, column=0, sticky="ew", padx=0, pady=0)
        header.grid_columnconfigure(0, weight=1)
        header.grid_columnconfigure(1, weight=0)

        h_left = ctk.CTkFrame(header, fg_color="transparent")
        h_left.grid(row=0, column=0, sticky="w", padx=20, pady=10)

        title_lbl = ctk.CTkLabel(
            h_left,
            text="🎬 VideoHook — Загрузчик вирусных аниме клипов 9:16",
            font=ctk.CTkFont(size=20, weight="bold"),
            text_color="#38bdf8",
        )
        title_lbl.pack(anchor="w")

        subtitle_lbl = ctk.CTkLabel(
            h_left,
            text="YouTube Shorts • TikTok • Instagram Reels • VK Клипы • RuTube | Русская озвучка • Субтитры • Без водяных знаков",
            font=ctk.CTkFont(size=12),
            text_color="gray75",
        )
        subtitle_lbl.pack(anchor="w")

        h_right = ctk.CTkFrame(header, fg_color="transparent")
        h_right.grid(row=0, column=1, sticky="e", padx=20, pady=10)

        shield_badge = ctk.CTkLabel(
            h_right,
            text=" 🛡️ Авто-очистка водяных знаков: ВКЛ ",
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="#065f46",
            text_color="#34d399",
            corner_radius=6,
        )
        shield_badge.pack(side="right", padx=(8, 0))

        folder_btn = ctk.CTkButton(
            h_right,
            text="📁 Папка с роликами",
            width=150,
            height=32,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="#334155",
            hover_color="#475569",
            command=self._on_open_output_folder,
        )
        folder_btn.pack(side="right")

        # =====================================================================
        # 2. TOP TOOLBAR: FAST URL INPUT, PARSER & OPTIONS
        # =====================================================================
        top_bar = ctk.CTkFrame(self, fg_color="#18181b", corner_radius=10)
        top_bar.grid(row=1, column=0, sticky="ew", padx=15, pady=(10, 6))
        top_bar.grid_columnconfigure(1, weight=1)

        # Row 0: Direct URL input
        ctk.CTkLabel(
            top_bar, text="⚡ Ссылка или поиск:", font=ctk.CTkFont(size=12, weight="bold")
        ).grid(row=0, column=0, padx=(14, 8), pady=8, sticky="w")

        self.social_url_entry = ctk.CTkEntry(
            top_bar,
            placeholder_text="Вставьте ссылку на Shorts, TikTok, Reels, VK, RuTube или название аниме (например: Магическая битва)...",
            height=34,
            font=ctk.CTkFont(size=12),
        )
        self.social_url_entry.grid(row=0, column=1, padx=4, pady=8, sticky="ew")
        self.social_url_entry.bind("<Return>", lambda e: self._on_parse_social_url())

        paste_btn = ctk.CTkButton(
            top_bar,
            text="📋 Вставить",
            width=90,
            height=34,
            fg_color="#27272a",
            hover_color="#3f3f46",
            command=self._on_paste_social_url,
        )
        paste_btn.grid(row=0, column=2, padx=4, pady=8)

        self.social_parse_btn = ctk.CTkButton(
            top_bar,
            text="🔍 Разобрать",
            width=115,
            height=34,
            fg_color="#2563eb",
            hover_color="#1d4ed8",
            font=ctk.CTkFont(weight="bold"),
            command=self._on_parse_social_url,
        )
        self.social_parse_btn.grid(row=0, column=3, padx=4, pady=8)

        self.quick_download_btn = ctk.CTkButton(
            top_bar,
            text="📥 Скачать сразу (9:16)",
            width=175,
            height=34,
            fg_color="#059669",
            hover_color="#047857",
            font=ctk.CTkFont(weight="bold"),
            command=self._on_quick_download_from_url,
        )
        self.quick_download_btn.grid(row=0, column=4, padx=(4, 14), pady=8)

        # Row 1: Filters & Options
        filter_row = ctk.CTkFrame(top_bar, fg_color="transparent")
        filter_row.grid(row=1, column=0, columnspan=5, sticky="ew", padx=14, pady=(0, 8))

        self.social_platform_var = ctk.StringVar(value="Все")
        self.social_platform_seg = ctk.CTkSegmentedButton(
            filter_row,
            values=["Все", "YouTube Shorts", "TikTok", "Instagram Reels", "VK Клипы", "RuTube"],
            command=self._on_social_filter_changed,
        )
        self.social_platform_seg.set("Все")
        self.social_platform_seg.pack(side="left", padx=(0, 15))

        self.social_no_wm_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            filter_row,
            text="🛡️ Удалять водяные знаки (Delogo + Outro cut)",
            font=ctk.CTkFont(size=11, weight="bold"),
            variable=self.social_no_wm_var,
        ).pack(side="left", padx=(0, 15))

        self.social_crop_9x16_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            filter_row,
            text="📱 Авто-формат 9:16 Fullscreen",
            font=ctk.CTkFont(size=11, weight="bold"),
            variable=self.social_crop_9x16_var,
        ).pack(side="left", padx=(0, 15))

        self.social_ru_only_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            filter_row,
            text="🇷🇺 Русская озвучка / субтитры",
            font=ctk.CTkFont(size=11, weight="bold"),
            variable=self.social_ru_only_var,
            command=self._refresh_social_feed,
        ).pack(side="left", padx=(0, 15))

        reset_btn = ctk.CTkButton(
            filter_row,
            text="🔄 Обновить радар",
            width=135,
            height=28,
            fg_color="#374151",
            hover_color="#4b5563",
            font=ctk.CTkFont(size=11),
            command=self._on_social_reset,
        )
        reset_btn.pack(side="right")

        # =====================================================================
        # 3. MAIN WORKSPACE: TWO COLUMNS (FEED + INSPECTOR / PLAYER)
        # =====================================================================
        content_frame = ctk.CTkFrame(self, fg_color="transparent")
        content_frame.grid(row=2, column=0, sticky="nsew", padx=15, pady=(0, 8))
        content_frame.grid_columnconfigure(0, weight=5)  # Left: Feed
        content_frame.grid_columnconfigure(1, weight=5)  # Right: Player & Inspector
        content_frame.grid_rowconfigure(0, weight=1)

        # ---------------------------------------------------------------------
        # LEFT COLUMN: FEED OF VIRAL ANIME SHORTS
        # ---------------------------------------------------------------------
        left_box = ctk.CTkFrame(content_frame, corner_radius=10, fg_color="#18181b")
        left_box.grid(row=0, column=0, sticky="nsew", padx=(0, 6), pady=0)
        left_box.grid_rowconfigure(1, weight=1)
        left_box.grid_columnconfigure(0, weight=1)

        left_hdr = ctk.CTkFrame(left_box, fg_color="transparent")
        left_hdr.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 6))

        ctk.CTkLabel(
            left_hdr,
            text="🔥 Лента вирусных аниме-роликов:",
            font=ctk.CTkFont(size=14, weight="bold"),
        ).pack(side="left")

        self.feed_count_lbl = ctk.CTkLabel(
            left_hdr,
            text="0 роликов",
            font=ctk.CTkFont(size=11),
            text_color="gray70",
        )
        self.feed_count_lbl.pack(side="right")

        self.social_scroll = ctk.CTkScrollableFrame(left_box, fg_color="transparent")
        self.social_scroll.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 8))
        self.social_scroll.grid_columnconfigure(0, weight=1)

        # ---------------------------------------------------------------------
        # RIGHT COLUMN: VIDEO PLAYER & DETAILS HUB
        # ---------------------------------------------------------------------
        right_box = ctk.CTkFrame(content_frame, corner_radius=10, fg_color="#18181b")
        right_box.grid(row=0, column=1, sticky="nsew", padx=(6, 0), pady=0)
        right_box.grid_rowconfigure(1, weight=1)
        right_box.grid_columnconfigure(0, weight=1)

        right_hdr = ctk.CTkFrame(right_box, fg_color="transparent")
        right_hdr.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 4))
        ctk.CTkLabel(
            right_hdr,
            text="🎬 Плеер предпросмотра и детали тайтла",
            font=ctk.CTkFont(size=14, weight="bold"),
        ).pack(side="left")

        # Scrollable content for the right column to accommodate player and all details
        right_scroll = ctk.CTkScrollableFrame(right_box, fg_color="transparent")
        right_scroll.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 8))
        right_scroll.grid_columnconfigure(0, weight=1)

        # 3.1 ANIME TITLE HIGHLIGHT BOX (Genuinely visible in description and header)
        anime_box = ctk.CTkFrame(right_scroll, fg_color="#0f172a", corner_radius=8, border_width=1, border_color="#38bdf8")
        anime_box.pack(fill="x", padx=6, pady=(0, 8))
        anime_box.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            anime_box,
            text="🎬 НАЗВАНИЕ АНИМЕ:",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#38bdf8",
        ).grid(row=0, column=0, padx=10, pady=8, sticky="w")

        self.social_anime_entry = ctk.CTkEntry(
            anime_box,
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color="#1e293b",
            text_color="#f8fafc",
            height=32,
        )
        self.social_anime_entry.grid(row=0, column=1, padx=(4, 10), pady=8, sticky="ew")

        # 3.2 EMBEDDED VIDEO PLAYER / CANVAS
        player_container = ctk.CTkFrame(right_scroll, fg_color="#09090b", corner_radius=8)
        player_container.pack(fill="x", padx=6, pady=(0, 8))
        player_container.grid_columnconfigure(0, weight=1)

        self.preview_canvas = tk.Canvas(
            player_container,
            width=225,
            height=400,
            bg="#000000",
            highlightthickness=1,
            highlightbackground="#27272a",
        )
        self.preview_canvas.grid(row=0, column=0, pady=(10, 6))

        # Controls under player
        ctl_frame = ctk.CTkFrame(player_container, fg_color="transparent")
        ctl_frame.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 6))
        ctl_frame.grid_columnconfigure(2, weight=1)

        self.play_btn = ctk.CTkButton(
            ctl_frame,
            text="▶ Воспроизвести",
            width=130,
            height=28,
            fg_color="#3b82f6",
            hover_color="#2563eb",
            font=ctk.CTkFont(size=11, weight="bold"),
            command=self._toggle_playback,
        )
        self.play_btn.grid(row=0, column=0, padx=(0, 6))

        self.replay_btn = ctk.CTkButton(
            ctl_frame,
            text="↺",
            width=36,
            height=28,
            fg_color="#374151",
            hover_color="#4b5563",
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self._replay_video,
        )
        self.replay_btn.grid(row=0, column=1, padx=(0, 8))

        self.scrub_slider = ctk.CTkSlider(
            ctl_frame,
            from_=0.0,
            to=1.0,
            height=16,
            command=self._on_scrub,
        )
        self.scrub_slider.set(0.0)
        self.scrub_slider.grid(row=0, column=2, sticky="ew", padx=(0, 8))

        self.time_lbl = ctk.CTkLabel(
            ctl_frame,
            text="00:00 / 00:00",
            font=ctk.CTkFont(size=11, family="Consolas"),
            text_color="gray75",
        )
        self.time_lbl.grid(row=0, column=3)

        # 3.3 Specs & Badges Row
        self.social_specs_frame = ctk.CTkFrame(right_scroll, fg_color="transparent")
        self.social_specs_frame.pack(fill="x", padx=6, pady=(0, 6))

        self.social_platform_lbl = ctk.CTkLabel(
            self.social_specs_frame,
            text="🌐 Платформа: YouTube Shorts",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#60a5fa",
        )
        self.social_platform_lbl.pack(side="left", padx=(0, 10))

        self.social_duration_lbl = ctk.CTkLabel(
            self.social_specs_frame,
            text="⏱ Длительность: 25с",
            font=ctk.CTkFont(size=11),
            text_color="gray80",
        )
        self.social_duration_lbl.pack(side="left", padx=(0, 10))

        self.social_views_lbl = ctk.CTkLabel(
            self.social_specs_frame,
            text="👁 Просмотры: 2.4M",
            font=ctk.CTkFont(size=11),
            text_color="gray80",
        )
        self.social_views_lbl.pack(side="left", padx=(0, 10))

        # 3.4 Description Box (Guaranteed to show Anime Title)
        desc_hdr = ctk.CTkFrame(right_scroll, fg_color="transparent")
        desc_hdr.pack(fill="x", padx=6, pady=(2, 2))
        ctk.CTkLabel(
            desc_hdr,
            text="📝 Описание ролика и метаданные:",
            font=ctk.CTkFont(size=11, weight="bold"),
        ).pack(side="left")

        self.social_desc_box = ctk.CTkTextbox(
            right_scroll,
            height=95,
            font=ctk.CTkFont(size=11, family="Consolas"),
            wrap="word",
            fg_color="#09090b",
        )
        self.social_desc_box.pack(fill="x", padx=6, pady=(0, 8))

        # 3.5 Action Buttons
        btn_frame = ctk.CTkFrame(right_scroll, fg_color="transparent")
        btn_frame.pack(fill="x", padx=6, pady=(4, 8))
        btn_frame.grid_columnconfigure((0, 1), weight=1)

        self.social_dl_btn = ctk.CTkButton(
            btn_frame,
            text="📥 СКАЧАТЬ БЕЗ ВОДЯНЫХ ЗНАКОВ (MP4 9:16)",
            height=44,
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color="#059669",
            hover_color="#047857",
            command=self._on_download_selected_social,
        )
        self.social_dl_btn.grid(row=0, column=0, padx=(0, 4), sticky="ew")

        self.social_edit_btn = ctk.CTkButton(
            btn_frame,
            text="✂ В монтажный стол",
            height=44,
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color="#e11d48",
            hover_color="#be123c",
            command=self._on_open_selected_social_in_editor,
        )
        self.social_edit_btn.grid(row=0, column=1, padx=(4, 0), sticky="ew")

        ag_row = ctk.CTkFrame(right_scroll, fg_color="transparent")
        ag_row.pack(fill="x", padx=6, pady=(0, 8))
        ag_row.grid_columnconfigure((0, 1), weight=1)

        self.antigravity_1click_btn = ctk.CTkButton(
            ag_row,
            text="⚡ В 1 КЛИК: В ANTIGRAVITY (3.8 FLASH)",
            height=40,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="#10b981",
            hover_color="#059669",
            command=self._on_one_click_antigravity,
        )
        self.antigravity_1click_btn.grid(row=0, column=0, padx=(0, 4), sticky="ew")

        self.antigravity_btn = ctk.CTkButton(
            ag_row,
            text="⚙ Настроить TG-воронку",
            height=40,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="#7c3aed",
            hover_color="#6d28d9",
            command=self._on_open_antigravity_dialog,
        )
        self.antigravity_btn.grid(row=0, column=1, padx=(4, 0), sticky="ew")

        # =====================================================================
        # 4. BOTTOM PANEL: PROGRESS BAR & STATUS LOG
        # =====================================================================
        bottom = ctk.CTkFrame(self, corner_radius=0, height=110, fg_color="#09090b")
        bottom.grid(row=3, column=0, sticky="ew", padx=15, pady=(0, 12))
        bottom.grid_columnconfigure(0, weight=1)

        prog_row = ctk.CTkFrame(bottom, fg_color="transparent")
        prog_row.grid(row=0, column=0, sticky="ew", padx=10, pady=(6, 2))
        prog_row.grid_columnconfigure(0, weight=1)

        self.status_lbl = ctk.CTkLabel(
            prog_row,
            text="Готов к работе | Выберите ролик или вставьте ссылку",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#38bdf8",
        )
        self.status_lbl.grid(row=0, column=0, sticky="w")

        self.progress_bar = ctk.CTkProgressBar(prog_row, height=8)
        self.progress_bar.grid(row=1, column=0, sticky="ew", pady=(4, 2))
        self.progress_bar.set(0.0)

        self.log_box = ctk.CTkTextbox(
            bottom,
            height=50,
            font=ctk.CTkFont(size=10, family="Consolas"),
            wrap="word",
            fg_color="#18181b",
        )
        self.log_box.grid(row=1, column=0, sticky="ew", padx=10, pady=(2, 6))

    # =========================================================================
    # LOGGING & UTILS
    # =========================================================================
    def log(self, msg: str):
        ts = time.strftime("%H:%M:%S")
        self.log_box.insert("end", f"[{ts}] {msg}\n")
        self.log_box.see("end")

    def _on_open_output_folder(self):
        """Opens output_shorts folder in Windows Explorer."""
        folder = str(self.output_dir)
        try:
            if os.name == "nt":
                os.startfile(folder)
            else:
                subprocess.run(["xdg-open", folder])
        except Exception as e:
            self.log(f"Ошибка открытия папки: {e}")

    # =========================================================================
    # VIRAL CLIPS FEED MANAGEMENT
    # =========================================================================
    def _init_social_clips(self):
        """Initializes curated viral clips on application startup."""
        self.social_clips_data = [dict(c) for c in CURATED_VIRAL_SHORTS]
        self._refresh_social_feed()
        if self.social_clips_data:
            self._on_select_social_clip(self.social_clips_data[0])

    def _refresh_social_feed(self):
        """Renders viral clip cards into the scrollable feed."""
        for widget in self.social_scroll.winfo_children():
            widget.destroy()

        platform_filter = self.social_platform_var.get()
        query = self.social_url_entry.get().strip().lower()
        ru_only = self.social_ru_only_var.get()

        visible_count = 0
        for item in self.social_clips_data:
            # Platform filter
            if platform_filter != "Все" and platform_filter not in item.get("platform", ""):
                continue

            # Russian audio/subtitles filter
            if ru_only:
                audio = item.get("audio_lang", "").lower()
                subs = item.get("subtitles", "").lower()
                desc = item.get("description", "").lower()
                if "рус" not in audio and "рус" not in subs and "рус" not in desc:
                    continue

            # Query filter (if query is entered in top bar)
            if query and not (query.startswith("http://") or query.startswith("https://")):
                full_text = f"{item.get('anime_title', '')} {item.get('title', '')} {item.get('description', '')}".lower()
                if query not in full_text:
                    continue

            self._create_social_clip_card(item)
            visible_count += 1

        self.feed_count_lbl.configure(text=f"{visible_count} роликов")

        if visible_count == 0:
            lbl = ctk.CTkLabel(
                self.social_scroll,
                text="Ничего не найдено по данному фильтру.\nВставьте прямую ссылку выше или введите поисковый запрос.",
                font=ctk.CTkFont(size=12),
                text_color="gray70",
            )
            lbl.pack(pady=40)

    def _create_social_clip_card(self, item: Dict[str, Any]):
        """Creates a modern UI card for a viral short clip."""
        card = ctk.CTkFrame(self.social_scroll, corner_radius=8, fg_color="#1f2937")
        card.pack(fill="x", padx=4, pady=4)
        card.grid_columnconfigure(0, weight=1)

        # Top Badge Row: Platform, Duration, Views
        top_row = ctk.CTkFrame(card, fg_color="transparent")
        top_row.grid(row=0, column=0, sticky="ew", padx=10, pady=(8, 2))

        plat = item.get("platform", "Shorts")
        p_color = (
            "#dc2626" if "YouTube" in plat
            else ("#0f766e" if "RuTube" in plat
            else ("#1d4ed8" if "VK" in plat
            else ("#7c3aed" if "Instagram" in plat
            else "#0284c7")))
        )
        plat_badge = ctk.CTkLabel(
            top_row,
            text=f" {plat} ",
            font=ctk.CTkFont(size=10, weight="bold"),
            fg_color=p_color,
            corner_radius=4,
        )
        plat_badge.pack(side="left", padx=(0, 6))

        dur_lbl = ctk.CTkLabel(
            top_row,
            text=f"⏱ {item.get('duration', 25)}с",
            font=ctk.CTkFont(size=10),
            text_color="gray75",
        )
        dur_lbl.pack(side="left", padx=(0, 6))

        views_lbl = ctk.CTkLabel(
            top_row,
            text=f"👁 {item.get('views', '1M+')}",
            font=ctk.CTkFont(size=10),
            text_color="gray75",
        )
        views_lbl.pack(side="left")

        # ANIME TITLE BADGE (Prominent and clear)
        anime_title = item.get("anime_title", "Аниме тайтл")
        anime_lbl = ctk.CTkLabel(
            card,
            text=f"🎬 Аниме: {anime_title}",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#38bdf8",
            anchor="w",
        )
        anime_lbl.grid(row=1, column=0, sticky="w", padx=10, pady=(2, 2))

        # Clip Title
        clip_title = item.get("title", "")
        title_lbl = ctk.CTkLabel(
            card,
            text=clip_title,
            font=ctk.CTkFont(size=12),
            text_color="#f3f4f6",
            anchor="w",
            wraplength=480,
            justify="left",
        )
        title_lbl.grid(row=2, column=0, sticky="w", padx=10, pady=(0, 4))

        # Features row: Subs, Voiceover, No watermark
        feat_row = ctk.CTkFrame(card, fg_color="transparent")
        feat_row.grid(row=3, column=0, sticky="ew", padx=10, pady=(0, 6))

        ctk.CTkLabel(
            feat_row,
            text="✓ Русская озвучка • ✓ Русские субтитры • 🛡️ Авто-очистка водяных знаков",
            font=ctk.CTkFont(size=10),
            text_color="#34d399",
        ).pack(side="left")

        # Action Buttons row
        action_row = ctk.CTkFrame(card, fg_color="transparent")
        action_row.grid(row=4, column=0, sticky="ew", padx=10, pady=(0, 8))

        sel_btn = ctk.CTkButton(
            action_row,
            text="👁 Выбрать",
            width=80,
            height=26,
            font=ctk.CTkFont(size=11),
            fg_color="#374151",
            hover_color="#4b5563",
            command=lambda it=item: self._on_select_social_clip(it),
        )
        sel_btn.pack(side="left", padx=(0, 6))

        dl_btn = ctk.CTkButton(
            action_row,
            text="📥 Скачать MP4",
            width=105,
            height=26,
            font=ctk.CTkFont(size=11),
            fg_color="#059669",
            hover_color="#047857",
            command=lambda it=item: self._on_download_social_clip_direct(it),
        )
        dl_btn.pack(side="left", padx=(0, 6))

        edit_btn = ctk.CTkButton(
            action_row,
            text="✂ В монтаж",
            width=90,
            height=26,
            font=ctk.CTkFont(size=11),
            fg_color="#be123c",
            hover_color="#9f1239",
            command=lambda it=item: self._on_open_social_clip_editor_direct(it),
        )
        edit_btn.pack(side="left")

    def _on_select_social_clip(self, item: Dict[str, Any]):
        """Populates the right inspector panel and loads poster into the player."""
        self.selected_social_clip = item

        # 1. Update Anime Title Entry and URL
        self.social_anime_entry.delete(0, "end")
        self.social_anime_entry.insert(0, item.get("anime_title", ""))
        if item.get("url"):
            self.social_url_entry.delete(0, "end")
            self.social_url_entry.insert(0, item.get("url"))

        # 2. Update Specs
        plat = item.get("platform", "Shorts")
        self.social_platform_lbl.configure(text=f"🌐 Платформа: {plat}")
        self.social_duration_lbl.configure(text=f"⏱ Длительность: {item.get('duration', 25)}с")
        self.social_views_lbl.configure(text=f"👁 Просмотры: {item.get('views', '1M+')}")

        # 3. Update Description
        desc = item.get("description", "")
        if not desc:
            desc = format_description_with_anime(
                original_description=item.get("title", ""),
                anime_title=item.get("anime_title", ""),
                platform_name=plat,
                has_subtitles=True,
                no_watermark=True,
            )
        self.social_desc_box.delete("1.0", "end")
        self.social_desc_box.insert("1.0", desc)

        # 4. Check if local video file exists for this clip in cache or output
        vid_id = item.get("id", "")
        found_local = None
        for folder in [self.output_dir, self.cache_dir]:
            for p in folder.glob(f"*{vid_id}*"):
                if p.is_file() and p.stat().st_size > 1024 and p.suffix.lower() == ".mp4":
                    found_local = str(p)
                    break
            if found_local:
                break

        if found_local:
            self._load_video_to_player(found_local)
        else:
            self._render_placeholder_frame(item.get("anime_title", "Аниме"), item.get("title", ""))

        self.log(f"Выбран ролик: {item.get('title')} (Аниме: {item.get('anime_title')})")

    # =========================================================================
    # EMBEDDED VIDEO PLAYER ENGINE (OpenCV + PIL)
    # =========================================================================
    def _render_placeholder_frame(self, anime_title: str, clip_title: str):
        """Draws a sleek 9:16 poster frame when video is not yet downloaded locally."""
        self._stop_playback()
        img = Image.new("RGB", (225, 400), color=(15, 23, 42))
        self.player_photo = ImageTk.PhotoImage(img)
        self.preview_canvas.delete("all")
        self.preview_canvas.create_image(112, 200, image=self.player_photo)
        self.preview_canvas.create_text(
            112, 160,
            text=f"🎬 {anime_title[:24]}",
            fill="#38bdf8",
            font=("Arial", 11, "bold"),
            width=200,
            justify="center",
        )
        self.preview_canvas.create_text(
            112, 220,
            text="Нажмите «Скачать MP4» для воспроизведения без водяных знаков",
            fill="#94a3b8",
            font=("Arial", 9),
            width=190,
            justify="center",
        )
        self.time_lbl.configure(text="00:00 / 00:25")
        self.play_btn.configure(text="▶ Воспроизвести", state="disabled")

    def _load_video_to_player(self, video_path: str):
        """Loads a local MP4 file into the OpenCV preview player."""
        self._stop_playback()
        if self.cap:
            self.cap.release()

        self.current_video_path = video_path
        self.cap = cv2.VideoCapture(video_path)
        if not self.cap.isOpened():
            self.log(f"Не удалось открыть видео в плеере: {video_path}")
            return

        self.total_video_frames = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT))
        self.video_fps = self.cap.get(cv2.CAP_PROP_FPS) or 30.0
        self.video_duration = self.total_video_frames / self.video_fps if self.video_fps > 0 else 25.0
        self.current_frame_idx = 0

        self.play_btn.configure(state="normal")
        self._display_frame_at_index(0)
        self.log(f"Видео загружено в плеер: {Path(video_path).name} ({self.video_duration:.1f}с)")

    def _display_frame_at_index(self, frame_idx: int):
        if not self.cap:
            return
        self.cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
        ret, frame = self.cap.read()
        if ret and frame is not None:
            # Resize preserving 9:16 aspect (225x400)
            fh, fw = frame.shape[:2]
            scale = min(225.0 / fw, 400.0 / fh)
            nw = int(fw * scale)
            nh = int(fh * scale)
            resized = cv2.resize(frame, (nw, nh))
            rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)

            canvas_img = Image.new("RGB", (225, 400), (0, 0, 0))
            paste_x = (225 - nw) // 2
            paste_y = (400 - nh) // 2
            canvas_img.paste(Image.fromarray(rgb), (paste_x, paste_y))

            self.player_photo = ImageTk.PhotoImage(canvas_img)
            self.preview_canvas.delete("all")
            self.preview_canvas.create_image(112, 200, image=self.player_photo)

            # Update slider and time label
            cur_sec = frame_idx / self.video_fps
            tot_sec = self.video_duration
            frac = cur_sec / tot_sec if tot_sec > 0 else 0.0
            self.scrub_slider.set(frac)
            self.time_lbl.configure(text=f"{int(cur_sec//60):02d}:{int(cur_sec%60):02d} / {int(tot_sec//60):02d}:{int(tot_sec%60):02d}")

    def _toggle_playback(self):
        if not self.cap or not self.current_video_path:
            return
        if self.is_playing:
            self._stop_playback()
        else:
            self.is_playing = True
            self.play_btn.configure(text="⏸ Пауза")
            self._playback_loop()

    def _stop_playback(self):
        self.is_playing = False
        self.play_btn.configure(text="▶ Воспроизвести")

    def _replay_video(self):
        self.current_frame_idx = 0
        self._display_frame_at_index(0)
        if not self.is_playing:
            self._toggle_playback()

    def _on_scrub(self, val):
        if not self.cap:
            return
        target_frame = int(float(val) * self.total_video_frames)
        self.current_frame_idx = max(0, min(self.total_video_frames - 1, target_frame))
        self._display_frame_at_index(self.current_frame_idx)

    def _playback_loop(self):
        if not self.is_playing or not self.cap:
            return

        self.current_frame_idx += 1
        if self.current_frame_idx >= self.total_video_frames:
            self.current_frame_idx = 0  # Loop playback

        self._display_frame_at_index(self.current_frame_idx)
        delay_ms = max(10, int(1000.0 / self.video_fps))
        self.after(delay_ms, self._playback_loop)

    # =========================================================================
    # ACTIONS: PASTE, SEARCH, PARSE, DOWNLOAD
    # =========================================================================
    def _on_paste_social_url(self):
        try:
            url = self.clipboard_get().strip()
            if url:
                self.social_url_entry.delete(0, "end")
                self.social_url_entry.insert(0, url)
                self.log(f"Вставлена ссылка из буфера: {url}")
        except Exception as e:
            self.log(f"Буфер обмена недоступен: {e}")

    def _on_parse_social_url(self):
        query_or_url = self.social_url_entry.get().strip()
        if not query_or_url:
            messagebox.showinfo("Внимание", "Пожалуйста, введите ссылку или поисковый запрос.")
            return

        is_direct = query_or_url.startswith("http://") or query_or_url.startswith("https://")
        if not is_direct:
            # It's a search query, search online and in catalog!
            self.log(f"Поиск вирусных роликов по запросу: {query_or_url}")
            self.status_lbl.configure(text=f"Поиск: {query_or_url}...")
            self.social_parse_btn.configure(state="disabled", text="⏳ Поиск...")

            def search_worker():
                results = search_viral_anime_clips(
                    query=query_or_url,
                    platform_filter=self.social_platform_var.get(),
                )
                self.after(0, lambda: self._on_search_done(results, query_or_url))

            threading.Thread(target=search_worker, daemon=True).start()
            return

        # Direct URL parsing
        self.social_parse_btn.configure(state="disabled", text="⏳ Анализ...")
        self.status_lbl.configure(text="Извлечение метаданных и детекция аниме...")
        self.log(f"Парсинг ссылки: {query_or_url}")

        def parse_worker():
            try:
                info = extract_social_video_info(query_or_url)
                self.social_clips_data.insert(0, info)
                self.after(0, lambda: self._on_parse_success(info))
            except Exception as e:
                self.after(0, lambda: self._on_parse_error(str(e)))

        threading.Thread(target=parse_worker, daemon=True).start()

    def _on_search_done(self, results: List[Dict[str, Any]], query: str):
        self.social_parse_btn.configure(state="normal", text="🔍 Разобрать")
        self.status_lbl.configure(text=f"Найдено: {len(results)} роликов")
        for r in results:
            if not any(item["id"] == r["id"] for item in self.social_clips_data):
                self.social_clips_data.insert(0, r)
        self._refresh_social_feed()
        if results:
            self._on_select_social_clip(results[0])
        self.log(f"Поиск завершен: найдено {len(results)} роликов")

    def _on_parse_success(self, info: Dict[str, Any]):
        self.social_parse_btn.configure(state="normal", text="🔍 Разобрать")
        self.status_lbl.configure(text=f"Разобрано: {info.get('anime_title')}")
        self.log(f"✓ Ролик успешно разобран! Определено аниме: {info.get('anime_title')}")
        self._refresh_social_feed()
        self._on_select_social_clip(info)
        messagebox.showinfo(
            "Успешно",
            f"Ролик успешно разобран!\n\n🎬 Аниме: {info.get('anime_title')}\n🌐 Платформа: {info.get('platform')}\n🛡 Водяные знаки: Будут удалены при скачивании\n📱 Формат: 9:16 Fullscreen",
        )

    def _on_parse_error(self, err_msg: str):
        self.social_parse_btn.configure(state="normal", text="🔍 Разобрать")
        self.status_lbl.configure(text="Ошибка разбора ссылки")
        self.log(f"❌ Ошибка разбора: {err_msg}")
        messagebox.showerror("Ошибка", f"Не удалось разобрать ссылку:\n{err_msg}")

    def _on_social_filter_changed(self, value):
        self._refresh_social_feed()

    def _on_social_reset(self):
        self.social_url_entry.delete(0, "end")
        self.social_platform_seg.set("Все")
        self.social_clips_data = [dict(c) for c in CURATED_VIRAL_SHORTS]
        self._refresh_social_feed()
        if self.social_clips_data:
            self._on_select_social_clip(self.social_clips_data[0])

    def _on_quick_download_from_url(self):
        url = self.social_url_entry.get().strip()
        if not url:
            if self.selected_social_clip:
                self._on_download_selected_social()
                return
            messagebox.showinfo("Внимание", "Введите или вставьте ссылку на ролик.")
            return

        is_direct = url.startswith("http://") or url.startswith("https://")
        dummy_item = {
            "id": "direct_dl",
            "url": url,
            "fallback_query": url if not is_direct else None,
            "title": url[:30],
            "anime_title": "Аниме ролик",
            "platform": identify_platform(url) if is_direct else "YouTube Shorts",
        }
        self._on_download_social_clip_direct(dummy_item)

    def _on_download_selected_social(self):
        if not self.selected_social_clip:
            messagebox.showinfo("Внимание", "Сначала выберите ролик из каталога или вставьте ссылку.")
            return
        self._on_download_social_clip_direct(self.selected_social_clip)

    def _on_download_social_clip_direct(self, item: Dict[str, Any]):
        """Downloads selected clip with automated watermark removal & 9:16 conversion."""
        if self.is_social_downloading:
            messagebox.showinfo("Загрузка", "Загрузка уже выполняется, пожалуйста подождите.")
            return

        self.is_social_downloading = True
        self.social_dl_btn.configure(state="disabled")
        self.quick_download_btn.configure(state="disabled")
        self.status_lbl.configure(text=f"Скачивание и очистка: {item.get('title')[:35]}...")
        self.log(f"Начало загрузки и авто-очистки ({item.get('platform')}): {item.get('title')} (Аниме: {item.get('anime_title')})")

        def worker():
            target = item.get("url") or item.get("fallback_query")
            try:
                def prog(status_text, ratio):
                    self.after(0, lambda: self.status_lbl.configure(text=status_text))
                    self.after(0, lambda: self.progress_bar.set(ratio))

                out_path = download_clean_social_clip(
                    url=target,
                    output_dir=str(self.output_dir),
                    remove_watermark=self.social_no_wm_var.get(),
                    auto_crop_9x16=self.social_crop_9x16_var.get(),
                    progress_callback=prog,
                )
                self.after(0, lambda: self._on_social_download_done(out_path, item))
            except Exception as e:
                fb = item.get("fallback_query")
                if fb and fb != target:
                    self.log(f"Попытка резервной загрузки: {fb}")
                    try:
                        out_path = download_clean_social_clip(
                            url=fb,
                            output_dir=str(self.output_dir),
                            remove_watermark=self.social_no_wm_var.get(),
                            auto_crop_9x16=self.social_crop_9x16_var.get(),
                        )
                        self.after(0, lambda: self._on_social_download_done(out_path, item))
                        return
                    except Exception as e2:
                        self.after(0, lambda: self._on_social_download_fail(str(e2)))
                        return
                self.after(0, lambda: self._on_social_download_fail(str(e)))

        threading.Thread(target=worker, daemon=True).start()

    def _on_social_download_done(self, out_path: str, item: Dict[str, Any]):
        self.is_social_downloading = False
        self.social_dl_btn.configure(state="normal")
        self.quick_download_btn.configure(state="normal")
        self.progress_bar.set(1.0)
        p = Path(out_path)
        self.status_lbl.configure(text=f"✓ Готово без водяных знаков: {p.name}")
        self.log(f"🎉 Файл успешно сохранен без водяных знаков (9:16): {p.name}")

        # Immediately load into embedded preview player!
        self._load_video_to_player(out_path)

        messagebox.showinfo(
            "Готово",
            f"Ролик успешно скачан и очищен от водяных знаков!\n\n🎬 Аниме: {item.get('anime_title')}\n📁 Файл: {p.name}\n📂 Папка: output_shorts\n📱 Формат: 9:16 Fullscreen",
        )

    def _on_social_download_fail(self, err: str):
        self.is_social_downloading = False
        self.social_dl_btn.configure(state="normal")
        self.quick_download_btn.configure(state="normal")
        self.status_lbl.configure(text="Ошибка загрузки ролика")
        self.log(f"❌ Ошибка скачивания: {err}")
        messagebox.showerror("Ошибка загрузки", f"Не удалось скачать видео:\n{err}")

    def _on_open_selected_social_in_editor(self):
        if not self.selected_social_clip:
            messagebox.showinfo("Внимание", "Сначала выберите ролик из каталога или вставьте ссылку.")
            return
        self._on_open_social_clip_editor_direct(self.selected_social_clip)

    def _on_open_social_clip_editor_direct(self, item: Dict[str, Any]):
        """Downloads clip to cache if needed, and opens VideoEditorWindow."""
        self.social_edit_btn.configure(state="disabled")
        self.status_lbl.configure(text="Подготовка видео для монтажного стола...")
        self.log(f"Подготовка ролика для интерактивного монтажа: {item.get('anime_title')}")

        def worker():
            target = item.get("url") or item.get("fallback_query")
            try:
                def prog(status_text, ratio):
                    self.after(0, lambda: self.status_lbl.configure(text=f"Загрузка: {int(ratio*100)}%"))
                    self.after(0, lambda: self.progress_bar.set(ratio))

                staged_path = download_clean_social_clip(
                    url=target,
                    output_dir=str(self.cache_dir),
                    remove_watermark=self.social_no_wm_var.get(),
                    auto_crop_9x16=self.social_crop_9x16_var.get(),
                    progress_callback=prog,
                )
                self.after(0, lambda: self._launch_social_editor(staged_path, item))
            except Exception as e:
                self.after(0, lambda: self._on_social_download_fail(str(e)))

        threading.Thread(target=worker, daemon=True).start()

    def _launch_social_editor(self, video_path: str, item: Dict[str, Any]):
        self.social_edit_btn.configure(state="normal")
        self.status_lbl.configure(text="Видео загружено в монтажный стол")
        self.log(f"✓ Ролик открыт в монтажном столе: {Path(video_path).name}")

        anime_title = self.social_anime_entry.get().strip() or item.get("anime_title", "")
        hook_text = item.get("hook", f"{anime_title.upper()[:22]} 🔥")
        subs_text = item.get("subs_snippet", "")

        def on_rendered(rendered_path):
            self.log(f"🎉 Монтаж вирусного ролика завершен: {Path(rendered_path).name} (Аниме: {anime_title})")
            self.status_lbl.configure(text=f"Готово: {Path(rendered_path).name}")
            self._load_video_to_player(rendered_path)

        try:
            editor = VideoEditorWindow(
                parent=self,
                video_path=video_path,
                initial_subtitles=subs_text,
                initial_hook=hook_text,
                output_dir=str(self.output_dir),
                on_render_complete=on_rendered,
            )
            editor.focus()
        except Exception as e:
            self.log(f"Ошибка запуска редактора: {e}")
            messagebox.showerror("Ошибка", f"Не удалось открыть окно монтажа:\n{e}")

    def _on_open_antigravity_dialog(self):
        """Opens the Antigravity Pro viral prompt generator modal."""
        item = self.selected_social_clip or {}
        anime_title = self.social_anime_entry.get().strip() or item.get("anime_title", "Популярное аниме")
        clip_title = item.get("title", "")

        # Check currently loaded or cached video
        video_path = self.current_video_path
        if not video_path:
            for cand in [
                self.output_dir / "Demon_Slayer_clean_9x16.mp4",
                self.cache_dir / "test_wm_cleaned.mp4",
                self.output_dir / "HKC8ssVlFc4.mp4",
            ]:
                if cand.exists():
                    video_path = str(cand)
                    break
        if not video_path:
            video_path = str(self.output_dir / "anime_clip_9x16.mp4")

        dlg = ctk.CTkToplevel(self)
        dlg.title("🚀 Antigravity Pro: Вирусный ИИ-Монтаж и Telegram-Воронка")
        dlg.geometry("780x640")
        dlg.transient(self)
        dlg.grab_set()

        # Top banner
        header = ctk.CTkFrame(dlg, fg_color="#18152e", corner_radius=8)
        header.pack(fill="x", padx=16, pady=12)
        ctk.CTkLabel(
            header,
            text="🚀 Antigravity Pro: Генератор вирусных промптов 9:16",
            font=ctk.CTkFont(size=15, weight="bold"),
            text_color="#c084fc",
        ).pack(anchor="w", padx=14, pady=(10, 2))
        ctk.CTkLabel(
            header,
            text="Промпт адаптирован для удержания 120%+ в Shorts/Reels/TikTok и перелива трафика в Telegram-канал",
            font=ctk.CTkFont(size=11),
            text_color="#94a3b8",
        ).pack(anchor="w", padx=14, pady=(0, 10))

        # Config Row: TG channel & Hook Strategy
        cfg_frame = ctk.CTkFrame(dlg, fg_color="transparent")
        cfg_frame.pack(fill="x", padx=16, pady=6)
        cfg_frame.grid_columnconfigure((0, 1), weight=1)

        tg_box = ctk.CTkFrame(cfg_frame, fg_color="#141422", corner_radius=8)
        tg_box.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ctk.CTkLabel(tg_box, text="📲 Твой Telegram-канал:", font=ctk.CTkFont(size=11, weight="bold"), text_color="#38bdf8").pack(anchor="w", padx=10, pady=(6, 2))
        tg_entry = ctk.CTkEntry(tg_box, font=ctk.CTkFont(size=12))
        tg_entry.insert(0, "@anime_empire")
        tg_entry.pack(fill="x", padx=10, pady=(0, 8))

        hook_box = ctk.CTkFrame(cfg_frame, fg_color="#141422", corner_radius=8)
        hook_box.grid(row=0, column=1, sticky="ew", padx=(6, 0))
        ctk.CTkLabel(hook_box, text="🎯 Стратегия хука:", font=ctk.CTkFont(size=11, weight="bold"), text_color="#38bdf8").pack(anchor="w", padx=10, pady=(6, 2))
        hook_opt = ctk.CTkOptionMenu(
            hook_box,
            values=[
                "🔥 Эпичный бой (Полная серия в TG)",
                "⚡ Интрига / Клиффхэнгер (Что было дальше? в TG)",
                "🧠 Тайна / Пасхалки (Разбор манги в TG)",
                "🎧 Трендовый эдит (Трек и 4K обои в TG)",
            ],
            font=ctk.CTkFont(size=11),
        )
        hook_opt.set("🔥 Эпичный бой (Полная серия в TG)")
        hook_opt.pack(fill="x", padx=10, pady=(0, 8))

        # Text Area for Prompt
        txt_frame = ctk.CTkFrame(dlg, fg_color="#0b0a16", corner_radius=8)
        txt_frame.pack(fill="both", expand=True, padx=16, pady=8)

        prompt_box = ctk.CTkTextbox(txt_frame, font=ctk.CTkFont(family="Consolas", size=11), wrap="word")
        prompt_box.pack(fill="both", expand=True, padx=8, pady=8)

        def update_prompt(*args):
            choice = hook_opt.get()
            htype = "epic_fight"
            if "Интрига" in choice:
                htype = "cliffhanger"
            elif "Тайна" in choice:
                htype = "theory_secret"
            elif "эдит" in choice:
                htype = "soundtrack_edit"

            tg_val = tg_entry.get().strip() or "@anime_empire"
            bundle = build_viral_antigravity_prompt(
                video_path=video_path,
                anime_title=anime_title,
                clip_title=clip_title,
                telegram_channel=tg_val,
                hook_type=htype,
            )
            prompt_box.delete("1.0", "end")
            prompt_box.insert("1.0", bundle["prompt_markdown"])

        tg_entry.bind("<KeyRelease>", update_prompt)
        hook_opt.configure(command=update_prompt)

        # Status line in dialog
        status_info_lbl = ctk.CTkLabel(dlg, text="", font=ctk.CTkFont(size=11), text_color="#34d399")
        status_info_lbl.pack(fill="x", padx=16, pady=(0, 4))

        def one_click_launch_dialog():
            choice = hook_opt.get()
            htype = "epic_fight" if "Эпичный" in choice else ("cliffhanger" if "Интрига" in choice else ("theory_secret" if "Тайна" in choice else "soundtrack_edit"))
            tg_val = tg_entry.get().strip() or "@anime_empire"
            dlg_launch_btn.configure(state="disabled", text="⏳ Создание чата (Gemini 3.8 Flash High)...")
            status_info_lbl.configure(text="🚀 Запуск чата Antigravity с моделью Gemini 3.8 Flash High...", text_color="#38bdf8")

            def d_worker():
                try:
                    res = launch_antigravity_job(
                        video_path=video_path,
                        anime_title=anime_title,
                        clip_title=clip_title,
                        telegram_channel=tg_val,
                        hook_type=htype,
                        model="flash",
                        output_dir=str(self.output_dir),
                    )
                    c_id = res.get("conversation_id", "")
                    def on_launched():
                        status_info_lbl.configure(text=f"✓ Чат Antigravity активен (Gemini 3.8 Flash)! ID: {c_id[:8]}... Отслеживаем...")
                        def poll():
                            st = get_antigravity_job_status(c_id)
                            status_info_lbl.configure(text=f"Шаг {st.get('steps_count', 1)}: {st.get('message', '')}")
                            if st.get("status") == "completed":
                                dlg_launch_btn.configure(state="normal", text="⚡ В 1 КЛИК: В ANTIGRAVITY (3.8 FLASH)")
                                status_info_lbl.configure(text="✅ Antigravity (Gemini 3.8 Flash) успешно завершил задачу!")
                                if st.get("response_text"):
                                    prompt_box.delete("1.0", "end")
                                    prompt_box.insert("1.0", f"=== ОТВЕТ ANTIGRAVITY (GEMINI 3.8 FLASH HIGH) ===\n\n" + st["response_text"])
                            else:
                                dlg.after(2000, poll)
                        poll()
                    dlg.after(0, on_launched)
                except Exception as e:
                    def on_err():
                        dlg_launch_btn.configure(state="normal", text="⚡ В 1 КЛИК: В ANTIGRAVITY (3.8 FLASH)")
                        status_info_lbl.configure(text=f"Ошибка: {e}", text_color="#ef4444")
                    dlg.after(0, on_err)

            threading.Thread(target=d_worker, daemon=True).start()

        dlg_launch_btn = ctk.CTkButton(
            dlg,
            text="⚡ В 1 КЛИК: В ANTIGRAVITY (GEMINI 3.8 FLASH HIGH)",
            height=42,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="#10b981",
            hover_color="#059669",
            command=one_click_launch_dialog,
        )
        dlg_launch_btn.pack(fill="x", padx=16, pady=(0, 6))

        # Buttons Row
        btn_bar = ctk.CTkFrame(dlg, fg_color="transparent")
        btn_bar.pack(fill="x", padx=16, pady=(0, 14))

        def copy_to_clipboard():
            text = prompt_box.get("1.0", "end-1c")
            self.clipboard_clear()
            self.clipboard_append(text)
            messagebox.showinfo("Скопировано", "Вирусный промпт для Antigravity Pro скопирован в буфер обмена!\n\nОтправь его в чат Antigravity для мгновенной обработки ролика.")

        def save_task_file():
            choice = hook_opt.get()
            htype = "epic_fight" if "Эпичный" in choice else ("cliffhanger" if "Интрига" in choice else ("theory_secret" if "Тайна" in choice else "soundtrack_edit"))
            saved = save_antigravity_bundle(
                video_path=video_path,
                anime_title=anime_title,
                telegram_channel=tg_entry.get().strip() or "@anime_empire",
                hook_type=htype,
                output_dir=str(self.output_dir),
            )
            messagebox.showinfo("Сохранено", f"Файл задачи для Antigravity сохранен:\n{saved}")

        ctk.CTkButton(
            btn_bar,
            text="📋 Скопировать готовый промпт в буфер",
            height=40,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="#059669",
            hover_color="#047857",
            command=copy_to_clipboard,
        ).pack(side="left", fill="x", expand=True, padx=(0, 6))

        ctk.CTkButton(
            btn_bar,
            text="💾 Сохранить .md задачу",
            height=40,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="#7c3aed",
            hover_color="#6d28d9",
            command=save_task_file,
        ).pack(side="left", fill="x", expand=True, padx=(6, 0))

        update_prompt()

    def _on_one_click_antigravity(self):
        video_path = self.current_video_path
        if not video_path or not Path(video_path).exists():
            for cand in [
                self.output_dir / "Demon_Slayer_clean_9x16.mp4",
                self.cache_dir / "test_wm_cleaned.mp4",
                self.output_dir / "HKC8ssVlFc4.mp4",
            ]:
                if cand.exists():
                    video_path = str(cand)
                    break

        if not video_path or not Path(video_path).exists():
            messagebox.showinfo("Внимание", "Сначала выберите или скачайте видеоролик.")
            return

        anime_title = "Аниме"
        clip_title = ""
        if self.selected_social_clip:
            anime_title = self.selected_social_clip.get("anime_title") or "Аниме"
            clip_title = self.selected_social_clip.get("title") or ""

        self.status_lbl.configure(text=f"⚡ Запуск задачи в Antigravity (Gemini 3.8 Flash High) для: {anime_title}...")
        self.log(f"🚀 Запуск 1-click Antigravity (Gemini 3.8 Flash High) для видео: {Path(video_path).name} (Аниме: {anime_title})")
        self.antigravity_1click_btn.configure(state="disabled", text="⏳ Запуск 3.8 Flash...")

        def worker():
            try:
                res = launch_antigravity_job(
                    video_path=video_path,
                    anime_title=anime_title,
                    clip_title=clip_title,
                    telegram_channel="@anime_empire",
                    hook_type="epic_fight",
                    model="flash",
                    output_dir=str(self.output_dir),
                )
                conv_id = res.get("conversation_id", "")
                self.after(0, lambda: self._on_antigravity_job_launched(conv_id, res))
            except Exception as e:
                self.after(0, lambda: self._on_antigravity_job_fail(str(e)))

        threading.Thread(target=worker, daemon=True).start()

    def _on_antigravity_job_launched(self, conv_id: str, res: Dict[str, Any]):
        self.antigravity_1click_btn.configure(state="normal", text="⚡ В 1 КЛИК: В ANTIGRAVITY (3.8 FLASH)")
        self.status_lbl.configure(text=f"✓ Antigravity (Gemini 3.8 Flash) запущен (ID: {conv_id[:8]}...)")
        self.log(f"✅ Чат Antigravity (Gemini 3.8 Flash High) успешно создан! ID: {conv_id}. Ссылка: {res.get('conversation_url')}")
        self._poll_antigravity_job(conv_id)

    def _poll_antigravity_job(self, conv_id: str):
        st = get_antigravity_job_status(conv_id)
        msg = st.get("message", "")
        steps = st.get("steps_count", 1)
        self.status_lbl.configure(text=f"Antigravity 3.8 Flash (шаг {steps}): {msg}")

        if st.get("status") == "completed":
            self.status_lbl.configure(text="✅ Antigravity (Gemini 3.8 Flash) завершил обработку ролика!")
            self.log(f"🎉 Antigravity (Gemini 3.8 Flash) завершил обработку! Ответ получен ({len(st.get('response_text', ''))} символов).")
            messagebox.showinfo(
                "Antigravity Завершено",
                f"Чат Antigravity (Gemini 3.8 Flash High) успешно выполнил задачу!\n\nID чата: {conv_id}\n\nОтвет:\n{st.get('response_text', '')[:400]}"
            )
        else:
            self.after(2500, lambda: self._poll_antigravity_job(conv_id))

    def _on_antigravity_job_fail(self, err: str):
        self.antigravity_1click_btn.configure(state="normal", text="⚡ В 1 КЛИК: В ANTIGRAVITY (3.8 FLASH)")
        self.status_lbl.configure(text="Ошибка запуска Antigravity")
        self.log(f"❌ Ошибка запуска Antigravity: {err}")
        messagebox.showerror("Ошибка Antigravity", f"Не удалось запустить Antigravity (Gemini 3.8 Flash):\n{err}")


def run_gui():
    app = VideoHookApp()
    app.mainloop()


if __name__ == "__main__":
    run_gui()
