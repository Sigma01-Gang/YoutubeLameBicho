"""Filtros de audio: normalización, ducking, efectos, voz (funciones puras)."""
from __future__ import annotations

from ..core.models import RenderOptions, SfxCue
from ..ffmpeg.graph import Graph, num


def atempo_chain(speed: float) -> list[str]:
    """atempo admite 0.5–100; encadenamos para valores fuera de rango."""
    if abs(speed - 1.0) < 1e-6:
        return []
    out = []
    s = speed
    while s < 0.5:
        out.append("atempo=0.5")
        s /= 0.5
    while s > 2.0:
        out.append("atempo=2.0")
        s /= 2.0
    out.append(f"atempo={num(s)}")
    return out


def loudnorm_filter(opts: RenderOptions) -> str:
    return f"loudnorm=I={num(opts.loudness_target)}:TP={num(opts.true_peak)}:LRA={num(opts.lra)}"


def build_audio(
    g: Graph,
    base: str,
    opts: RenderOptions,
    *,
    select_expr: str | None,
    speed: float,
    volume: float,
    out_duration: float,
    total_duration: float,
    voice_label: str | None = None,
    music_label: str | None = None,
    sfx_labels: list[tuple[str, SfxCue]] | None = None,
) -> str:
    """Construye la cadena de audio y devuelve la etiqueta final.

    - base: audio original del segmento (ya recortado por -ss/-t).
    - select_expr: expresión de aselect para eliminar pausas (en tiempo relativo al segmento).
    - out_duration: duración tras pausas y velocidad (sin congelado).
    - total_duration: duración final del clip (incluye congelado).
    """
    chain: list[str] = []
    if select_expr:
        chain += [f"aselect='{select_expr}'", "asetpts=N/SR/TB"]
    chain += atempo_chain(speed)
    if abs(volume - 1.0) > 1e-6:
        chain.append(f"volume={num(volume)}")
    chain.append("aresample=48000")
    cur = g.chain([base], chain, prefix="a")

    if voice_label:
        vchain = []
        if opts.voice_offset > 0:
            ms = int(opts.voice_offset * 1000)
            vchain.append(f"adelay={ms}|{ms}")
        vchain += ["aresample=48000", "aformat=channel_layouts=stereo"]
        voice = g.chain([voice_label], vchain, prefix="vo")
        if opts.voice_mode == "replace":
            cur = g.chain([voice], [f"apad=whole_dur={num(out_duration)}"], prefix="a")
        else:
            cur = g.chain([cur, voice], ["amix=inputs=2:duration=first:normalize=0"], prefix="a")

    if opts.loudnorm:
        cur = g.chain([cur], [loudnorm_filter(opts), "aresample=48000"], prefix="a")

    if music_label:
        mus = g.chain([music_label], [f"volume={num(opts.music_volume)}", "aresample=48000",
                                      "aformat=channel_layouts=stereo"], prefix="mu")
        if opts.ducking:
            main, sc = g.new("am"), g.new("sc")
            g.chain([cur], "asplit=2", [main, sc])
            mus = g.chain([mus, sc], ["sidechaincompress=threshold=0.03:ratio=8:attack=20:release=500"], prefix="du")
            cur = main
        cur = g.chain([cur, mus], ["amix=inputs=2:duration=first:normalize=0"], prefix="a")

    if sfx_labels:
        labels = [cur]
        for lab, cue in sfx_labels:
            ms = max(0, int(cue.time * 1000))
            labels.append(g.chain([lab], [f"adelay={ms}|{ms}", f"volume={num(cue.volume)}", "aresample=48000",
                                          "aformat=channel_layouts=stereo"], prefix="fx"))
        cur = g.chain(labels, [f"amix=inputs={len(labels)}:duration=first:normalize=0"], prefix="a")

    tail: list[str] = []
    freeze = total_duration - out_duration
    if freeze > 0.001:
        tail.append(f"apad=pad_dur={num(freeze)}")
    if opts.fade_in > 0:
        tail.append(f"afade=t=in:st=0:d={num(opts.fade_in)}")
    if opts.fade_out > 0:
        tail.append(f"afade=t=out:st={num(max(0.0, total_duration - opts.fade_out))}:d={num(opts.fade_out)}")
    tail.append("alimiter=limit=0.97")
    tail.append(f"atrim=0:{num(total_duration)}")
    return g.chain([cur], tail, out="aout")


def silencedetect_command(ffmpeg: str, src: str, noise_db: float = -35, min_dur: float = 0.4,
                          start: float | None = None, duration: float | None = None) -> list[str]:
    cmd = [ffmpeg, "-hide_banner", "-nostats"]
    if start is not None:
        cmd += ["-ss", num(start)]
    if duration is not None:
        cmd += ["-t", num(duration)]
    cmd += ["-i", src, "-vn", "-af", f"silencedetect=noise={num(noise_db)}dB:d={num(min_dur)}", "-f", "null", "-"]
    return cmd


def volumedetect_command(ffmpeg: str, src: str) -> list[str]:
    return [ffmpeg, "-hide_banner", "-nostats", "-i", src, "-vn", "-af", "volumedetect", "-f", "null", "-"]


def waveform_pcm_command(ffmpeg: str, src: str, rate: int = 4000) -> list[str]:
    """Extrae audio mono PCM s16le a baja frecuencia para dibujar la forma de onda."""
    return [ffmpeg, "-hide_banner", "-v", "error", "-i", src, "-vn", "-ac", "1", "-ar", str(rate),
            "-f", "s16le", "-"]
