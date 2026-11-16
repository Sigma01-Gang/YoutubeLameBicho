"""Hashes, caché de renderizado, espacio en disco, temporales y biblioteca de activos."""
from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from pathlib import Path

from ..persistence.db import Database, now_iso

ASSET_KINDS = {
    "fondo": {".png", ".jpg", ".jpeg", ".webp", ".mp4", ".mov"},
    "musica": {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac"},
    "sfx": {".mp3", ".wav", ".ogg"},
    "overlay": {".png", ".webp"},
    "fuente": {".ttf", ".otf"},
}


def sha256_file(path: str | Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def quick_fingerprint(path: str | Path) -> str:
    """Huella rápida (tamaño + mtime + primeros/últimos MB) para la caché de vídeos grandes."""
    p = Path(path)
    st = p.stat()
    h = hashlib.sha256(f"{st.st_size}:{int(st.st_mtime)}".encode())
    with open(p, "rb") as f:
        h.update(f.read(1 << 20))
        if st.st_size > (2 << 20):
            f.seek(-(1 << 20), 2)
            h.update(f.read(1 << 20))
    return h.hexdigest()


def verify_hash(path: str | Path, expected: str) -> bool:
    return bool(expected) and Path(path).is_file() and sha256_file(path) == expected


def cache_key(source_fp: str, segment: dict, options: dict, extra: str = "") -> str:
    blob = json.dumps({"src": source_fp, "seg": segment, "opt": options, "x": extra}, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


class RenderCache:
    def __init__(self, db: Database):
        self.db = db

    def get(self, key: str) -> str | None:
        row = self.db.one("SELECT output_path FROM render_cache WHERE key=?", (key,))
        if row and Path(row["output_path"]).is_file():
            return row["output_path"]
        if row:
            self.db.execute("DELETE FROM render_cache WHERE key=?", (key,))
        return None

    def put(self, key: str, output: str) -> None:
        self.db.execute(
            "INSERT INTO render_cache(key, output_path, created_at) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET output_path=excluded.output_path, created_at=excluded.created_at",
            (key, output, now_iso()),
        )


def free_space(path: str | Path) -> int:
    p = Path(path)
    while not p.exists() and p != p.parent:
        p = p.parent
    return shutil.disk_usage(p).free


def estimate_output_size(duration: float, video_kbps: int = 8000, audio_kbps: int = 192) -> int:
    return int(duration * (video_kbps + audio_kbps) * 1000 / 8 * 1.3)


def ensure_space(path: str | Path, needed: int) -> None:
    free = free_space(path)
    if free < needed:
        raise OSError(f"espacio insuficiente en {path}: libre {free // 2**20} MB, necesario {needed // 2**20} MB")


def check_source_integrity(tools, path: str) -> tuple[bool, str]:
    """Decodifica rápidamente el archivo buscando errores (solo cabeceras y flujo de vídeo a baja resolución)."""
    from ..ffmpeg import runner

    p = Path(path)
    if not p.is_file() or p.stat().st_size == 0:
        return False, "el archivo no existe o está vacío"
    try:
        info = runner.media_info(tools, path)
    except Exception as exc:  # noqa: BLE001
        return False, f"ffprobe no puede leerlo: {exc}"
    if info.duration <= 0 or info.width <= 0:
        return False, "sin duración o sin pista de vídeo"
    return True, "ok"


def make_temp_dir(base: str | Path | None = None) -> Path:
    return Path(tempfile.mkdtemp(prefix="sf_", dir=str(base) if base else None))


def cleanup_temp(base: str | Path, older_than_hours: float = 12) -> int:
    import time

    removed = 0
    limit = time.time() - older_than_hours * 3600
    for d in Path(base).glob("sf_*"):
        try:
            if d.stat().st_mtime < limit:
                shutil.rmtree(d, ignore_errors=True) if d.is_dir() else d.unlink()
                removed += 1
        except OSError:
            pass
    return removed


# --- biblioteca de activos ---------------------------------------------------

def guess_kind(path: str | Path) -> str:
    ext = Path(path).suffix.lower()
    for kind, exts in ASSET_KINDS.items():
        if ext in exts:
            return kind
    return "otro"


class AssetLibrary:
    def __init__(self, db: Database):
        self.db = db

    def add(self, path: str, kind: str | None = None, tags: list[str] | None = None) -> None:
        self.db.execute(
            "INSERT INTO assets(path, kind, tags, added_at) VALUES(?,?,?,?) "
            "ON CONFLICT(path) DO UPDATE SET kind=excluded.kind, tags=excluded.tags",
            (str(path), kind or guess_kind(path), ",".join(tags or []), now_iso()),
        )

    def add_folder(self, folder: str, tags: list[str] | None = None) -> int:
        n = 0
        for p in Path(folder).rglob("*"):
            if p.is_file() and guess_kind(p) != "otro":
                self.add(str(p), tags=tags)
                n += 1
        return n

    def remove(self, path: str) -> None:
        self.db.execute("DELETE FROM assets WHERE path=?", (path,))

    def list(self, kind: str | None = None, tag: str | None = None) -> list[dict]:
        rows = [dict(r) for r in self.db.query("SELECT * FROM assets ORDER BY kind, path")]
        if kind:
            rows = [r for r in rows if r["kind"] == kind]
        if tag:
            rows = [r for r in rows if tag.lower() in [t.strip().lower() for t in r["tags"].split(",")]]
        return rows
