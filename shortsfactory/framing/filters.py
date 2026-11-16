"""Filtros de encuadre, color, Ken Burns y estabilización (funciones puras)."""
from __future__ import annotations

from ..core.models import RenderOptions
from ..ffmpeg.graph import Graph, escape_path, ff_color, num


def pre_filters(opts: RenderOptions) -> list[str]:
    """Filtros aplicados sobre el vídeo de origen antes del reencuadre."""
    f: list[str] = []
    if opts.autocrop:
        w, h, x, y = opts.autocrop
        f.append(f"crop={w}:{h}:{x}:{y}")
    if opts.stabilize and opts.stabilize_file:
        f.append(f"vidstabtransform=input={escape_path(opts.stabilize_file)}:smoothing=10:zoom=0:optzoom=1")
        f.append("unsharp=5:5:0.8:3:3:0.4")
    elif opts.stabilize:
        f.append("deshake")
    f.extend(color_filters(opts))
    return f


def color_filters(opts: RenderOptions) -> list[str]:
    f: list[str] = []
    if opts.brightness != 0.0 or opts.contrast != 1.0 or opts.saturation != 1.0:
        f.append(f"eq=brightness={num(opts.brightness)}:contrast={num(opts.contrast)}:saturation={num(opts.saturation)}")
    if opts.temperature and int(opts.temperature) != 6500:
        f.append(f"colortemperature=temperature={int(opts.temperature)}")
    return f


def cover_crop(w: int, h: int, fx: float = 0.5, fy: float = 0.5) -> list[str]:
    """Escala para cubrir WxH y recorta con la posición relativa (fx, fy)."""
    fx = min(max(fx, 0.0), 1.0)
    fy = min(max(fy, 0.0), 1.0)
    return [
        f"scale={w}:{h}:force_original_aspect_ratio=increase",
        f"crop={w}:{h}:(iw-ow)*{num(fx)}:(ih-oh)*{num(fy)}",
    ]


def fit_inside(w: int, h: int) -> str:
    return f"scale={w}:{h}:force_original_aspect_ratio=decrease"


def build_framing(g: Graph, vin: str, opts: RenderOptions, bg_image_label: str | None = None) -> str:
    """Añade el reencuadre al grafo y devuelve la etiqueta de salida (WxH)."""
    W, H = opts.width, opts.height
    mode = opts.framing
    if mode in ("crop", "manual"):
        fx, fy = (0.5, 0.5) if mode == "crop" else (opts.manual_x, opts.manual_y)
        return g.chain([vin], cover_crop(W, H, fx, fy) + ["setsar=1"], prefix="fr")
    if mode == "face":
        filters = [f"scale={W}:{H}:force_original_aspect_ratio=increase"]
        if opts.face_track_file:
            filters.append(f"sendcmd=f={escape_path(opts.face_track_file)}")
        filters.append(f"crop@face={W}:{H}:(iw-ow)*0.5:(ih-oh)*0.5")
        filters.append("setsar=1")
        return g.chain([vin], filters, prefix="fr")
    if mode == "blur":
        a, b = g.new("bga"), g.new("fga")
        g.chain([vin], "split=2", [a, b])
        bg = g.chain([a], cover_crop(W, H) + [f"boxblur={int(opts.blur_strength)}:2", "eq=brightness=-0.08"], prefix="bg")
        fg = g.chain([b], [fit_inside(W, H)], prefix="fg")
        return g.chain([bg, fg], ["overlay=(W-w)/2:(H-h)/2", "setsar=1"], prefix="fr")
    if mode == "letterbox":
        if bg_image_label:
            bg = g.chain([bg_image_label], cover_crop(W, H) + [f"fps={opts.fps}"], prefix="bg")
        else:
            bg = g.source(f"color=c={ff_color(opts.letterbox_color)}:s={W}x{H}:r={opts.fps}", prefix="bg")
        fg = g.chain([vin], [fit_inside(W, H)], prefix="fg")
        return g.chain([bg, fg], ["overlay=(W-w)/2:(H-h)/2:shortest=1", "setsar=1"], prefix="fr")
    raise ValueError(f"modo de encuadre desconocido: {mode}")


def kenburns_filter(opts: RenderOptions, duration: float) -> str:
    """Zoom gradual (zoompan) desde kb_start_zoom a kb_end_zoom durante kb_duration segundos."""
    d = opts.kb_duration if opts.kb_duration > 0 else max(duration, 0.1)
    frames = max(1, int(round(d * opts.fps)))
    zs, ze = opts.kb_start_zoom, opts.kb_end_zoom
    z = f"{num(zs)}+({num(ze - zs)})*min(in/{frames}\\,1)"
    return (
        f"zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
        f":d=1:s={opts.width}x{opts.height}:fps={opts.fps}"
    )


def vidstab_detect_command(ffmpeg: str, src: str, start: float, duration: float, trf: str) -> list[str]:
    """Primera pasada de estabilización: genera transforms.trf."""
    return [
        ffmpeg, "-hide_banner", "-y", "-ss", num(start), "-t", num(duration), "-i", src,
        "-vf", f"vidstabdetect=shakiness=5:accuracy=15:result={escape_path(trf)}",
        "-f", "null", "-",
    ]


def cropdetect_command(ffmpeg: str, src: str, start: float = 0.0, duration: float = 20.0) -> list[str]:
    return [
        ffmpeg, "-hide_banner", "-ss", num(start), "-t", num(duration), "-i", src,
        "-vf", "cropdetect=limit=24:round=2:reset=0", "-f", "null", "-",
    ]


def parse_cropdetect(stderr: str) -> tuple[int, int, int, int] | None:
    """Devuelve el último crop=w:h:x:y detectado."""
    import re

    matches = re.findall(r"crop=(\d+):(\d+):(\d+):(\d+)", stderr)
    if not matches:
        return None
    w, h, x, y = (int(v) for v in matches[-1])
    return (w, h, x, y)
