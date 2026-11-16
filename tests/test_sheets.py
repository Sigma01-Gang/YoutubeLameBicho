import pytest
from shortsfactory.sheets import sheets as sh


def _row(**kw):
    base = {"archivo_video": "v1.mp4", "canal": "Español", "titulo": "Título", "descripcion": "d",
            "etiquetas": "a,b", "fecha_hora_publicacion": "2026-12-01 18:00", "estado": "", "url_publicado": ""}
    base.update(kw)
    return base


def test_errors_per_row_do_not_block_others(tmp_path):
    (tmp_path / "v1.mp4").write_bytes(b"x")
    (tmp_path / "v3.mp4").write_bytes(b"x")
    rows = [
        _row(),
        _row(archivo_video="noexiste.mp4", titulo="x" * 101),
        _row(archivo_video="v3.mp4", titulo="Otro <b>", fecha_hora_publicacion="ayer", descripcion="y" * 5001),
        _row(archivo_video="v1.mp4", titulo="Título"),
    ]
    res = sh.validate_rows(rows, [tmp_path])
    assert res[0].ok
    assert any("no existe" in e for e in res[1].errors) and any("101" in e for e in res[1].errors)
    errs = " ".join(res[2].errors)
    assert "no permitidos" in errs and "fecha no válida" in errs and "5001" in errs
    assert any("duplicado" in e for e in res[3].errors)
    assert len(res) == 4


def test_duplicate_title_against_history(tmp_path):
    (tmp_path / "v1.mp4").write_bytes(b"x")
    res = sh.validate_rows([_row()], [tmp_path], history_titles={"título"})
    assert res[0].ok and res[0].warnings


def test_read_csv_xlsx_aliases(tmp_path):
    csvp = tmp_path / "s.csv"
    csvp.write_text("Video File;Channel;Title;Description;Tags;Publish At;Status;URL\n"
                    "a.mp4;Inglés;Hi;d;t;2026-12-01 10:00;;\n", encoding="utf-8")
    rows = sh.read_table(csvp)
    assert rows[0]["archivo_video"] == "a.mp4" and rows[0]["canal"] == "Inglés"
    assert sh.missing_columns(rows) == []
    x = sh.write_table(rows, tmp_path / "s.xlsx")
    assert sh.read_table(x)[0]["titulo"] == "Hi"


def test_template_download(tmp_path):
    p = sh.write_template(tmp_path / "plantilla.csv")
    rows = sh.read_table(p)
    assert sh.missing_columns(rows) == []


def test_rules_from_filename():
    vars_ = sh.extract_vars("La_casa_del_lago_parte3_de7.mp4")
    assert vars_["titulo"] == "La casa del lago" and vars_["parte"] == "3" and vars_["total"] == "7"
    rows = sh.apply_rules([{"archivo_video": "La_casa_parte2.mp4", "titulo": ""}],
                          {"titulo": "{titulo} (Parte {parte})", "descripcion": "{bloque:sub} {desconocido}"},
                          {"sub": "Suscríbete"})
    assert rows[0]["titulo"] == "La casa (Parte 2)"
    assert rows[0]["descripcion"] == "Suscríbete {desconocido}"


def test_match_folder(tmp_path):
    (tmp_path / "Parte1.MP4").write_bytes(b"x")
    (tmp_path / "extra.mp4").write_bytes(b"x")
    rows, unmatched = sh.match_folder([{"archivo_video": "parte1.mp4"}, {"archivo_video": "nada.mp4"}], tmp_path)
    assert rows[0]["archivo_video"].endswith("Parte1.MP4") and rows[1]["archivo_video"] == "nada.mp4"
    assert unmatched == ["extra.mp4"]


def test_preview_card():
    card = sh.preview_card({"titulo": "T" * 80, "descripcion": "l1\nl2\nl3\nl4", "etiquetas": "a b, c"})
    assert "…" in card and "80/100" in card and "l4" not in card and "#ab" in card
