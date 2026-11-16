"""Las planillas de ejemplo incluidas deben ser válidas (salvo la existencia de los vídeos)."""
from pathlib import Path

from shortsfactory.sheets import sheets as sh

SAMPLES = Path(__file__).resolve().parents[1] / "samples"


def test_sample_sheets_are_valid():
    for name in ("planilla_es.csv", "planilla_en.csv"):
        rows = sh.read_table(SAMPLES / name)
        assert sh.missing_columns(rows) == []
        res = sh.validate_rows(rows, check_files=False)
        assert all(r.ok for r in res), [r.errors for r in res]


def test_sample_groups_pair_languages():
    es = {r["grupo"] for r in sh.read_table(SAMPLES / "planilla_es.csv")}
    en = {r["grupo"] for r in sh.read_table(SAMPLES / "planilla_en.csv")}
    assert es == en


def test_resolve_tool_prefers_bundled_ffmpeg(tmp_path, monkeypatch):
    import sys
    from shortsfactory.config import resolve_tool

    exe = "ffmpeg.exe" if sys.platform.startswith("win") else "ffmpeg"
    (tmp_path / exe).write_bytes(b"")
    monkeypatch.setenv("SHORTSFACTORY_FFMPEG_DIR", str(tmp_path))
    assert resolve_tool("ffmpeg", "ffmpeg") == str(tmp_path / exe)
    assert resolve_tool("ffmpeg", "C:/otra/ffmpeg.exe") == "C:/otra/ffmpeg.exe"


def test_installer_bat_is_crlf_and_ascii():
    root = SAMPLES.parent
    for name in ("INSTALAR.bat", "DESINSTALAR.bat"):
        data = (root / name).read_bytes()
        assert data.count(b"\n") == data.count(b"\r\n"), f"{name} debe usar CRLF"
        assert all(b < 128 for b in data), f"{name} debe ser ASCII"
