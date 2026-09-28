"""
Viral Shorts & Telegram Funnel Generator for Demon Slayer (9:16)
Fully automated according to antigravity_task_Demon_Slayer_clean_9x16.md
"""

import os
import sys
import time
from pathlib import Path

# Ensure UTF-8 output encoding for Windows terminal
if sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from moviepy.editor import VideoFileClip, CompositeAudioClip
from moviepy.audio.AudioClip import AudioArrayClip

INPUT_FILE = Path("D:/videohook/output_shorts/Demon_Slayer_clean_9x16.mp4")
OUTPUT_FILE = Path("D:/videohook/output_shorts/Demon_Slayer_clean_9x16_viral_tg.mp4")
TG_CHANNEL = "@anime_empire"
ANIME_TITLE_RU = "Клинок, рассекающий демонов"
ANIME_TITLE_EN = "(Demon Slayer)"

def check_input_file():
    if not INPUT_FILE.exists():
        raise FileNotFoundError(f"Input video not found: {INPUT_FILE}")
    print(f"[OK] Input video found: {INPUT_FILE} ({INPUT_FILE.stat().st_size / (1024*1024):.2f} MB)")

def draw_telegram_icon(draw, cx, cy, radius):
    """Draws a crisp Telegram circle with paper airplane logo."""
    draw.ellipse([cx - radius, cy - radius, cx + radius, cy + radius], fill=(36, 161, 222))
    scale = radius / 20.0
    p1 = (cx - 9 * scale, cy - 1 * scale)
    p2 = (cx + 9 * scale, cy - 8 * scale)
    p3 = (cx - 3 * scale, cy + 8 * scale)
    p4 = (cx - 2 * scale, cy + 1 * scale)
    draw.polygon([p1, p2, p4], fill=(235, 235, 235))
    draw.polygon([p4, p2, p3], fill=(255, 255, 255))
    draw.polygon([(cx - 2 * scale, cy + 1 * scale), (cx - 3 * scale, cy + 8 * scale), (cx + 1 * scale, cy + 3 * scale)], fill=(190, 215, 240))

def create_top_pill(w=1080):
    """Top banner pill: 🎬 Клинок, рассекающий демонов (Demon Slayer)"""
    pill_w, pill_h = 760, 68
    px = (w - pill_w) // 2
    py = 45
    img = Image.new("RGBA", (pill_w, pill_h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, pill_w, pill_h], radius=34, fill=(15, 15, 22, 230), outline=(220, 38, 38, 240), width=3)
    
    font_emoji = ImageFont.truetype("C:/Windows/Fonts/seguiemj.ttf", 34)
    font_title = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", 28)
    font_sub = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 26)
    
    d.text((24, 15), "\U0001F3AC", font=font_emoji, fill=(255, 255, 255))
    d.text((74, 18), ANIME_TITLE_RU, font=font_title, fill=(255, 255, 255))
    d.text((520, 19), ANIME_TITLE_EN, font=font_sub, fill=(250, 204, 21))
    return img, px, py

def create_hook_badge(w=1080):
    """Hook overlay for seconds 0-3.5: 🔥 САМЫЙ ЖЕСТОКИЙ И КРАСИВЫЙ БОЙ В ИСТОРИИ АНИМЕ"""
    badge_w, badge_h = w - 120, 190
    bx, by = 60, 140
    img = Image.new("RGBA", (badge_w, badge_h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, badge_w, badge_h], radius=24, fill=(12, 14, 22, 230), outline=(250, 204, 21, 240), width=3)
    
    font_hook_emoji = ImageFont.truetype("C:/Windows/Fonts/seguiemj.ttf", 44)
    font_hook_bold = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", 40)
    d.text((badge_w // 2 - 25, 15), "\U0001F525", font=font_hook_emoji, fill=(255, 120, 0))
    
    line1 = "«САМЫЙ ЖЕСТОКИЙ И КРАСИВЫЙ»"
    line2 = "БОЙ В ИСТОРИИ АНИМЕ"
    bbox1 = d.textbbox((0,0), line1, font=font_hook_bold)
    w1 = bbox1[2] - bbox1[0]
    d.text(((badge_w - w1)//2, 75), line1, font=font_hook_bold, fill=(255, 255, 255))
    
    bbox2 = d.textbbox((0,0), line2, font=font_hook_bold)
    w2 = bbox2[2] - bbox2[0]
    d.text(((badge_w - w2)//2, 125), line2, font=font_hook_bold, fill=(250, 204, 21))
    return img, bx, by

def create_mid_pill(w=1080, h=1920):
    """Subtle lower pill during main action (3.5s - 23s)"""
    bot_w, bot_h = 520, 68
    bx = (w - bot_w) // 2
    by = h - 220
    img = Image.new("RGBA", (bot_w, bot_h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, bot_w, bot_h], radius=34, fill=(12, 16, 28, 230), outline=(56, 189, 248, 220), width=2)
    draw_telegram_icon(d, 42, 34, 20)
    font_bot = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", 30)
    d.text((80, 18), f"Telegram: {TG_CHANNEL}", font=font_bot, fill=(255, 220, 50))
    return img, bx, by

def create_cta_card(w=1080, h=1920):
    """Full high-converting CTA card for the climax / ending (last 6.3 seconds)"""
    card_w, card_h = w - 80, 310
    cx = 40
    cy = h - card_h - 70
    img = Image.new("RGBA", (card_w, card_h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, card_w, card_h], radius=28, fill=(12, 16, 26, 238), outline=(56, 189, 248, 240), width=3)
    
    # TG Pill inside card
    pill_w, pill_h = 440, 56
    pl_x = (card_w - pill_w) // 2
    pl_y = 20
    d.rounded_rectangle([pl_x, pl_y, pl_x + pill_w, pl_y + pill_h], radius=28, fill=(22, 28, 44, 255), outline=(56, 189, 248, 200), width=2)
    draw_telegram_icon(d, pl_x + 35, pl_y + 28, 18)
    font_tg = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", 32)
    d.text((pl_x + 70, pl_y + 10), TG_CHANNEL, font=font_tg, fill=(255, 215, 0))
    
    # CTA Lines
    line1 = "«Полную серию в 4K 60FPS без цензуры"
    line2 = "и с русской озвучкой выложил в Telegram»"
    font_main = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", 30)
    bbox1 = d.textbbox((0,0), line1, font=font_main)
    w1 = bbox1[2] - bbox1[0]
    d.text(((card_w - w1)//2, 95), line1, font=font_main, fill=(255, 255, 255))
    
    bbox2 = d.textbbox((0,0), line2, font=font_main)
    w2 = bbox2[2] - bbox2[0]
    d.text(((card_w - w2)//2, 140), line2, font=font_main, fill=(255, 255, 255))
    
    # Sub Callout with clean emoji indicators
    sub_text = "Ссылка на канал в описании / шапке профиля"
    font_sub = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", 24)
    font_emoji_sub = ImageFont.truetype("C:/Windows/Fonts/seguiemj.ttf", 24)
    bbox_sub = d.textbbox((0,0), sub_text, font=font_sub)
    sw = bbox_sub[2] - bbox_sub[0]
    start_x = (card_w - sw) // 2
    d.text((start_x - 34, 195), "\U0001F449", font=font_emoji_sub, fill=(56, 189, 248))
    d.text((start_x, 195), sub_text, font=font_sub, fill=(56, 189, 248))
    d.text((start_x + sw + 8, 195), "\U0001F448", font=font_emoji_sub, fill=(56, 189, 248))
    
    return img, cx, cy

def create_arrow_sprite():
    """Red neon pointer arrow for CTA"""
    aw, ah = 70, 50
    img = Image.new("RGBA", (aw, ah), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pts = [(6, 8), (aw - 6, 8), (aw // 2, ah - 8)]
    d.polygon(pts, fill=(239, 68, 68))
    d.polygon(pts, outline=(255, 255, 255), width=2)
    return img

def blend_patch(bg, sprite_rgb, sprite_alpha, x, y, opacity=1.0):
    """Blends a transparent sprite patch onto background buffer with high speed."""
    sh, sw = sprite_rgb.shape[:2]
    x1, y1 = max(0, x), max(0, y)
    x2, y2 = min(bg.shape[1], x + sw), min(bg.shape[0], y + sh)
    if x1 >= x2 or y1 >= y2:
        return
    sx1, sy1 = x1 - x, y1 - y
    sx2, sy2 = sx1 + (x2 - x1), sy1 + (y2 - y1)
    
    alpha = sprite_alpha[sy1:sy2, sx1:sx2] * opacity
    bg_roi = bg[y1:y2, x1:x2].astype(np.float32)
    fg_roi = sprite_rgb[sy1:sy2, sx1:sx2].astype(np.float32)
    bg[y1:y2, x1:x2] = (bg_roi * (1.0 - alpha) + fg_roi * alpha).astype(np.uint8)

def synthesize_impact_audio(fps=44100, dur=1.2):
    """Synthesizes cinema trailer sub-bass punch & whoosh riser for shock-factor hook."""
    t = np.linspace(0, dur, int(fps * dur), endpoint=False)
    freq = 120 * np.exp(-t * 2.2) + 40
    phase = 2 * np.pi * np.cumsum(freq) / fps
    bass_env = np.exp(-t * 3.2) * np.minimum(t / 0.04, 1.0)
    bass = np.sin(phase) * bass_env * 0.45
    
    noise = np.random.uniform(-1, 1, len(t))
    kernel = np.ones(40) / 40.0
    noise_smooth = np.convolve(noise, kernel, mode="same")
    whoosh_env = np.where(t < 0.35, np.sin(np.pi * t / 0.7), np.exp(-(t - 0.35) * 5.0))
    whoosh = noise_smooth * whoosh_env * 0.22
    
    impact = bass + whoosh
    stereo = np.column_stack([impact, impact])
    return AudioArrayClip(stereo, fps=fps)

def main():
    print("=" * 60)
    print("🚀 VideoHook - Antigravity Autonomous Viral Video Pipeline")
    print("=" * 60)
    check_input_file()
    
    print("\n[1/5] Loading input video & metadata...")
    clip = VideoFileClip(str(INPUT_FILE))
    w, h = clip.size
    duration = clip.duration
    fps = clip.fps
    print(f"Video resolution: {w}x{h} (9:16 target), FPS: {fps}, Duration: {duration:.2f}s")
    
    # 2. Precompute sprites
    print("\n[2/5] Pre-rendering viral overlays & retention components...")
    top_img, top_x, top_y = create_top_pill(w)
    top_rgb, top_a = np.array(top_img)[:, :, :3], (np.array(top_img)[:, :, 3:4] / 255.0).astype(np.float32)
    
    hook_img, hook_x, hook_y = create_hook_badge(w)
    hook_rgb, hook_a = np.array(hook_img)[:, :, :3], (np.array(hook_img)[:, :, 3:4] / 255.0).astype(np.float32)
    
    mid_img, mid_x, mid_y = create_mid_pill(w, h)
    mid_rgb, mid_a = np.array(mid_img)[:, :, :3], (np.array(mid_img)[:, :, 3:4] / 255.0).astype(np.float32)
    
    cta_img, cta_x, cta_y = create_cta_card(w, h)
    cta_rgb, cta_a = np.array(cta_img)[:, :, :3], (np.array(cta_img)[:, :, 3:4] / 255.0).astype(np.float32)
    
    arr_img = create_arrow_sprite()
    arr_rgb, arr_a = np.array(arr_img)[:, :, :3], (np.array(arr_img)[:, :, 3:4] / 255.0).astype(np.float32)
    arr_w, arr_h = arr_img.size
    
    # 3. Precompute Color Grading (Anime Pop CC: Saturation +15%, Contrast +10%, Vignette)
    print("Precomputing Anime Pop CC (Contrast +10%, Saturation +15%, Vignette)...")
    lut_sat = np.clip(np.arange(256) * 1.15, 0, 255).astype(np.uint8)
    lut_val = np.clip((np.arange(256) - 128) * 1.10 + 128, 0, 255).astype(np.uint8)
    
    Y, X = np.ogrid[:h, :w]
    cx, cy = w / 2, h / 2
    dist_sq = ((X - cx) / (w * 0.65)) ** 2 + ((Y - cy) / (h * 0.65)) ** 2
    vignette_mask = np.clip(1.0 - dist_sq * 0.18, 0.82, 1.0).astype(np.float32)[:, :, np.newaxis]
    
    # 4. Audio impact mixing
    print("\n[3/5] Mixing sub-bass whoosh / impact riser into audio track...")
    impact_clip = synthesize_impact_audio(dur=1.2)
    if clip.audio is not None:
        final_audio = CompositeAudioClip([clip.audio, impact_clip.set_start(0.0)])
    else:
        final_audio = impact_clip
    
    # 5. Frame processing function
    print("\n[4/5] Processing frames with dynamic retention architecture...")
    
    def process_frame(get_frame, t):
        frame = get_frame(t).copy()
        
        # 1. Shock Hook Zoom (0 - 3.0s: 1.05x -> 1.15x focused on character)
        if t < 3.0:
            zoom = 1.05 + 0.10 * (t / 3.0)
            crop_w = int(w / zoom)
            crop_h = int(h / zoom)
            x1 = (w - crop_w) // 2
            y1 = int((h - crop_h) * 0.40)
            cropped = frame[y1:y1 + crop_h, x1:x1 + crop_w]
            frame = cv2.resize(cropped, (w, h), interpolation=cv2.INTER_LINEAR)
        
        # 2. Anime Pop CC: Saturation + Contrast + Vignette
        hsv = cv2.cvtColor(frame, cv2.COLOR_RGB2HSV)
        hsv[:, :, 1] = cv2.LUT(hsv[:, :, 1], lut_sat)
        hsv[:, :, 2] = cv2.LUT(hsv[:, :, 2], lut_val)
        frame = cv2.cvtColor(hsv, cv2.COLOR_HSV2RGB)
        frame = (frame.astype(np.float32) * vignette_mask).astype(np.uint8)
        
        # 3. Top Banner (🎬 Клинок, рассекающий демонов (Demon Slayer))
        blend_patch(frame, top_rgb, top_a, top_x, top_y)
        
        # 4. Hook Banner in upper third (0.0 - 3.5s)
        if t < 3.5:
            opacity = 1.0 if t < 3.0 else max(0.0, (3.5 - t) / 0.5)
            blend_patch(frame, hook_rgb, hook_a, hook_x, hook_y, opacity=opacity)
        
        # 5. Mid-video lower Telegram pill vs Climax CTA Card
        if t < 23.0:
            if t >= 3.5:
                blend_patch(frame, mid_rgb, mid_a, mid_x, mid_y)
        else:
            # Climax CTA Card (last ~6.3 seconds)
            blend_patch(frame, cta_rgb, cta_a, cta_x, cta_y)
            # Animated bouncing arrow
            bounce_y = int(8 * np.sin((t - 23.0) * 8.0))
            arrow_px = (w - arr_w) // 2
            arrow_py = cta_y + 255 + bounce_y
            blend_patch(frame, arr_rgb, arr_a, arrow_px, arrow_py)
            
        return frame

    # 6. Apply transformation & Export
    final_video = clip.fl(process_frame)
    final_video = final_video.set_audio(final_audio)
    
    print("\n[5/5] Exporting viral shorts to output directory...")
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    final_video.write_videofile(
        str(OUTPUT_FILE),
        fps=fps,
        codec="libx264",
        audio_codec="aac",
        preset="fast",
        bitrate="6000k",
        threads=4,
        logger="bar",
    )
    
    clip.close()
    final_video.close()
    
    print("\n" + "=" * 60)
    print("✅ VIRAL SHORT EXPORT COMPLETE!")
    print(f"File: {OUTPUT_FILE}")
    print(f"Size: {OUTPUT_FILE.stat().st_size / (1024*1024):.2f} MB")
    print("=" * 60)

if __name__ == "__main__":
    main()
