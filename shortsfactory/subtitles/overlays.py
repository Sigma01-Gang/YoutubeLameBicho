"""Superposiciones de texto: numeración de partes, barra de progreso y firma (funciones puras)."""
from __future__ import annotations

from ..core.models import RenderOptions
from ..ffmpeg.graph import Graph, escape_path, escape_text, ff_color, num


def _font_opt(opts: RenderOptions) -> str:
    return f":fontfile={escape_path(opts.font_file)}" if opts.font_file else ""


def _anim(opts: RenderOptions, base_x: str, start: float = 0.0) -> tuple[str, str]:
    """Devuelve (x, alpha) según la animación de entrada configurada."""
    if opts.text_animation == "fade":
        return base_x, f"min(1\\,max(0\\,(t-{num(start)})/0.5))"
    if opts.text_animation == "slide":
        return f"{base_x}-max(0\\,({num(start)}+0.4-t))*900", "1"
    return base_x, "1"


def part_label_filter(opts: RenderOptions) -> str | None:
    if not opts.part_label:
        return None
    x, alpha = _anim(opts, "(w-tw)/2")
    return (
        f"drawtext=text={escape_text(opts.part_label)}:expansion=none{_font_opt(opts)}"
        f":fontsize={int(opts.height * 0.036)}:fontcolor=white:borderw=4:bordercolor=black"
        f":box=1:boxcolor=black@0.45:boxborderw=18:x='{x}':y={int(opts.height * 0.075)}:alpha='{alpha}'"
    )


def _corner(pos: str, margin: int = 40) -> tuple[str, str]:
    return {
        "top-left": (f"{margin}", f"{margin}"),
        "top-right": (f"W-w-{margin}", f"{margin}"),
        "bottom-left": (f"{margin}", f"H-h-{margin}"),
        "bottom-right": (f"W-w-{margin}", f"H-h-{margin}"),
        "center": ("(W-w)/2", "(H-h)/2"),
    }.get(pos, (f"W-w-{margin}", f"{margin}"))


def signature_enable(opts: RenderOptions) -> str:
    if opts.signature_duration > 0:
        return f"between(t\\,{num(opts.signature_start)}\\,{num(opts.signature_start + opts.signature_duration)})"
    if opts.signature_start > 0:
        return f"gte(t\\,{num(opts.signature_start)})"
    return "1"


def signature_text_filter(opts: RenderOptions) -> str | None:
    if not opts.signature_text:
        return None
    x, y = _corner(opts.signature_position)
    # drawtext usa w/h del vídeo y tw/th del texto
    x = x.replace("W-w", "w-tw").replace("(W-w)", "(w-tw)")
    y = y.replace("H-h", "h-th").replace("(H-h)", "(h-th)")
    return (
        f"drawtext=text={escape_text(opts.signature_text)}:expansion=none{_font_opt(opts)}"
        f":fontsize={int(opts.height * 0.022)}:fontcolor=white@0.85:borderw=2:bordercolor=black@0.6"
        f":x={x}:y={y}:enable='{signature_enable(opts)}'"
    )


def add_overlays(g: Graph, vin: str, opts: RenderOptions, duration: float,
                 frame_label: str | None, logo_label: str | None, subs_filter: str | None) -> str:
    """Añade marco, subtítulos, numeración, barra de progreso y firma."""
    cur = vin
    if frame_label:
        fr = g.chain([frame_label], [f"scale={opts.width}:{opts.height}", "format=rgba"], prefix="pf")
        cur = g.chain([cur, fr], ["overlay=0:0:format=auto"], prefix="ov")
    simple = [f for f in (subs_filter, part_label_filter(opts)) if f]
    if simple:
        cur = g.chain([cur], simple, prefix="ov")
    if opts.progress:
        p0, p1 = opts.progress
        bar_h = max(8, int(opts.height * 0.008))
        bar = g.source(f"color=c={ff_color(opts.progress_color)}:s={opts.width}x{bar_h}:r={opts.fps}", prefix="bar")
        frac = f"{num(p0)}+({num(p1 - p0)})*min(t/{num(max(duration, 0.01))}\\,1)"
        cur = g.chain([cur, bar], [f"overlay=x='-w+w*({frac})':y=H-h:shortest=1"], prefix="ov")
    sig = signature_text_filter(opts)
    if sig:
        cur = g.chain([cur], [sig], prefix="ov")
    if logo_label:
        lw = int(opts.width * 0.18)
        logo = g.chain([logo_label], [f"scale={lw}:-1", "format=rgba"], prefix="lg")
        x, y = _corner(opts.signature_position)
        cur = g.chain([cur, logo], [f"overlay={x}:{y}:enable='{signature_enable(opts)}'"], prefix="ov")
    return cur
