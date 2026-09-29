"""Сборка сцены по принципу «сначала максимум, потом сокращаем только пустое».

ИИ размечает весь исходник на смысловые биты (что происходит, есть ли речь, важность, динамика, роль в сюжете).
Дальше решает код — предсказуемо и одинаково для всех клипов:
  1. берётся весь файл без заставок/титров по краям;
  2. пока ролик длиннее лимита — убирается самый «дешёвый» кусок: сначала края, дальше от кульминации;
     из середины — только бит без речи, с низкой важностью и динамикой (пустой проход, пауза);
  3. кульминация и завязка перед ней не удаляются никогда, реплики не разрезаются;
  4. соседние оставшиеся куски склеиваются; разрывы короче 1.2 c не делаются (такая склейка режет глаз).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

ROLES = ("intro_outro", "setup", "build", "climax", "payoff", "reaction", "filler")
MIN_GAP = 1.2          # вырезать кусок короче — хуже, чем оставить
MIN_INTERIOR_CUT = 2.0  # из середины убираем только заметные пустые куски


def normalize_beats(beats: List[Dict[str, Any]], duration: float) -> List[Dict[str, Any]]:
    """Сортирует биты, закрывает дыры и перекрытия, чтобы они покрывали файл без разрывов."""
    out: List[Dict[str, Any]] = []
    for b in sorted((b for b in beats if float(b.get("end", 0)) > float(b.get("start", 0))), key=lambda b: float(b["start"])):
        st = max(0.0, float(b["start"]))
        en = min(duration, float(b["end"])) if duration else float(b["end"])
        if out and st < out[-1]["end"]:
            st = out[-1]["end"]
        if out and st - out[-1]["end"] > 0.05:
            out.append({"start": out[-1]["end"], "end": st, "role": "filler", "importance": 3, "intensity": 3,
                        "speech": False, "what": "(без описания)"})
        if en - st < 0.2:
            continue
        role = b.get("role") if b.get("role") in ROLES else "build"
        out.append({**b, "start": round(st, 2), "end": round(en, 2), "role": role,
                    "importance": _num(b.get("importance"), 5), "intensity": _num(b.get("intensity"), 5),
                    "speech": bool(b.get("speech"))})
    return out


def _num(v: Any, default: float) -> float:
    try:
        return max(0.0, min(10.0, float(v)))
    except (TypeError, ValueError):
        return default


def beat_value(b: Dict[str, Any]) -> float:
    """Ценность куска для ролика: смысл важнее динамики, речь почти не трогаем."""
    v = b["importance"] * 1.0 + b["intensity"] * 0.6
    if b["speech"]:
        v += 4.0
    if b["role"] in ("climax", "payoff"):
        v += 6.0
    if b["role"] == "setup":
        v += 2.0
    if b["role"] in ("filler", "intro_outro"):
        v -= 3.0
    return v


def plan_from_beats(beats: List[Dict[str, Any]], duration: float, peak: Optional[float],
                    min_len: float = 35.0, max_len: float = 58.0) -> Dict[str, Any]:
    """Части ролика [(start, end), ...] и пояснение, что было убрано."""
    bs = normalize_beats(beats, duration)
    if not bs:
        return {"parts": [], "removed": []}
    # заставки/титры по краям — убираем сразу
    while len(bs) > 1 and bs[0]["role"] == "intro_outro":
        bs.pop(0)
    while len(bs) > 1 and bs[-1]["role"] == "intro_outro":
        bs.pop()
    if peak is None:
        climax = [b for b in bs if b["role"] == "climax"] or [max(bs, key=lambda b: b["intensity"] + b["importance"])]
        peak = (climax[0]["start"] + climax[0]["end"]) / 2
    peak_i = min(range(len(bs)), key=lambda i: 0 if bs[i]["start"] <= peak <= bs[i]["end"] else
                 min(abs(bs[i]["start"] - peak), abs(bs[i]["end"] - peak)))

    def protected(i: int) -> bool:
        b = bs[i]
        return i == peak_i or b["role"] == "climax" or (i == peak_i - 1 and b["role"] in ("setup", "build"))

    keep = [True] * len(bs)
    removed: List[Dict[str, Any]] = []

    def total() -> float:
        return sum(b["end"] - b["start"] for b, k in zip(bs, keep) if k)

    while total() > max_len:
        idx = [i for i, k in enumerate(keep) if k]
        left, right = idx[0], idx[-1]
        cands: List[Tuple[float, int]] = []
        for i in (left, right):
            if not protected(i):
                dist = abs((bs[i]["start"] + bs[i]["end"]) / 2 - peak)
                cands.append((beat_value(bs[i]) - dist * 0.02, i))
        for i in idx[1:-1]:
            b = bs[i]
            if (not protected(i) and not b["speech"] and b["importance"] <= 3 and b["intensity"] <= 4
                    and b["end"] - b["start"] >= MIN_INTERIOR_CUT):
                cands.append((beat_value(b) + 1.0, i))   # середину трогаем осторожнее, чем края
        if not cands:
            break
        _, i = min(cands)
        over = total() - max_len
        b = bs[i]
        # крайний бит длиннее, чем нужно убрать, и без речи — подрезаем, а не выкидываем целиком
        if i in (left, right) and not b["speech"] and b["end"] - b["start"] - over >= 1.5:
            if i == left:
                b["start"] = round(b["start"] + over, 2)
            else:
                b["end"] = round(b["end"] - over, 2)
            removed.append({"what": b.get("what", ""), "trimmed": round(over, 1)})
            break
        keep[i] = False
        removed.append({"start": b["start"], "end": b["end"], "what": b.get("what", ""), "role": b["role"]})

    parts: List[List[float]] = []
    for b, k in zip(bs, keep):
        if not k:
            continue
        if parts and b["start"] - parts[-1][1] < MIN_GAP:
            parts[-1][1] = b["end"]
        else:
            parts.append([b["start"], b["end"]])
    # короткие разрывы между частями не делаем — возвращаем кусок обратно
    merged: List[List[float]] = []
    for p in parts:
        if merged and p[0] - merged[-1][1] < MIN_GAP:
            merged[-1][1] = p[1]
        else:
            merged.append(list(p))
    return {"parts": [[round(a, 2), round(b, 2)] for a, b in merged], "removed": removed, "peak": peak}


def protect_speech(parts: List[List[float]], subtitles: List[Dict[str, Any]], duration: float = 0.0,
                   pad: float = 0.25) -> List[List[float]]:
    """Ни одна реплика не разрезается: границы частей расширяются до конца/начала фразы."""
    out = [list(p) for p in parts]
    for s in subtitles or []:
        st, en = float(s["start"]), float(s["end"])
        for p in out:
            if p[0] < st < p[1] < en:          # фраза начинается внутри части и уходит за её конец
                p[1] = en + pad
            if st < p[0] < en and p[0] < p[1]:  # фраза начинается до части и заканчивается внутри
                p[0] = max(0.0, st - pad)
    if duration:
        out = [[max(0.0, a), min(duration, b)] for a, b in out]
    merged: List[List[float]] = []
    for p in sorted(out):
        if merged and p[0] - merged[-1][1] < MIN_GAP:
            merged[-1][1] = max(merged[-1][1], p[1])
        else:
            merged.append(p)
    return [[round(a, 2), round(b, 2)] for a, b in merged]


def expand_window(start: float, end: float, duration: float, min_len: float, max_len: float,
                  before_share: float = 0.6) -> Tuple[float, float]:
    """Без ИИ: расширяет окно вокруг кульминации до максимальной длины (завязка важнее хвоста)."""
    cur = end - start
    target = max(min_len, max_len)
    extra = max(0.0, min(target, duration) - cur)
    s = start - extra * before_share
    e = end + extra * (1 - before_share)
    if s < 0:
        e = min(duration, e - s)
        s = 0.0
    if e > duration:
        s = max(0.0, s - (e - duration))
        e = duration
    return round(s, 2), round(e, 2)
