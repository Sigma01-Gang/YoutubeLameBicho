from shortsfactory.sheets.sync import SheetService


def _write(path, rows):
    header = "archivo_video,canal,titulo,descripcion,etiquetas,fecha_hora_publicacion,estado,url_publicado,grupo\n"
    path.write_text(header + "\n".join(rows) + "\n", encoding="utf-8")


def test_multi_source_sync_preserves_state_and_history(ctx, tmp_path):
    es = ctx.channels.create("Español", "es")
    en = ctx.channels.clone(es.id, "Inglés", "en")
    for n in ("h_p1.mp4", "h_p2.mp4"):
        (tmp_path / n).write_bytes(b"x")
    s_es, s_en = tmp_path / "es.csv", tmp_path / "en.csv"
    _write(s_es, ["h_p1.mp4,Español,Historia 1,d,a,2026-12-01 18:00,,,g1",
                  "h_p2.mp4,Español,Historia 2,d,a,fecha-mala,,,g2"])
    _write(s_en, ["h_p1.mp4,Inglés,Story 1,d,a,2026-12-01 18:00,,,g1"])
    r1 = ctx.sheets.load_source(str(s_es), "Español")
    r2 = ctx.sheets.load_source(str(s_en), "Inglés")
    assert r1.inserted == 2 and r1.error_count == 1 and r2.inserted == 1
    rows = ctx.sheets.rows(es.id)
    assert len(rows) == 2

    # la app marca la fila como publicada
    rid = rows[0]["id"]
    ctx.sheets.update_field(rid, "estado", "publicado")
    ctx.sheets.update_field(rid, "url_publicado", "https://youtube.com/shorts/abcdefghijk")
    # se recarga la planilla con un título cambiado y estado vacío
    _write(s_es, ["h_p1.mp4,Español,Historia 1 (editada),d,a,2026-12-01 18:00,,,g1",
                  "h_p2.mp4,Español,Historia 2,d,a,fecha-mala,,,g2"])
    r3 = ctx.sheets.load_source(str(s_es), "Español")
    assert r3.updated == 1 and r3.inserted == 0
    row = [r for r in ctx.sheets.rows(es.id) if r["id"] == rid][0]
    assert row["estado"] == "publicado" and row["url_publicado"].startswith("https://")
    assert row["titulo"] == "Historia 1 (editada)"
    fields = [h["field"] for h in ctx.sheets.history(rid)]
    assert "titulo" in fields and "estado" in fields

    # alertas multi-idioma: g2 no tiene versión en inglés
    alerts = ctx.channels.translation_alerts()
    assert any("g2" in a for a in alerts) and not any("«g1»" in a and "no tiene" in a for a in alerts)

    out = ctx.sheets.export(str(tmp_path / "export.csv"), es.id)
    assert "publicado" in out.read_text(encoding="utf-8-sig")


def test_channel_clone_is_isolated(ctx):
    es = ctx.channels.create("ES", "es", preferred_slots=["18:00-22:00"], daily_limit=3)
    en = ctx.channels.clone(es.id, "EN", "en")
    assert en.profile_dir != es.profile_dir and en.preferred_slots == ["18:00-22:00"] and en.daily_limit == 3
    assert "Part" in en.templates["titulo"]
