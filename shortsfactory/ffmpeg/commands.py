"""Generación del comando de render completo (función pura y testeable)."""
from __future__ import annotations

from dataclasses import dataclass, field

from ..audio.filters import build_audio
from ..core.models import RenderOptions, Segment
from ..framing.filters import build_framing, kenburns_filter, pre_filters
from ..subtitles.overlays import add_overlays
from .graph import Graph, escape_path, num

ENCODERS = {
    "libx264": lambda o: ["-c:v", "libx264", "-preset", o.preset, "-crf", str(o.crf), "-profile:v", "high"],
    "h264_nvenc": lambda o: ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", str(o.crf), "-b:v", "0"],
    "h264_qsv": lambda o: ["-c:v", "h264_qsv", "-preset", "medium", "-global_quality", str(o.crf)],
    "h264_amf": lambda o: ["-c:v", "h264_amf", "-quality", "balanced", "-rc", "cqp",
                           "-qp_i", str(o.crf), "-qp_p", str(o.crf)],
}

HW_MAP = {"nvenc": "h264_nvenc", "qsv": "h264_qsv", "amf": "h264_amf"}


def choose_encoder(preference: str, available: set[str]) -> str:
    """Elige el codificador: 'auto' prueba NVENC, QuickSync y AMF; si no, CPU (libx264)."""
    if preference in HW_MAP:
        enc = HW_MAP[preference]
        return enc if enc in available else "libx264"
    if preference == "auto":
        for enc in ("h264_nvenc", "h264_qsv", "h264_amf"):
            if enc in available:
                return enc
    return "libx264"


def encoder_args(opts: RenderOptions) -> list[str]:
    return ENCODERS.get(opts.encoder, ENCODERS["libx264"])(opts)


def select_expression(seg: Segment) -> str | None:
    """Expresión select/aselect para conservar sólo los intervalos `keep` (tiempo relativo al segmento)."""
    if not seg.keep:
        return None
    terms = [f"between(t\\,{num(a - seg.start)}\\,{num(b - seg.start)})" for a, b in seg.keep]
    return "+".join(terms)


@dataclass
class RenderPlan:
    cmd: list[str]
    duration: float
    inputs: list[str] = field(default_factory=list)
    filtergraph: str = ""


def build_render_command(
    ffmpeg: str,
    src: str,
    seg: Segment,
    opts: RenderOptions,
    output: str,
    *,
    has_audio: bool = True,
    subtitles_ass: str | None = None,
    video_only: bool = False,
) -> RenderPlan:
    """Construye el comando ffmpeg para renderizar un segmento en vertical.

    El recorte se hace con -ss/-t sobre la entrada (re-codificando, por lo que es preciso al fotograma)
    y vídeo y audio salen del mismo intervalo, de modo que se mantienen sincronizados.
    """
    if seg.end <= seg.start:
        raise ValueError("el segmento tiene duración nula")
    speed = seg.speed or 1.0
    if not 0.5 <= speed <= 2.0:
        raise ValueError("la velocidad debe estar entre 0,5x y 2x")
    out_dur = seg.output_duration()
    total = out_dur + max(0.0, opts.freeze_last)

    inputs: list[list[str]] = [["-ss", num(seg.start), "-t", num(seg.duration), "-i", src]]
    labels: dict[str, str] = {}

    def add_input(key: str, args: list[str]) -> None:
        inputs.append(args)
        labels[key] = f"{len(inputs) - 1}"

    if opts.framing == "letterbox" and opts.letterbox_image:
        add_input("bgimg", ["-loop", "1", "-framerate", str(opts.fps), "-i", opts.letterbox_image])
    if opts.frame_png:
        add_input("frame", ["-i", opts.frame_png])
    if opts.signature_logo:
        add_input("logo", ["-i", opts.signature_logo])
    if opts.voice:
        add_input("voice", ["-i", opts.voice])
    if opts.music:
        add_input("music", ["-stream_loop", "-1", "-i", opts.music])
    sfx_keys = []
    for i, cue in enumerate(opts.sfx):
        add_input(f"sfx{i}", ["-i", cue.path])
        sfx_keys.append((f"sfx{i}", cue))

    g = Graph()
    sel = select_expression(seg)

    # --- vídeo ---
    vchain: list[str] = [f"fps={opts.fps}"]
    if sel:
        vchain += [f"select='{sel}'", "setpts=N/FRAME_RATE/TB"]
    if abs(speed - 1.0) > 1e-6:
        vchain.append(f"setpts=PTS/{num(speed)}")
    vchain += pre_filters(opts)
    v = g.chain(["0:v"], vchain, prefix="v")
    v = build_framing(g, v, opts, bg_image_label=(labels["bgimg"] + ":v") if "bgimg" in labels else None)
    if opts.kenburns:
        v = g.chain([v], [kenburns_filter(opts, out_dur)], prefix="kb")
    subs_filter = None
    if subtitles_ass:
        subs_filter = f"ass=filename={escape_path(subtitles_ass)}"
        if opts.fonts_dir:
            subs_filter += f":fontsdir={escape_path(opts.fonts_dir)}"
    v = add_overlays(
        g, v, opts, out_dur,
        frame_label=(labels["frame"] + ":v") if "frame" in labels else None,
        logo_label=(labels["logo"] + ":v") if "logo" in labels else None,
        subs_filter=subs_filter,
    )
    tail: list[str] = []
    if opts.freeze_last > 0:
        tail.append(f"tpad=stop_mode=clone:stop_duration={num(opts.freeze_last)}")
    if opts.fade_in > 0:
        tail.append(f"fade=t=in:st=0:d={num(opts.fade_in)}")
    if opts.fade_out > 0:
        tail.append(f"fade=t=out:st={num(max(0.0, total - opts.fade_out))}:d={num(opts.fade_out)}")
    tail += [f"fps={opts.fps}", "format=yuv420p", f"trim=0:{num(total)}"]
    g.chain([v], tail, out="vout")

    # --- audio ---
    if video_only:
        fg = g.render()
        cmd = [ffmpeg, "-hide_banner", "-y", "-nostats"]
        for args in inputs:
            cmd += args
        cmd += ["-filter_complex", fg, "-map", "[vout]", "-frames:v", "1", output]
        return RenderPlan(cmd=cmd, duration=total, inputs=[a[-1] for a in inputs], filtergraph=fg)
    if has_audio:
        base = "0:a"
    else:
        base = g.source(f"anullsrc=r=48000:cl=stereo,atrim=0:{num(seg.duration)}", prefix="sil")
    build_audio(
        g, base, opts,
        select_expr=sel, speed=speed, volume=seg.volume,
        out_duration=out_dur, total_duration=total,
        voice_label=(labels["voice"] + ":a") if "voice" in labels else None,
        music_label=(labels["music"] + ":a") if "music" in labels else None,
        sfx_labels=[(labels[k] + ":a", c) for k, c in sfx_keys] or None,
    )

    fg = g.render()
    cmd = [ffmpeg, "-hide_banner", "-y", "-nostats", "-progress", "pipe:1"]
    for args in inputs:
        cmd += args
    cmd += ["-filter_complex", fg, "-map", "[vout]", "-map", "[aout]"]
    cmd += encoder_args(opts)
    cmd += ["-r", str(opts.fps), "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", opts.audio_bitrate, "-ar", "48000", "-ac", "2",
            "-t", num(total), "-movflags", "+faststart", output]
    return RenderPlan(cmd=cmd, duration=total, inputs=[a[-1] for a in inputs], filtergraph=fg)


def build_preview_frame_command(ffmpeg: str, src: str, seg: Segment, opts: RenderOptions, at: float,
                                output_png: str, has_audio: bool = True,
                                subtitles_ass: str | None = None) -> list[str]:
    """Renderiza un único fotograma con todos los filtros, para previsualizar antes de exportar."""
    at = min(max(at, seg.start), seg.end - 0.01)
    one = Segment(start=at, end=min(seg.end, at + 0.5), speed=1.0)
    plan = build_render_command(ffmpeg, src, one, RenderOptions.from_dict({
        **opts.to_dict(), "fade_in": 0, "fade_out": 0, "freeze_last": 0, "music": None, "sfx": [],
        "voice": None, "loudnorm": False, "kenburns": False, "text_animation": "none"}), output_png,
        has_audio=has_audio, subtitles_ass=subtitles_ass, video_only=True)
    return plan.cmd


def build_scene_command(ffmpeg: str, src: str, threshold: float = 0.35) -> list[str]:
    """Detección de cambios de plano (filtro scene) con valores por fotograma."""
    return [ffmpeg, "-hide_banner", "-nostats", "-i", src, "-an",
            "-vf", f"scale=160:-2,select='gt(scene\\,{num(threshold)})',showinfo", "-f", "null", "-"]


def build_motion_command(ffmpeg: str, src: str, sample_fps: int = 2) -> list[str]:
    """Puntuación de movimiento: valor scene de cada fotograma muestreado."""
    return [ffmpeg, "-hide_banner", "-nostats", "-i", src, "-an",
            "-vf", f"fps={sample_fps},scale=160:-2,select='gte(scene\\,0)',metadata=print:file=-",
            "-f", "null", "-"]


def build_qc_command(ffmpeg: str, src: str) -> list[str]:
    """Comprobaciones técnicas: fotogramas negros, congelados y niveles de audio en una pasada."""
    return [ffmpeg, "-hide_banner", "-nostats", "-i", src,
            "-vf", "blackdetect=d=0.5:pix_th=0.10,freezedetect=n=0.003:d=2",
            "-af", "volumedetect", "-f", "null", "-"]


def build_thumbnail_command(ffmpeg: str, src: str, at: float, output: str) -> list[str]:
    return [ffmpeg, "-hide_banner", "-y", "-ss", num(at), "-i", src, "-frames:v", "1", "-q:v", "2", output]


def build_waveform_command(ffmpeg: str, src: str, output_png: str, width: int = 2000, height: int = 120) -> list[str]:
    return [ffmpeg, "-hide_banner", "-y", "-i", src, "-filter_complex",
            f"aformat=channel_layouts=mono,showwavespic=s={width}x{height}:colors=0x4FC3F7",
            "-frames:v", "1", output_png]
