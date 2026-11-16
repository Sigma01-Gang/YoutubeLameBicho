"""Subtítulos SRT/VTT/ASS: parseo, escritura, edición, validación y karaoke. Sin servicios externos."""
from __future__ import annotations

import csv
import difflib
import io
import re
from dataclasses import dataclass, field, replace
from pathlib import Path

from ..core.models import SubtitleStyle
from ..core.timeutil import format_timestamp, parse_timestamp


@dataclass
class Word:
    text: str
    start: float
    end: float


@dataclass
class Cue:
    start: float
    end: float
    text: str
    words: list[Word] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return self.end - self.start


_ARROW = re.compile(r"(\S+)\s*-->\s*(\S+)")


def _parse_ts(s: str) -> float:
    s = s.strip().replace(",", ".")
    if s.count(":") == 1:
        s = "00:" + s
    return parse_timestamp(s)


def parse_srt(text: str) -> list[Cue]:
    return _parse_blocks(text)


def parse_vtt(text: str) -> list[Cue]:
    text = re.sub(r"^﻿?WEBVTT[^\n]*\n", "", text.lstrip("﻿"), count=1)
    # elimina bloques NOTE / STYLE
    blocks = re.split(r"\n\s*\n", text.replace("\r\n", "\n"))
    kept = [b for b in blocks if not b.strip().startswith(("NOTE", "STYLE", "REGION"))]
    return _parse_blocks("\n\n".join(kept))


def _parse_blocks(text: str) -> list[Cue]:
    cues: list[Cue] = []
    for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n").strip()):
        lines = [l for l in block.split("\n")]
        idx = next((i for i, l in enumerate(lines) if "-->" in l), None)
        if idx is None:
            continue
        m = _ARROW.search(lines[idx])
        if not m:
            continue
        try:
            start, end = _parse_ts(m.group(1)), _parse_ts(m.group(2))
        except ValueError:
            continue
        body = "\n".join(lines[idx + 1:]).strip()
        body = re.sub(r"</?[^>]+>", "", body)  # etiquetas <i>, <c.x>, etc.
        cues.append(Cue(start, end, body))
    cues.sort(key=lambda c: c.start)
    return cues


def load(path: str | Path) -> list[Cue]:
    p = Path(path)
    text = p.read_text(encoding="utf-8-sig", errors="replace")
    if p.suffix.lower() == ".vtt":
        return parse_vtt(text)
    return parse_srt(text)


def to_srt(cues: list[Cue]) -> str:
    out = []
    for i, c in enumerate(cues, 1):
        out.append(f"{i}\n{format_timestamp(c.start, ',')} --> {format_timestamp(c.end, ',')}\n{c.text}\n")
    return "\n".join(out)


def to_vtt(cues: list[Cue]) -> str:
    out = ["WEBVTT\n"]
    for c in cues:
        out.append(f"{format_timestamp(c.start, '.')} --> {format_timestamp(c.end, '.')}\n{c.text}\n")
    return "\n".join(out)


def save(cues: list[Cue], path: str | Path) -> Path:
    p = Path(path)
    content = to_vtt(cues) if p.suffix.lower() == ".vtt" else to_srt(cues)
    p.write_text(content, encoding="utf-8")
    return p


def convert(src: str | Path, dst: str | Path) -> Path:
    return save(load(src), dst)


# --- edición ---------------------------------------------------------------

def shift(cues: list[Cue], offset: float) -> list[Cue]:
    out = []
    for c in cues:
        s, e = c.start + offset, c.end + offset
        if e <= 0:
            continue
        out.append(replace(c, start=max(0.0, s), end=e,
                           words=[Word(w.text, max(0.0, w.start + offset), w.end + offset) for w in c.words]))
    return out


def split_cue(cues: list[Cue], index: int, at: float | None = None) -> list[Cue]:
    """Divide una línea en dos. Si no se da `at`, se parte por la mitad (en palabras y tiempo)."""
    c = cues[index]
    words = c.text.split()
    if len(words) < 2:
        return list(cues)
    mid_w = len(words) // 2
    t = at if at is not None else c.start + c.duration * (mid_w / len(words))
    t = min(max(t, c.start + 0.05), c.end - 0.05)
    a = Cue(c.start, t, " ".join(words[:mid_w]))
    b = Cue(t, c.end, " ".join(words[mid_w:]))
    return cues[:index] + [a, b] + cues[index + 1:]


def merge_cues(cues: list[Cue], index: int) -> list[Cue]:
    """Une la línea `index` con la siguiente."""
    if index >= len(cues) - 1:
        return list(cues)
    a, b = cues[index], cues[index + 1]
    merged = Cue(a.start, b.end, f"{a.text} {b.text}".strip(), a.words + b.words)
    return cues[:index] + [merged] + cues[index + 2:]


def wrap_text(text: str, max_chars: int) -> str:
    """Reparte el texto en líneas de como máximo max_chars (sin cortar palabras)."""
    lines, cur = [], ""
    for w in text.replace("\n", " ").split():
        if cur and len(cur) + 1 + len(w) > max_chars:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return "\n".join(lines)


@dataclass
class CueWarning:
    index: int
    message: str


def validate(cues: list[Cue], max_chars: int = 42, max_lines: int = 2) -> list[CueWarning]:
    warns: list[CueWarning] = []
    for i, c in enumerate(cues):
        lines = c.text.split("\n")
        if len(lines) > max_lines:
            warns.append(CueWarning(i, f"{len(lines)} líneas (máx. {max_lines})"))
        for ln in lines:
            if len(ln) > max_chars:
                warns.append(CueWarning(i, f"línea de {len(ln)} caracteres (máx. {max_chars})"))
                break
        if c.end <= c.start:
            warns.append(CueWarning(i, "el fin es anterior o igual al inicio"))
        if i and c.start < cues[i - 1].end - 0.001:
            warns.append(CueWarning(i, "se solapa con la línea anterior"))
    return warns


def check_glossary(cues: list[Cue], glossary: list[str]) -> list[CueWarning]:
    """Señala palabras que se parecen a un nombre del glosario pero con distinta ortografía."""
    warns: list[CueWarning] = []
    terms = {t.lower(): t for t in glossary if t.strip()}
    for i, c in enumerate(cues):
        for raw in re.findall(r"[\wÀ-ÿ'-]+", c.text):
            low = raw.lower()
            if low in terms and raw != terms[low]:
                warns.append(CueWarning(i, f"«{raw}» debería escribirse «{terms[low]}»"))
                continue
            if len(raw) >= 4 and low not in terms:
                close = difflib.get_close_matches(low, terms.keys(), n=1, cutoff=0.85)
                if close:
                    warns.append(CueWarning(i, f"«{raw}» ¿quisiste decir «{terms[close[0]]}»?"))
    return warns


# --- mapeo a segmentos ------------------------------------------------------

def map_time(t: float, kept: list[tuple[float, float]], speed: float = 1.0) -> float | None:
    """Convierte un tiempo de origen al tiempo de salida tras eliminar pausas y aplicar velocidad.

    Devuelve None si t cae en una parte eliminada.
    """
    acc = 0.0
    for a, b in kept:
        if t < a:
            return None
        if t <= b:
            return (acc + (t - a)) / speed
        acc += b - a
    return None


def slice_for_segment(cues: list[Cue], kept: list[tuple[float, float]], speed: float = 1.0) -> list[Cue]:
    """Recorta y reubica los subtítulos para un segmento (mantiene la sincronía)."""
    out: list[Cue] = []
    for c in cues:
        pieces = []
        for a, b in kept:
            s, e = max(c.start, a), min(c.end, b)
            if e - s > 0.04:
                pieces.append((s, e))
        if not pieces:
            continue
        s_out = map_time(pieces[0][0], kept, speed)
        e_out = map_time(pieces[-1][1], kept, speed)
        if s_out is None or e_out is None or e_out <= s_out:
            continue
        words = []
        for w in c.words:
            ws, we = map_time(w.start, kept, speed), map_time(w.end, kept, speed)
            if ws is not None and we is not None:
                words.append(Word(w.text, ws, we))
        out.append(Cue(s_out, e_out, c.text, words))
    return out


# --- tiempos por palabra (karaoke) -----------------------------------------

def load_word_timings(path: str | Path) -> list[Word]:
    """CSV con columnas palabra,inicio,fin (también word,start,end). Tiempos en s o HH:MM:SS.mmm."""
    text = Path(path).read_text(encoding="utf-8-sig")
    return parse_word_timings(text)


def parse_word_timings(text: str) -> list[Word]:
    reader = csv.DictReader(io.StringIO(text))
    words = []
    for row in reader:
        r = {k.strip().lower(): (v or "").strip() for k, v in row.items() if k}
        w = r.get("palabra") or r.get("word") or r.get("texto") or r.get("text")
        s = r.get("inicio") or r.get("start")
        e = r.get("fin") or r.get("end")
        if w and s and e:
            words.append(Word(w, parse_timestamp(s), parse_timestamp(e)))
    return words


def group_words(words: list[Word], max_chars: int = 24, max_gap: float = 0.6) -> list[Cue]:
    """Agrupa palabras en líneas cortas para subtítulos palabra a palabra."""
    cues: list[Cue] = []
    cur: list[Word] = []
    for w in words:
        text_len = len(" ".join(x.text for x in cur + [w]))
        if cur and (text_len > max_chars or w.start - cur[-1].end > max_gap):
            cues.append(Cue(cur[0].start, cur[-1].end, " ".join(x.text for x in cur), cur))
            cur = []
        cur.append(w)
    if cur:
        cues.append(Cue(cur[0].start, cur[-1].end, " ".join(x.text for x in cur), cur))
    return cues


# --- ASS ------------------------------------------------------------------

def ass_color(hex_color: str, alpha: int = 0) -> str:
    """'#RRGGBB' -> '&HAABBGGRR' (formato ASS)."""
    h = hex_color.lstrip("#")
    if len(h) != 6:
        h = "FFFFFF"
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def _ass_ts(t: float) -> str:
    cs = int(round(max(0.0, t) * 100))
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, cs = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _ass_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "(").replace("}", ")").replace("\n", "\\N")


def to_ass(cues: list[Cue], style: SubtitleStyle, width: int = 1080, height: int = 1920,
           karaoke: bool = False) -> str:
    align = {"bottom": 2, "middle": 5, "top": 8}.get(style.position, 2)
    primary = ass_color(style.highlight_color if karaoke else style.color)
    secondary = ass_color(style.color)
    header = (
        "[Script Info]\nScriptType: v4.00+\nWrapStyle: 0\nScaledBorderAndShadow: yes\n"
        f"PlayResX: {width}\nPlayResY: {height}\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, "
        "Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{style.font},{style.size},{primary},{secondary},{ass_color(style.outline_color)},"
        f"&H80000000,{-1 if style.bold else 0},0,0,0,100,100,0,0,1,{style.outline},1,{align},60,60,"
        f"{style.margin_v},1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    lines = []
    for c in cues:
        prefix = ""
        if style.animation == "fade":
            prefix = "{\\fad(180,120)}"
        elif style.animation == "slide":
            y = {2: height - style.margin_v, 5: height // 2, 8: style.margin_v}[align]
            prefix = f"{{\\move({width // 2},{y + 60},{width // 2},{y},0,180)}}"
        if karaoke and c.words:
            parts = []
            cursor = c.start
            for w in c.words:
                gap = max(0, int(round((w.start - cursor) * 100)))
                if gap:
                    parts.append(f"{{\\k{gap}}}")
                dur = max(1, int(round((w.end - w.start) * 100)))
                parts.append(f"{{\\k{dur}}}{_ass_escape(w.text)} ")
                cursor = w.end
            text = "".join(parts).rstrip()
        else:
            text = _ass_escape(wrap_text(c.text, style.max_chars) if "\n" not in c.text else c.text)
        lines.append(f"Dialogue: 0,{_ass_ts(c.start)},{_ass_ts(c.end)},Default,,0,0,0,,{prefix}{text}")
    return header + "\n".join(lines) + "\n"
