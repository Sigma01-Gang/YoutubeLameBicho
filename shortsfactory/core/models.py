"""Modelos de datos compartidos entre módulos (sin dependencias de UI)."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum


class Status(str, Enum):
    PENDING = "pendiente"
    AWAITING_APPROVAL = "esperando_aprobacion"
    UPLOADING = "subiendo"
    PUBLISHED = "publicado"
    ERROR = "error"
    RETRYING = "reintentando"

    @classmethod
    def values(cls) -> list[str]:
        return [s.value for s in cls]


@dataclass
class Segment:
    start: float
    end: float
    label: str = ""
    kind: str = "part"  # part | short
    speed: float = 1.0
    volume: float = 1.0
    score: float | None = None
    # Intervalos (en tiempo de origen) que se conservan; None = todo el segmento.
    keep: list[tuple[float, float]] | None = None

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    def kept_intervals(self) -> list[tuple[float, float]]:
        return list(self.keep) if self.keep else [(self.start, self.end)]

    def output_duration(self) -> float:
        """Duración del resultado (tras eliminar pausas y aplicar velocidad), sin congelado."""
        kept = sum(b - a for a, b in self.kept_intervals())
        return kept / (self.speed or 1.0)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Segment":
        d = dict(d)
        if d.get("keep"):
            d["keep"] = [tuple(x) for x in d["keep"]]
        return cls(**d)


@dataclass
class SubtitleStyle:
    font: str = "Arial"
    size: int = 64
    color: str = "#FFFFFF"
    outline_color: str = "#000000"
    outline: int = 4
    bold: bool = True
    position: str = "bottom"  # bottom | middle | top
    margin_v: int = 260
    max_chars: int = 32
    max_lines: int = 2
    animation: str = "none"  # none | fade | slide
    highlight_color: str = "#FFD400"  # karaoke


@dataclass
class SfxCue:
    path: str
    time: float
    volume: float = 1.0


@dataclass
class RenderOptions:
    # Formato
    width: int = 1080
    height: int = 1920
    fps: int = 30
    crf: int = 20
    preset: str = "medium"
    encoder: str = "libx264"  # libx264 | h264_nvenc | h264_qsv | h264_amf
    audio_bitrate: str = "192k"
    # Encuadre
    framing: str = "blur"  # crop | blur | face | manual | letterbox
    manual_x: float = 0.5  # 0..1 posición horizontal del recorte
    manual_y: float = 0.5
    face_track_file: str | None = None  # archivo sendcmd generado por el seguimiento de caras
    letterbox_color: str = "black"
    letterbox_image: str | None = None
    blur_strength: int = 20
    frame_png: str | None = None
    kenburns: bool = False
    kb_start_zoom: float = 1.0
    kb_end_zoom: float = 1.15
    kb_duration: float = 0.0  # 0 = toda la duración
    brightness: float = 0.0  # -1..1
    contrast: float = 1.0  # 0..3
    saturation: float = 1.0  # 0..3
    temperature: int = 6500  # K
    stabilize: bool = False
    stabilize_file: str | None = None  # transforms.trf de vidstabdetect
    autocrop: tuple[int, int, int, int] | None = None  # w,h,x,y detectado
    # Transiciones
    fade_in: float = 0.0
    fade_out: float = 0.0
    freeze_last: float = 0.0
    # Audio
    loudnorm: bool = True
    loudness_target: float = -14.0
    true_peak: float = -1.5
    lra: float = 11.0
    music: str | None = None
    music_volume: float = 0.15
    ducking: bool = True
    sfx: list[SfxCue] = field(default_factory=list)
    voice: str | None = None
    voice_mode: str = "mix"  # mix | replace
    voice_offset: float = 0.0
    # Subtítulos y textos
    subtitles: str | None = None  # .srt/.vtt/.ass ya recortado al segmento
    subtitle_style: SubtitleStyle = field(default_factory=SubtitleStyle)
    fonts_dir: str | None = None
    font_file: str | None = None  # para drawtext
    part_label: str | None = None  # "Parte 1/5"
    progress: tuple[float, float] | None = None  # (fracción inicio, fracción fin) de la historia
    progress_color: str = "#FF3B30"
    signature_text: str | None = None
    signature_logo: str | None = None
    signature_position: str = "top-right"
    signature_start: float = 0.0
    signature_duration: float = 0.0  # 0 = todo
    text_animation: str = "fade"  # none | fade | slide

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "RenderOptions":
        d = dict(d)
        if isinstance(d.get("subtitle_style"), dict):
            d["subtitle_style"] = SubtitleStyle(**d["subtitle_style"])
        if d.get("sfx"):
            d["sfx"] = [SfxCue(**s) if isinstance(s, dict) else s for s in d["sfx"]]
        for k in ("autocrop", "progress"):
            if d.get(k) is not None:
                d[k] = tuple(d[k])
        known = cls.__dataclass_fields__.keys()
        return cls(**{k: v for k, v in d.items() if k in known})


FORMAT_TEMPLATES: dict[str, tuple[int, int]] = {
    "9:16": (1080, 1920),
    "1:1": (1080, 1080),
    "4:5": (1080, 1350),
}
