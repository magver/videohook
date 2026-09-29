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


def extract_audio(path: str, out_path: str) -> str:
    """Звуковая дорожка 16 кГц моно WAV — чтобы агент Antigravity услышал реплики."""
    run_ffmpeg(["-i", str(path), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(out_path)])
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
        ("Subs", 54, "#FFFFFF", "#000000", "#000000", 1, 1, 5, 2, 2, 0),
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

    # --- нижняя зона: субтитры / реплики по таймингу или статичная подпись
    key_lines = params.get("key_lines") or []
    subtitles = params.get("subtitles") or []
    cap_y = bottom_zone + 170 if not fullscreen else H - 420
    if subtitles:
        for sub in subtitles:
            for st, en, txt in subtitle_chunks(sub):
                if txt and st < duration:
                    ev("Subs", txt, W // 2, cap_y, st, min(duration, en), extra="\\fad(60,60)")
        if template == "commentary" and caption:
            ev("Caption", caption, W // 2, bottom_zone + 330, 0.4, extra="\\fad(250,0)")
    elif template == "commentary":
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
DEFAULT_EFFECTS = {
    "color_pop": True,       # сочные цвета
    "zoom_punch": True,      # панч-зум на первом кадре
    "accent_zoom": True,     # панч-зумы на ударных моментах
    "flash": True,           # короткая вспышка на кульминации
    "slowmo": True,          # замедление кульминации (если задан момент)
    "impact_sfx": True,      # низкий «удар» на акцентах
    "whoosh_sfx": True,      # «вжух» на переходах между частями
    "punchy_audio": True,    # компрессия и бас оригинальной дорожки
    "loudnorm": True,        # громкость −14 LUFS
}

# переход между частями ролика по настроению сцены (фильтр xfade)
MOOD_TRANSITIONS = {
    "epic": "fadewhite", "dark": "fadeblack", "twist": "zoomin", "emotional": "dissolve",
    "romantic": "dissolve", "funny": "slideleft",
}
TRANSITIONS = {
    "auto": "Авто по настроению", "cut": "Жёсткая склейка", "fadewhite": "Вспышка", "fadeblack": "Через чёрный",
    "dissolve": "Растворение", "zoomin": "Зум", "slideleft": "Сдвиг", "smoothup": "Плавный вверх",
    "circleopen": "Круг", "pixelize": "Пикселизация",
}
XFADE_DUR = 0.35


def subtitle_chunks(sub: Dict[str, Any], max_chars: int = 42) -> List[Tuple[float, float, str]]:
    """Длинная реплика → куски по 1–2 строки, время делится пропорционально длине текста."""
    txt = clean_text(sub.get("text", ""))
    st, en = float(sub.get("start", 0)), float(sub.get("end", 0))
    if not txt or en <= st:
        return []
    words, chunks, cur = txt.split(), [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > max_chars * 2:
            chunks.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        chunks.append(cur)
    total = sum(len(c) for c in chunks) or 1
    out, t = [], st
    for c in chunks:
        d = (en - st) * len(c) / total
        out.append((round(t, 2), round(t + d, 2), c))
        t += d
    return out


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


class Timeline:
    """Части исходника → выходная шкала времени (с замедлением и наложением переходов)."""

    def __init__(self, segments: List[Tuple[float, float]], slowmo: Optional[Dict[str, Any]] = None,
                 xfade: float = 0.0):
        self.xfade = xfade if len(segments) > 1 else 0.0
        self.pieces: List[List[Tuple[float, float, float]]] = []
        for s, e in segments:
            parts = [(s, e, 1.0)]
            if slowmo:
                t0 = float(slowmo.get("t", -1))
                d = float(slowmo.get("dur", 1.2))
                f = max(0.5, min(0.9, float(slowmo.get("factor", 0.5))))
                a, b = max(s, t0 - d / 2), min(e, t0 + d / 2)
                if b - a >= 0.4:
                    parts = [p for p in ((s, a, 1.0), (a, b, f), (b, e, 1.0)) if p[1] - p[0] > 0.05]
            self.pieces.append(parts)
        self.seg_durs = [sum((e - s) / sp for s, e, sp in parts) for parts in self.pieces]
        self.seg_starts, acc = [], 0.0
        for i, d in enumerate(self.seg_durs):
            self.seg_starts.append(acc)
            acc += d - self.xfade
        self.duration = sum(self.seg_durs) - self.xfade * (len(self.seg_durs) - 1)

    def map(self, t: float) -> Optional[float]:
        for i, parts in enumerate(self.pieces):
            out = self.seg_starts[i]
            for s, e, sp in parts:
                if s <= t <= e:
                    return round(out + (t - s) / sp, 3)
                out += (e - s) / sp
        return None

    def transitions(self) -> List[float]:
        """Середины переходов на выходной шкале."""
        return [self.seg_starts[i] + self.xfade / 2 for i in range(1, len(self.pieces))]


def map_items(tl: Timeline, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for it in items or []:
        st = tl.map(float(it["start"]))
        en = tl.map(float(it["end"]))
        if st is None and en is None:
            continue
        st = st if st is not None else 0.0
        en = en if en is not None else min(tl.duration, st + 3.0)
        if en > st:
            out.append({**it, "start": st, "end": en})
    return out


def _pulses(times: List[float], amp: float, width: float) -> str:
    return "+".join(f"{amp}*exp(-pow((it-{t:.2f})/{width},2))" for t in times) or "0"


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

    # подгонка конца под первый кадр — только по явному запросу: иначе обрезается финал сцены
    if params.get("loop_trim") and len(segments) == 1:
        s, e = segments[0]
        segments = [(s, find_loop_end(str(src), s, e, search=1.0))]

    transition = params.get("transition") or "auto"
    if transition == "auto":
        transition = MOOD_TRANSITIONS.get(params.get("mood", "epic"), "fadewhite")
    xd = XFADE_DUR if transition != "cut" and len(segments) > 1 else 0.0
    slowmo = params.get("slowmo") if effects.get("slowmo") else None
    tl = Timeline(segments, slowmo, xd)
    speed = float(params.get("speed") or 1.0)
    duration = tl.duration / speed

    accents = sorted({round(t / speed, 2) for t in (tl.map(float(a)) for a in params.get("accents") or [])
                      if t is not None and 0.3 < t < tl.duration - 0.3})[:10]
    trans_times = [t / speed for t in tl.transitions()]
    subtitles = [{**x, "start": x["start"] / speed, "end": x["end"] / speed}
                 for x in map_items(tl, params.get("subtitles") or [])]

    work = TMP_DIR / new_id("r_")
    (work / "fonts").mkdir(parents=True, exist_ok=True)
    for f in FONTS_DIR.glob("*.ttf"):
        shutil.copy2(f, work / "fonts" / f.name)

    RENDERS_DIR.mkdir(parents=True, exist_ok=True)
    out = Path(out_path) if out_path else RENDERS_DIR / f"{new_id('v_')}.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)

    fc: List[str] = []
    has_audio = info["has_audio"]
    # 1. нарезка: части сегмента (с замедлением) склеиваются встык, сегменты — через переход
    for i, parts in enumerate(tl.pieces):
        for j, (s, e, sp) in enumerate(parts):
            fc.append(f"[0:v]trim=start={s}:end={e},setpts=(PTS-STARTPTS)/{sp},fps=30,setsar=1,format=yuv420p[v{i}_{j}]")
            if has_audio:
                fc.append(f"[0:a]atrim=start={s}:end={e},asetpts=PTS-STARTPTS"
                          + (f",atempo={sp}" if sp != 1.0 else "") + f",aresample=44100[a{i}_{j}]")
        n = len(parts)
        if n == 1:
            fc.append(f"[v{i}_0]settb=1/30[vs{i}]")
            if has_audio:
                fc.append(f"[a{i}_0]anull[as{i}]")
        elif has_audio:
            ins = "".join(f"[v{i}_{j}][a{i}_{j}]" for j in range(n))
            fc.append(f"{ins}concat=n={n}:v=1:a=1[vc{i}][as{i}]")
            fc.append(f"[vc{i}]fps=30,settb=1/30[vs{i}]")
        else:
            ins = "".join(f"[v{i}_{j}]" for j in range(n))
            fc.append(f"{ins}concat=n={n}:v=1:a=0[vc{i}]")
            fc.append(f"[vc{i}]fps=30,settb=1/30[vs{i}]")
    vprev, aprev = "[vs0]", "[as0]"
    acc = tl.seg_durs[0]
    for i in range(1, len(tl.pieces)):
        if xd:
            fc.append(f"{vprev}[vs{i}]xfade=transition={transition}:duration={xd}:offset={acc - xd:.3f}[vx{i}]")
            if has_audio:
                fc.append(f"{aprev}[as{i}]acrossfade=d={xd}[ax{i}]")
            acc += tl.seg_durs[i] - xd
        else:
            if has_audio:
                fc.append(f"{vprev}{aprev}[vs{i}][as{i}]concat=n=2:v=1:a=1[vx{i}][ax{i}]")
            else:
                fc.append(f"{vprev}[vs{i}]concat=n=2:v=1:a=0[vx{i}]")
            acc += tl.seg_durs[i]
        vprev, aprev = f"[vx{i}]", f"[ax{i}]"
    fc.append(f"{vprev}null[vcat]")
    if has_audio:
        fc.append(f"{aprev}anull[acat]")

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
    if speed != 1.0:
        fc.append(f"{cur}setpts=PTS/{speed:.4f}[sp]")
        cur = "[sp]"
    # 3. эффекты: панч-зум на старте и на ударных моментах, вспышки на кульминации
    zoom_terms = []
    if effects.get("zoom_punch"):
        zoom_terms.append("if(lt(it,0.7),0.14-0.2*it,0)")
    if effects.get("accent_zoom") and accents:
        zoom_terms.append(_pulses(accents, 0.09, 0.16))
    if zoom_terms:
        fc.append(f"{cur}zoompan=z='1+{'+'.join(zoom_terms)}':d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
                  f"s={W}x{H}:fps=30[zp]")
        cur = "[zp]"
    if effects.get("flash"):
        flashes = accents[:3] if accents else []
        if flashes:
            en = "+".join(f"between(t,{t:.2f},{t + 0.07:.2f})" for t in flashes)
            fc.append(f"{cur}eq=brightness=0.28:contrast=1.1:enable='{en}'[fl]")
            cur = "[fl]"
    # 4. надписи
    ass_text = build_ass({**params, "template": template, "subtitles": subtitles}, duration, box)
    (work / "overlay.ass").write_text(ass_text, encoding="utf-8")
    fc.append(f"{cur}ass=overlay.ass:fontsdir=fonts,format=yuv420p[vout]")

    # 5. звук: оригинальная дорожка — основа; музыка и эффекты — поверх
    inputs = ["-i", str(src.resolve())]
    mpath = music_path(params.get("music", "none"))
    dialog_vol = float(params.get("dialog_volume", 1.0))
    music_vol = float(params.get("music_volume", 0.22))
    if has_audio:
        a_chain = f"[acat]volume={dialog_vol:.2f}"
        if effects.get("punchy_audio"):
            a_chain += ",acompressor=threshold=0.1:ratio=3:attack=8:release=160:makeup=1.6,bass=g=3:f=110"
        if speed != 1.0:
            a_chain += f",atempo={speed:.4f}"
        fc.append(a_chain + ",aformat=sample_rates=44100:channel_layouts=stereo[dia]")
    else:
        fc.append(f"anullsrc=r=44100:cl=stereo,atrim=0:{duration:.3f}[dia]")
    mix = ["[dia]"]
    if mpath:
        inputs += ["-stream_loop", "-1", "-i", str(mpath.resolve())]
        fade_st = max(0.0, duration - 1.2)
        fc.append(f"[1:a]atrim=0:{duration:.3f},asetpts=PTS-STARTPTS,volume={music_vol:.2f},"
                  f"afade=t=in:d=0.4,afade=t=out:st={fade_st:.2f}:d=1.2,"
                  f"aformat=sample_rates=44100:channel_layouts=stereo[mus]")
        mix.append("[mus]")
    sfx = []
    if effects.get("impact_sfx"):
        for t in accents[:6]:
            sfx.append((t, "sine=f=52:r=44100:d=0.7,volume=1.4,afade=t=out:st=0.04:d=0.66:curve=exp"))
    if effects.get("whoosh_sfx"):
        for t in trans_times:
            sfx.append((max(0.0, t - 0.3), "anoisesrc=d=0.5:c=pink:r=44100:a=0.3,highpass=f=400,lowpass=f=6000,"
                                           "afade=t=in:d=0.3:curve=exp,afade=t=out:st=0.3:d=0.2"))
    for k, (t, chain) in enumerate(sfx):
        ms = int(t * 1000)
        fc.append(f"{chain},aformat=sample_rates=44100:channel_layouts=stereo,adelay={ms}|{ms}[fx{k}]")
        mix.append(f"[fx{k}]")
    if len(mix) > 1:
        fc.append(f"{''.join(mix)}amix=inputs={len(mix)}:duration=first:normalize=0[amix]")
        acur = "[amix]"
    else:
        acur = "[dia]"
    a_final = "afade=t=in:d=0.08"
    if params.get("loop_friendly"):
        a_final += f",afade=t=out:st={max(0.0, duration - 0.15):.2f}:d=0.15"
    else:
        a_final += f",afade=t=out:st={max(0.0, duration - 0.6):.2f}:d=0.6"
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
    return {"path": str(out), "duration": round(duration, 2), "segments": segments, "template": template,
            "transition": transition if xd else "cut", "accents": accents, "subtitles": len(subtitles)}
