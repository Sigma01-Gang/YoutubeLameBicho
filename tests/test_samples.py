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
