"""Локальный рендер вертикального ролика 9:16 (FFmpeg + ASS-оверлеи через libass).

Шаблоны «подложки» — трансформирующее оформление, которое сохраняет суть сцены и добавляет
авторский слой (заголовок, контекст, подпись, кредит правообладателю, прогресс-бар):
  cinema      — размытый фон + кадр 16:9 по центру + плашки сверху/снизу (самый безопасный);
  fullscreen  — полноэкранный кроп 9:16 с умным центрированием на персонаже;
  frame       — фирменная рамка/фон канала + кадр по центру;
  commentary  — кадр сверху + блок авторского комментария снизу (максимум «своего» контента).
"""

from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .core import FONTS_DIR, MUSIC_DIR, RENDERS_DIR, TMP_DIR, new_id, probe, run_ffmpeg

log = logging.getLogger("videohook.render")

W, H = 1080, 1920
FONT_NAME = "Rubik ExtraBold"  # семейство из assets/fonts (OFL)
TEMPLATES = {
    "cinema": "Кино: размытый фон + кадр по центру",
    "fullscreen": "Во весь экран (умный кроп)",
    "frame": "Фирменная рамка канала",
    "commentary": "Кадр + авторский комментарий",
}
MUSIC_TRACKS = {
    "epic_orchestral": "Эпичный оркестр",
    "phonk_bass": "Фонк / бас",
    "lofi_chill": "Lo-Fi / эмоции",
}

_EMOJI = re.compile("[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0000FE0F\U0000200D\U00002B00-\U00002BFF]+")


def clean_text(text: str) -> str:
    """Для прожига в кадр: без эмодзи (шрифт их не отрисует) и без ASS-управляющих символов."""
    t = _EMOJI.sub("", text or "")
    t = t.replace("{", "(").replace("}", ")").replace("\\", "/").replace("\n", " ")
    return re.sub(r"\s+", " ", t).strip()


def music_path(choice: str) -> Optional[Path]:
    if not choice or choice == "none":
        return None
    p = Path(choice)
    if p.is_absolute() and p.exists():
        return p
    cand = MUSIC_DIR / f"{choice}.mp3"
    return cand if cand.exists() else None


# ---------------------------------------------------------------------------
# Анализ кадра: умный кроп, точка зацикливания, обложка
# ---------------------------------------------------------------------------
def _frames(path: str, times: List[float], size: Tuple[int, int] = (320, 180)):
    import cv2

    cap = cv2.VideoCapture(str(path))
    out = []
    for t in times:
        cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, t) * 1000)
        ok, frame = cap.read()
        out.append(cv2.resize(frame, size) if ok and frame is not None else None)
    cap.release()
    return out


def subject_center(path: str, start: float, end: float, samples: int = 14) -> float:
    """Горизонтальный центр главного объекта (0..1) по салиентности столбцов — медиана по кадрам."""
    import cv2
    import numpy as np

    times = [start + (end - start) * (i + 0.5) / samples for i in range(samples)]
    centers = []
    for fr in _frames(path, times):
        if fr is None:
            continue
        gray = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY).astype(np.float32)
        edges = np.abs(cv2.Sobel(gray, cv2.CV_32F, 1, 0)) + np.abs(cv2.Sobel(gray, cv2.CV_32F, 0, 1))
        cols = edges.mean(axis=0) + gray.var(axis=0) * 0.02
        cols = np.convolve(cols, np.ones(15) / 15, mode="same")
        bias = np.exp(-0.5 * ((np.arange(cols.size) - cols.size / 2) / (cols.size * 0.4)) ** 2)
        weighted = cols * bias
        if weighted.max() > 1:
            centers.append(float(np.argmax(weighted)) / cols.size)
    if not centers:
        return 0.5
    centers.sort()
    return centers[len(centers) // 2]


def find_loop_end(path: str, start: float, end: float, search: float = 3.0) -> float:
    """Конец отрезка, где кадр максимально похож на первый — ролик зацикливается бесшовно."""
    import cv2
    import numpy as np

    if end - start < 8:
        return end
    first = _frames(path, [start + 0.05], (96, 54))[0]
    if first is None:
        return end
    step = 0.1
    times = [end - search + i * step for i in range(int(search / step) + 1)]
    best_t, best_d = end, None
    f0 = cv2.cvtColor(first, cv2.COLOR_BGR2GRAY).astype(np.float32)
    for t, fr in zip(times, _frames(path, times, (96, 54))):
        if fr is None:
            continue
        d = float(np.mean(np.abs(cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY).astype(np.float32) - f0)))
        # лёгкий штраф за сильное укорачивание
        d += (end - t) * 1.5
        if best_d is None or d < best_d:
            best_t, best_d = t, d
    return round(best_t, 2)


def extract_thumbnail(path: str, out_path: str, at: Optional[float] = None) -> str:
    """Самый резкий кадр из первых секунд (или в точке at) → JPG-обложка."""
    import cv2

    dur = probe(path)["duration"] or 1
    if at is None:
        times = [min(dur - 0.1, 0.5 + i * 0.4) for i in range(10)]
        best_t, best_s = times[0], -1.0
        for t, fr in zip(times, _frames(path, times, (480, 270))):
            if fr is None:
                continue
            s = cv2.Laplacian(cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var()
            if s > best_s:
                best_t, best_s = t, s
        at = best_t
    run_ffmpeg(["-ss", f"{at:.2f}", "-i", str(path), "-frames:v", "1", "-q:v", "3", str(out_path)])
    return out_path


def contact_sheet(path: str, out_path: str, cols: int = 4, rows: int = 4) -> str:
    """Раскадровка с таймкодами — отдаётся агенту Antigravity для визуального анализа."""
    dur = probe(path)["duration"] or 1
    n = cols * rows
    fps = n / dur
    run_ffmpeg([
        "-i", str(path), "-vf",
        f"fps={fps:.5f},scale=360:-2,tile={cols}x{rows}:padding=6:margin=6",
        "-frames:v", "1", "-q:v", "4", str(out_path),
    ])
    return out_path


# ---------------------------------------------------------------------------
# ASS-оверлей
# ---------------------------------------------------------------------------
def _ass_time(t: float) -> str:
    t = max(0.0, t)
    h = int(t // 3600)
    m = int(t % 3600 // 60)
    s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _color(hex_rgb: str, alpha: int = 0) -> str:
    """#RRGGBB → &HAABBGGRR (формат ASS)."""
    h = hex_rgb.lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def build_ass(params: Dict[str, Any], duration: float, video_box: Tuple[int, int, int, int]) -> str:
    """Все надписи ролика одним ASS-файлом. video_box = (x, y, w, h) кадра на холсте 1080×1920."""
    accent = params.get("accent", "#FACC15")
    template = params.get("template", "cinema")
    vx, vy, vw, vh = video_box
    top_zone = vy
    bottom_zone = vy + vh
    fullscreen = template == "fullscreen"

    styles = [
        # имя, размер, основной, контур/бокс, тень, bold, borderstyle, outline, shadow, align, marginV
        ("Hook", 66, "#111111", accent, "#000000", 1, 3, 20, 0, 8, 0),
        ("Label", 34, "#FFFFFF", "#000000", "#000000", 1, 1, 3, 0, 8, 0),
        ("Caption", 60, "#FFFFFF", "#000000", "#000000", 1, 1, 6, 2, 2, 0),
        ("Lines", 58, "#FFFFFF", "#000000", "#000000", 1, 1, 6, 2, 2, 0),
        ("Comment", 44, "#F5F5F5", "#101014", "#000000", 0, 3, 14, 0, 7, 0),
        ("Handle", 38, accent, "#000000", "#000000", 1, 1, 3, 0, 2, 0),
        ("Credit", 26, "#D4D4D8", "#000000", "#000000", 0, 1, 2, 0, 2, 0),
        ("Bar", 10, accent, accent, "#000000", 0, 1, 0, 0, 7, 0),
    ]
    lines = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {W}", f"PlayResY: {H}", "WrapStyle: 0",
        "ScaledBorderAndShadow: yes", "", "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
        "MarginR, MarginV, Encoding",
    ]
    for name, size, prim, outl, back, bold, bstyle, outline, shadow, align, mv in styles:
        lines.append(
            f"Style: {name},{FONT_NAME},{size},{_color(prim)},{_color(prim)},{_color(outl)},{_color(back, 0x80)},"
            f"{-1 if bold else 0},0,0,0,100,100,0,0,{bstyle},{outline},{shadow},{align},70,70,{mv},1"
        )
    lines += ["", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
    end = _ass_time(duration + 0.5)

    def ev(style: str, text: str, x: int, y: int, start: float = 0.0, stop: Optional[float] = None,
           extra: str = "", layer: int = 1):
        lines.append(f"Dialogue: {layer},{_ass_time(start)},{_ass_time(stop) if stop is not None else end},{style},,0,0,0,,"
                     f"{{\\pos({x},{y}){extra}}}{text}")

    anime_label = clean_text(params.get("anime_label", "")).upper()
    hook = clean_text(params.get("hook", ""))
    caption = clean_text(params.get("caption", ""))
    handle = clean_text(params.get("handle", ""))
    credit = clean_text(params.get("credit", ""))

    # --- верхняя зона: метка аниме + хук
    hook_y = max(170, top_zone - 150) if not fullscreen else 230
    if anime_label:
        ev("Label", anime_label, W // 2, hook_y - 95, extra="\\fad(200,0)")
    if hook:
        ev("Hook", hook, W // 2, hook_y, extra="\\fad(120,0)\\fscx70\\fscy70\\t(0,220,\\fscx100\\fscy100)", layer=2)

    # --- нижняя зона: реплики по таймингу или статичная подпись
    key_lines = params.get("key_lines") or []
    cap_y = bottom_zone + 170 if not fullscreen else H - 420
    if template == "commentary":
        comment = clean_text(params.get("commentary") or caption)
        if comment:
            lines.append(f"Dialogue: 1,{_ass_time(0.3)},{end},Comment,,0,0,0,,"
                         f"{{\\an7\\pos(70,{bottom_zone + 60})\\fad(300,0)}}{comment}")
    elif key_lines:
        for i, kl in enumerate(key_lines):
            st = float(kl.get("t", 0))
            nxt = float(key_lines[i + 1]["t"]) if i + 1 < len(key_lines) else min(duration, st + 3.5)
            txt = clean_text(kl.get("text", ""))
            if txt and st < duration:
                ev("Lines", txt, W // 2, cap_y, st, max(st + 0.6, min(nxt, st + 4.0)),
                   extra="\\fad(80,80)\\fscx92\\fscy92\\t(0,120,\\fscx100\\fscy100)")
    elif caption:
        ev("Caption", caption, W // 2, cap_y, 0.4, extra="\\fad(250,0)")

    # --- подпись канала и кредит правообладателю (кредит всегда внизу, мелко)
    if handle:
        ev("Handle", handle, W // 2, H - 110)
    if credit:
        ev("Credit", credit, W // 2, H - 60)

    # --- прогресс-бар (удерживает досмотр)
    if params.get("progress_bar", True):
        bar_y = H - 16
        lines.append(
            f"Dialogue: 3,{_ass_time(0)},{end},Bar,,0,0,0,,{{\\an7\\pos(0,0)\\p1\\clip(0,{bar_y},0,{H})"
            f"\\t(0,{int(duration * 1000)},\\clip(0,{bar_y},{W},{H}))}}m 0 {bar_y} l {W} {bar_y} l {W} {H} l 0 {H}{{\\p0}}"
        )
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Рендер
# ---------------------------------------------------------------------------
DEFAULT_EFFECTS = {"color_pop": True, "zoom_punch": True, "flash": False, "loudnorm": True}


def plan_segments(segments: List[Tuple[float, float]], src_dur: float, cap: float) -> List[Tuple[float, float]]:
    segs = [(max(0.0, float(s)), min(src_dur or float(e), float(e))) for s, e in segments if float(e) > float(s)]
    if not segs:
        segs = [(0.0, min(src_dur or cap, cap))]
    out, total = [], 0.0
    for s, e in segs:
        if total >= cap:
            break
        e = min(e, s + (cap - total))
        out.append((round(s, 3), round(e, 3)))
        total += e - s
    return out


def render(source: str, params: Dict[str, Any], out_path: Optional[str] = None,
           progress: Optional[Callable[[float], None]] = None) -> Dict[str, Any]:
    src = Path(source)
    if not src.exists():
        raise FileNotFoundError(f"Нет исходника: {source}")
    info = probe(src)
    template = params.get("template", "cinema") if params.get("template") in TEMPLATES else "cinema"
    effects = {**DEFAULT_EFFECTS, **(params.get("effects") or {})}
    cap = float(params.get("max_seconds") or 59)
    segments = plan_segments([tuple(s) for s in params.get("segments") or []], info["duration"], cap)

    if params.get("loop_friendly") and len(segments) == 1:
        s, e = segments[0]
        segments = [(s, find_loop_end(str(src), s, e))]

    speed = float(params.get("speed") or 1.0)
    total = sum(e - s for s, e in segments)
    duration = total / speed

    work = TMP_DIR / new_id("r_")
    (work / "fonts").mkdir(parents=True, exist_ok=True)
    for f in FONTS_DIR.glob("*.ttf"):
        shutil.copy2(f, work / "fonts" / f.name)

    RENDERS_DIR.mkdir(parents=True, exist_ok=True)
    out = Path(out_path) if out_path else RENDERS_DIR / f"{new_id('v_')}.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)

    fc: List[str] = []
    has_audio = info["has_audio"]
    # 1. нарезка и склейка
    if len(segments) == 1:
        s, e = segments[0]
        fc.append(f"[0:v]trim=start={s}:end={e},setpts=PTS-STARTPTS[vcat]")
        if has_audio:
            fc.append(f"[0:a]atrim=start={s}:end={e},asetpts=PTS-STARTPTS[acat]")
    else:
        for i, (s, e) in enumerate(segments):
            fc.append(f"[0:v]trim=start={s}:end={e},setpts=PTS-STARTPTS[v{i}]")
            if has_audio:
                fc.append(f"[0:a]atrim=start={s}:end={e},asetpts=PTS-STARTPTS[a{i}]")
        if has_audio:
            ins = "".join(f"[v{i}][a{i}]" for i in range(len(segments)))
            fc.append(f"{ins}concat=n={len(segments)}:v=1:a=1[vcat][acat]")
        else:
            ins = "".join(f"[v{i}]" for i in range(len(segments)))
            fc.append(f"{ins}concat=n={len(segments)}:v=1:a=0[vcat]")

    grade = "eq=saturation=1.18:contrast=1.06:brightness=0.01," if effects.get("color_pop") else ""
    sw, sh = info["width"] or 1920, info["height"] or 1080
    # 2. геометрия
    if template == "fullscreen":
        cx = float(params.get("crop_center") if params.get("crop_center") is not None else
                   subject_center(str(src), segments[0][0], segments[-1][1]))
        fc.append(f"[vcat]{grade}scale={W}:{H}:force_original_aspect_ratio=increase,"
                  f"crop={W}:{H}:x='max(0,min(iw-{W},iw*{cx:.4f}-{W // 2}))':y=(ih-{H})/2,setsar=1[base]")
        box = (0, 0, W, H)
    else:
        fg_w = W if template != "frame" else 1000
        fg_h = int(round(fg_w * sh / sw / 2) * 2)
        fg_h = min(fg_h, 1300)
        y_off = (H - fg_h) // 2 + (-110 if template == "commentary" else 0)
        x_off = (W - fg_w) // 2
        if template == "frame":
            bg = f"color=c={params.get('bg_color', '#101018').replace('#', '0x')}:s={W}x{H}:r=30[bgc]"
            fc.append(bg)
            fc.append(f"[vcat]{grade}scale={fg_w}:{fg_h},setsar=1,pad={fg_w + 12}:{fg_h + 12}:6:6:"
                      f"color={params.get('accent', '#FACC15').replace('#', '0x')}[fg]")
            fc.append(f"[bgc][fg]overlay={x_off - 6}:{y_off - 6}:shortest=1[base]")
        else:
            fc.append(f"[vcat]{grade}split=2[fgi][bgi]")
            fc.append(f"[bgi]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,gblur=sigma=12,"
                      f"eq=brightness=-0.12:saturation=1.1,scale={W}:{H},setsar=1[bg]")
            fc.append(f"[fgi]scale={fg_w}:{fg_h},setsar=1[fg]")
            fc.append(f"[bg][fg]overlay={x_off}:{y_off}[base]")
        box = (x_off, y_off, fg_w, fg_h)

    cur = "[base]"
    # 3. эффекты
    if effects.get("zoom_punch"):
        fc.append(f"{cur}zoompan=z='if(lt(it,0.7),1.14-0.2*it,1)':d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
                  f"s={W}x{H}:fps=30[zp]")
        cur = "[zp]"
    if effects.get("flash"):
        fc.append(f"{cur}fade=t=in:st=0:d=0.25:color=white[fl]")
        cur = "[fl]"
    if speed != 1.0:
        fc.append(f"{cur}setpts=PTS/{speed:.4f}[sp]")
        cur = "[sp]"
    # 4. надписи
    ass_text = build_ass({**params, "template": template}, duration, box)
    (work / "overlay.ass").write_text(ass_text, encoding="utf-8")
    fc.append(f"{cur}ass=overlay.ass:fontsdir=fonts,format=yuv420p[vout]")

    # 5. звук
    inputs = ["-i", str(src.resolve())]
    mpath = music_path(params.get("music", "none"))
    dialog_vol = float(params.get("dialog_volume", 1.0))
    music_vol = float(params.get("music_volume", 0.22))
    if has_audio:
        a_chain = f"[acat]volume={dialog_vol:.2f}"
        if speed != 1.0:
            a_chain += f",atempo={speed:.4f}"
        fc.append(a_chain + "[dia]")
    else:
        fc.append(f"anullsrc=r=44100:cl=stereo,atrim=0:{duration:.3f}[dia]")
    if mpath:
        inputs += ["-stream_loop", "-1", "-i", str(mpath.resolve())]
        fade_st = max(0.0, duration - 1.2)
        fc.append(f"[1:a]atrim=0:{duration:.3f},asetpts=PTS-STARTPTS,volume={music_vol:.2f},"
                  f"afade=t=in:d=0.4,afade=t=out:st={fade_st:.2f}:d=1.2[mus]")
        fc.append("[dia][mus]amix=inputs=2:duration=first:normalize=0[amix]")
        acur = "[amix]"
    else:
        acur = "[dia]"
    a_final = "afade=t=in:d=0.08"
    if params.get("loop_friendly"):
        a_final += f",afade=t=out:st={max(0.0, duration - 0.15):.2f}:d=0.15"
    if effects.get("loudnorm"):
        a_final += ",loudnorm=I=-14:TP=-1.5:LRA=11"
    fc.append(f"{acur}{a_final},aresample=44100[aout]")

    args = inputs + [
        "-filter_complex", ";".join(fc),
        "-map", "[vout]", "-map", "[aout]",
        "-t", f"{duration:.3f}",
        "-r", "30",
        "-c:v", "libx264", "-preset", params.get("preset", "veryfast"), "-crf", str(params.get("crf", 20)),
        "-profile:v", "high", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
        "-movflags", "+faststart",
        str(out.resolve()),
    ]
    try:
        run_ffmpeg(args, progress=progress, duration=duration, cwd=work)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return {"path": str(out), "duration": round(duration, 2), "segments": segments, "template": template}
