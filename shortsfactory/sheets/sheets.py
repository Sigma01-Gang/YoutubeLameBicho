"""Planillas CSV/XLSX: carga, validación por fila, reglas de relleno y exportación."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from ..config import DESCRIPTION_MAX, TAGS_MAX_TOTAL, TITLE_MAX
from ..core.models import Status
from ..core.timeutil import parse_datetime

REQUIRED_COLUMNS = [
    "archivo_video", "canal", "titulo", "descripcion", "etiquetas",
    "fecha_hora_publicacion", "estado", "url_publicado",
]
OPTIONAL_COLUMNS = ["grupo", "miniatura", "subtitulos"]
ALL_COLUMNS = REQUIRED_COLUMNS + OPTIONAL_COLUMNS

# Alias aceptados (planillas en inglés o exportadas de Google Sheets con otros nombres)
ALIASES = {
    "video_file": "archivo_video", "file": "archivo_video", "archivo": "archivo_video", "video": "archivo_video",
    "channel": "canal", "title": "titulo", "description": "descripcion", "tags": "etiquetas",
    "publish_at": "fecha_hora_publicacion", "publish_date": "fecha_hora_publicacion", "fecha": "fecha_hora_publicacion",
    "status": "estado", "published_url": "url_publicado", "url": "url_publicado",
    "group": "grupo", "group_id": "grupo", "thumbnail": "miniatura", "subtitles": "subtitulos",
}

FORBIDDEN_CHARS = set("<>")


def normalize_header(h: str) -> str:
    s = unicodedata.normalize("NFKD", str(h)).encode("ascii", "ignore").decode().strip().lower()
    s = re.sub(r"[\s\-/]+", "_", s)
    return ALIASES.get(s, s)


@dataclass
class RowResult:
    index: int  # número de fila en la planilla (1 = primera fila de datos)
    data: dict
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def read_table(path: str | Path) -> list[dict]:
    """Lee CSV (detecta separador , ; o tab) o XLSX. Todos los valores como texto."""
    import pandas as pd

    p = Path(path)
    if p.suffix.lower() in (".xlsx", ".xlsm", ".xls"):
        df = pd.read_excel(p, dtype=str, engine="openpyxl" if p.suffix.lower() != ".xls" else None)
    else:
        df = pd.read_csv(p, dtype=str, sep=None, engine="python", encoding="utf-8-sig", keep_default_na=False)
    df = df.fillna("")
    df.columns = [normalize_header(c) for c in df.columns]
    rows = []
    for rec in df.to_dict(orient="records"):
        rows.append({k: ("" if v is None else str(v).strip()) for k, v in rec.items()})
    return rows


def missing_columns(rows: list[dict], columns: list[str] | None = None) -> list[str]:
    if not rows:
        return []
    present = set(rows[0].keys())
    return [c for c in (columns or REQUIRED_COLUMNS) if c not in present]


def resolve_video_path(value: str, base_dirs: list[str | Path]) -> Path | None:
    if not value:
        return None
    p = Path(value)
    if p.is_absolute():
        return p if p.is_file() else None
    for b in base_dirs:
        cand = Path(b) / value
        if cand.is_file():
            return cand
    return None


def validate_rows(rows: list[dict], base_dirs: list[str | Path] | None = None,
                  check_files: bool = True, history_titles: set[str] | None = None) -> list[RowResult]:
    """Valida cada fila de forma independiente: una fila errónea no detiene al resto."""
    base_dirs = base_dirs or []
    results: list[RowResult] = []
    seen_files: dict[str, int] = {}
    seen_titles: dict[str, int] = {}
    history_titles = {t.strip().lower() for t in (history_titles or set())}
    for i, raw in enumerate(rows, 1):
        r = RowResult(i, {c: raw.get(c, "") for c in set(ALL_COLUMNS) | set(raw.keys())})
        d = r.data
        try:
            archivo = d.get("archivo_video", "")
            if not archivo:
                r.errors.append("falta archivo_video")
            elif check_files and resolve_video_path(archivo, base_dirs) is None:
                r.errors.append(f"el archivo no existe: {archivo}")
            key = archivo.lower()
            if archivo and key in seen_files:
                r.errors.append(f"archivo duplicado (igual que la fila {seen_files[key]})")
            elif archivo:
                seen_files[key] = i

            titulo = d.get("titulo", "")
            if not titulo:
                r.errors.append("falta el título")
            elif len(titulo) > TITLE_MAX:
                r.errors.append(f"título de {len(titulo)} caracteres (máx. {TITLE_MAX})")
            tkey = titulo.strip().lower()
            if titulo and tkey in seen_titles:
                r.errors.append(f"título duplicado (igual que la fila {seen_titles[tkey]})")
            elif titulo:
                seen_titles[tkey] = i
            if titulo and tkey in history_titles:
                r.warnings.append("título ya usado en un vídeo publicado")

            desc = d.get("descripcion", "")
            if len(desc) > DESCRIPTION_MAX:
                r.errors.append(f"descripción de {len(desc)} caracteres (máx. {DESCRIPTION_MAX})")

            for fname in ("titulo", "descripcion", "etiquetas"):
                bad = sorted(set(d.get(fname, "")) & FORBIDDEN_CHARS)
                if bad:
                    r.errors.append(f"caracteres no permitidos en {fname}: {' '.join(bad)}")
                if any(ord(ch) < 32 and ch not in "\n\r\t" for ch in d.get(fname, "")):
                    r.errors.append(f"caracteres de control en {fname}")

            tags = split_tags(d.get("etiquetas", ""))
            if sum(len(t) for t in tags) + max(0, len(tags) - 1) > TAGS_MAX_TOTAL:
                r.errors.append(f"las etiquetas superan {TAGS_MAX_TOTAL} caracteres en total")

            try:
                parse_datetime(d.get("fecha_hora_publicacion", ""))
            except ValueError as exc:
                r.errors.append(str(exc))

            estado = d.get("estado", "").strip().lower()
            if estado and estado not in Status.values():
                r.warnings.append(f"estado desconocido «{estado}», se tratará como pendiente")
        except Exception as exc:  # noqa: BLE001 — nunca debe parar el resto
            r.errors.append(f"error inesperado: {exc}")
        results.append(r)
    return results


def split_tags(value: str) -> list[str]:
    return [t.strip().lstrip("#") for t in re.split(r"[,;\n]", value or "") if t.strip()]


# --- reglas y plantillas ----------------------------------------------------

DEFAULT_FILENAME_PATTERN = r"(?P<titulo>.+?)[ _-]+(?:parte|part|p)[ _-]*(?P<parte>\d+)(?:[ _-]*(?:de|of)[ _-]*(?P<total>\d+))?"


def extract_vars(filename: str, pattern: str = DEFAULT_FILENAME_PATTERN) -> dict:
    """Extrae variables del nombre de archivo, p. ej. "La_casa_parte3_de7.mp4" -> titulo, parte, total."""
    stem = Path(filename).stem
    out = {"archivo": Path(filename).name, "nombre": stem}
    m = re.search(pattern, stem, flags=re.IGNORECASE)
    if m:
        out.update({k: v for k, v in m.groupdict().items() if v is not None})
    if "titulo" in out:
        out["titulo"] = re.sub(r"[_]+", " ", out["titulo"]).strip()
    else:
        out["titulo"] = re.sub(r"[_]+", " ", stem).strip()
    return out


class _SafeDict(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def render_template(template: str, variables: dict, blocks: dict | None = None) -> str:
    """Sustituye {variables} y {bloque:nombre}. Las variables desconocidas se dejan tal cual."""
    blocks = blocks or {}
    text = re.sub(r"\{bloque:([\w-]+)\}", lambda m: blocks.get(m.group(1), ""), template or "")
    return text.format_map(_SafeDict(variables)).strip()


def apply_rules(rows: list[dict], templates: dict, blocks: dict | None = None,
                pattern: str = DEFAULT_FILENAME_PATTERN, overwrite: bool = False) -> list[dict]:
    """Rellena titulo/descripcion/etiquetas vacíos usando las plantillas del canal."""
    out = []
    for row in rows:
        new = dict(row)
        vars_ = extract_vars(row.get("archivo_video", ""), pattern)
        vars_.update({"canal": row.get("canal", "")})
        for col, tkey in (("titulo", "titulo"), ("descripcion", "descripcion"), ("etiquetas", "etiquetas")):
            tpl = templates.get(tkey)
            if tpl and (overwrite or not new.get(col)):
                new[col] = render_template(tpl, vars_, blocks)
        out.append(new)
    return out


def match_folder(rows: list[dict], folder: str | Path, exts=(".mp4", ".mov", ".mkv", ".webm")) -> tuple[list[dict], list[str]]:
    """Empareja vídeos de una carpeta con las filas por nombre de archivo (sin distinguir mayúsculas ni extensión).

    Devuelve (filas actualizadas con ruta absoluta, archivos sin fila).
    """
    files = {p.name.lower(): p for p in Path(folder).iterdir() if p.suffix.lower() in exts}
    stems = {p.stem.lower(): p for p in files.values()}
    used = set()
    out = []
    for row in rows:
        new = dict(row)
        name = Path(row.get("archivo_video", "")).name.lower()
        stem = Path(name).stem
        hit = files.get(name) or stems.get(stem)
        if hit:
            new["archivo_video"] = str(hit)
            used.add(hit)
        out.append(new)
    unmatched = [p.name for p in files.values() if p not in used]
    return out, sorted(unmatched)


def preview_card(row: dict) -> str:
    """Texto aproximado de cómo se verá en YouTube (título truncado y primeras líneas de descripción)."""
    title = row.get("titulo", "")
    shown = title if len(title) <= 70 else title[:67] + "…"
    desc_lines = (row.get("descripcion", "") or "").splitlines()
    tags = split_tags(row.get("etiquetas", ""))
    hashtags = " ".join("#" + t.replace(" ", "") for t in tags[:3])
    return f"{shown}\n{len(title)}/{TITLE_MAX}\n\n" + "\n".join(desc_lines[:3]) + (f"\n{hashtags}" if hashtags else "")


def write_template(path: str | Path, channel: str = "Español") -> Path:
    import pandas as pd

    p = Path(path)
    example = {
        "archivo_video": "mi_historia_parte1_de5.mp4", "canal": channel,
        "titulo": "Mi historia (Parte 1/5)", "descripcion": "Primera parte de la historia.\n#historias",
        "etiquetas": "historias, misterio, parte 1", "fecha_hora_publicacion": "2026-12-01 18:00",
        "estado": "pendiente", "url_publicado": "", "grupo": "mi_historia_p1", "miniatura": "", "subtitulos": "",
    }
    df = pd.DataFrame([example], columns=ALL_COLUMNS)
    if p.suffix.lower() == ".xlsx":
        df.to_excel(p, index=False)
    else:
        df.to_csv(p, index=False, encoding="utf-8-sig")
    return p


def write_table(rows: list[dict], path: str | Path) -> Path:
    import pandas as pd

    p = Path(path)
    cols = ALL_COLUMNS + sorted({k for r in rows for k in r} - set(ALL_COLUMNS))
    df = pd.DataFrame(rows, columns=cols).fillna("")
    if p.suffix.lower() == ".xlsx":
        df.to_excel(p, index=False)
    else:
        df.to_csv(p, index=False, encoding="utf-8-sig")
    return p
