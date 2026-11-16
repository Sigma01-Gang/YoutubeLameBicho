"""Pestaña Cola: orden por arrastre, pausas, desplazamiento de fechas, simulación y calendario."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QDate, Qt
from PySide6.QtGui import QColor, QTextCharFormat
from PySide6.QtWidgets import (QAbstractItemView, QCalendarWidget, QComboBox, QDialog, QDoubleSpinBox, QFormLayout,
                               QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QMessageBox, QPlainTextEdit,
                               QPushButton, QSplitter, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget, QDialogButtonBox)

from ...upload.studio import AUTOMATION_NOTICE

COLS = ["#", "Canal", "Título", "Archivo", "Estado", "Subida prevista", "Publicación", "Intentos", "Último error"]
STATUS_COLORS = {
    "pendiente": QColor(33, 150, 243, 60), "esperando_aprobacion": QColor(255, 193, 7, 70),
    "subiendo": QColor(156, 39, 176, 70), "publicado": QColor(76, 175, 80, 70),
    "error": QColor(244, 67, 54, 80), "reintentando": QColor(255, 87, 34, 70),
}


class QueueTree(QTreeWidget):
    def __init__(self, on_reorder):
        super().__init__()
        self.on_reorder = on_reorder
        self.setColumnCount(len(COLS))
        self.setHeaderLabels(COLS)
        self.setRootIsDecorated(False)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setDefaultDropAction(Qt.MoveAction)

    def dropEvent(self, e):  # noqa: N802
        super().dropEvent(e)
        ids = [self.topLevelItem(i).data(0, Qt.UserRole) for i in range(self.topLevelItemCount())]
        self.on_reorder([i for i in ids if i is not None])


class MetaDialog(QDialog):
    def __init__(self, item: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Editar metadatos")
        f = QFormLayout(self)
        self.title = QLineEdit(item["title"])
        self.desc = QPlainTextEdit(item["description"])
        self.tags = QLineEdit(item["tags"])
        self.publish = QLineEdit(item["publish_at"])
        self.publish.setPlaceholderText("AAAA-MM-DD HH:MM (vacío = publicar al subir)")
        self.thumb = QLineEdit(item["thumbnail"])
        self.subs = QLineEdit(item["subtitles"])
        for lab, w in (("Título", self.title), ("Descripción", self.desc), ("Etiquetas", self.tags),
                       ("Publicación", self.publish), ("Miniatura", self.thumb), ("Subtítulos SRT", self.subs)):
            f.addRow(lab, w)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        f.addRow(bb)

    def values(self) -> dict:
        return {"title": self.title.text()[:100], "description": self.desc.toPlainText()[:5000],
                "tags": self.tags.text(), "publish_at": self.publish.text().strip(),
                "thumbnail": self.thumb.text().strip(), "subtitles": self.subs.text().strip()}


class QueueTab(QWidget):
    def __init__(self, ctx, tasks, main=None):
        super().__init__()
        self.ctx, self.tasks, self.main = ctx, tasks, main
        lay = QVBoxLayout(self)
        notice = QLabel("⚠ " + AUTOMATION_NOTICE)
        notice.setWordWrap(True)
        notice.setStyleSheet("background:#5d4037; color:white; padding:6px; border-radius:4px;")
        lay.addWidget(notice)

        bar = QHBoxLayout()
        self.run_btn = QPushButton("▶ Iniciar cola")
        self.run_btn.setCheckable(True)
        self.run_btn.toggled.connect(self.toggle_run)
        self.pause_btn = QPushButton("Pausa global")
        self.pause_btn.setCheckable(True)
        self.pause_btn.setChecked(bool(ctx.db.get_setting("global_pause", False)))
        self.pause_btn.toggled.connect(lambda v: (ctx.db.set_setting("global_pause", v), self.refresh()))
        bar.addWidget(self.run_btn)
        bar.addWidget(self.pause_btn)
        for text, fn in (("Pausar/reanudar canal…", self.toggle_channel), ("Desplazar fechas…", self.shift),
                         ("Simulación", self.simulate), ("Editar…", self.edit_meta), ("Reintentar", self.retry),
                         ("Quitar", self.remove), ("Subir ahora (siguiente)", self.tick_now)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            bar.addWidget(b)
        lay.addLayout(bar)
        info = QHBoxLayout()
        self.interval = QDoubleSpinBox()
        self.interval.setRange(0.25, 48)
        self.interval.setSingleStep(0.5)
        self.interval.setValue(float(ctx.db.get_setting("upload_interval_hours", 3)))
        self.interval.valueChanged.connect(lambda v: (ctx.db.set_setting("upload_interval_hours", v), self.refresh()))
        info.addWidget(QLabel("Intervalo entre subidas (h):"))
        info.addWidget(self.interval)
        self.status = QLabel()
        info.addWidget(self.status, 1)
        lay.addLayout(info)

        split = QSplitter(Qt.Horizontal)
        self.tree = QueueTree(self.reorder)
        split.addWidget(self.tree)
        cal_box = QWidget()
        cl = QVBoxLayout(cal_box)
        self.cal_channel = QComboBox()
        self.cal_channel.currentIndexChanged.connect(self.refresh_calendar)
        self.calendar = QCalendarWidget()
        self.calendar.selectionChanged.connect(self.refresh_day)
        self.day_list = QListWidget()
        cl.addWidget(QLabel("Calendario por canal:"))
        cl.addWidget(self.cal_channel)
        cl.addWidget(self.calendar)
        cl.addWidget(self.day_list)
        split.addWidget(cal_box)
        split.setSizes([950, 350])
        lay.addWidget(split, 1)
        self.refresh()

    # --------------------------------------------------------------------------
    def refresh(self):
        self.ctx.queue.save_plan()
        items = self.ctx.queue.items()
        sel = set(self.selected_ids())
        self.tree.clear()
        for it in items:
            row = QTreeWidgetItem([str(it["position"]), it["canal"], it["title"], Path(it["video_path"]).name,
                                   it["status"], it["scheduled_at"] or ("—" if it["status"] != "publicado" else ""),
                                   it["publish_at"] or "al subir", str(it["attempts"]), it["last_error"][:120]])
            row.setData(0, Qt.UserRole, it["id"])
            row.setFlags(row.flags() & ~Qt.ItemIsDropEnabled)
            col = STATUS_COLORS.get(it["status"])
            if col:
                for c in range(len(COLS)):
                    row.setBackground(c, col)
            self.tree.addTopLevelItem(row)
            if it["id"] in sel:
                row.setSelected(True)
        for c in range(len(COLS)):
            self.tree.resizeColumnToContents(c)
        paused = [c.name for c in self.ctx.channels.list() if c.paused]
        last = self.ctx.queue.last_upload_started()
        self.status.setText(
            f"{'EN MARCHA' if self.run_btn.isChecked() else 'Detenida'} · "
            f"{'SIMULACIÓN · ' if self.ctx.db.get_setting('simulation_mode', False) else ''}"
            f"{'MODO PRUEBA (privado/no listado) · ' if self.ctx.db.get_setting('test_mode', False) else ''}"
            f"Última subida: {last or '—'} · Canales en pausa: {', '.join(paused) or 'ninguno'}")
        cur = self.cal_channel.currentData()
        self.cal_channel.blockSignals(True)
        self.cal_channel.clear()
        self.cal_channel.addItem("(todos)", None)
        for c in self.ctx.channels.list():
            self.cal_channel.addItem(c.name, c.id)
        self.cal_channel.setCurrentIndex(max(0, self.cal_channel.findData(cur)))
        self.cal_channel.blockSignals(False)
        self.refresh_calendar()

    def _dates_for_calendar(self) -> list[tuple[datetime, dict]]:
        out = []
        ch = self.cal_channel.currentData()
        for it in self.ctx.queue.items(ch):
            s = it["publish_at"] or it["scheduled_at"] or it["published_at"]
            try:
                out.append((datetime.fromisoformat(s.strip()), it))
            except (ValueError, AttributeError):
                continue
        return out

    def refresh_calendar(self):
        self.calendar.setDateTextFormat(QDate(), QTextCharFormat())
        fmt = QTextCharFormat()
        fmt.setBackground(QColor(79, 195, 247, 120))
        fmt.setFontWeight(700)
        for dt, _ in self._dates_for_calendar():
            self.calendar.setDateTextFormat(QDate(dt.year, dt.month, dt.day), fmt)
        self.refresh_day()

    def refresh_day(self):
        d = self.calendar.selectedDate().toPython()
        self.day_list.clear()
        for dt, it in sorted(self._dates_for_calendar(), key=lambda x: x[0]):
            if dt.date() == d:
                self.day_list.addItem(f"{dt:%H:%M} [{it['canal']}] {it['title']} ({it['status']})")

    def selected_ids(self) -> list[int]:
        return [i.data(0, Qt.UserRole) for i in self.tree.selectedItems()]

    def reorder(self, ids):
        self.ctx.queue.reorder(ids)
        self.refresh()

    def toggle_run(self, on: bool):
        self.run_btn.setText("⏸ Detener cola" if on else "▶ Iniciar cola")
        if self.main:
            self.main.set_queue_running(on)
        self.refresh()

    def toggle_channel(self):
        chans = self.ctx.channels.list()
        if not chans:
            return
        name, ok = QInputDialog.getItem(self, "Canal", "Canal:", [f"{c.name} ({'en pausa' if c.paused else 'activo'})"
                                                                  for c in chans], 0, False)
        if ok:
            ch = chans[[f"{c.name} ({'en pausa' if c.paused else 'activo'})" for c in chans].index(name)]
            self.ctx.channels.set_paused(ch.id, not ch.paused)
            self.refresh()

    def shift(self):
        hours, ok = QInputDialog.getDouble(self, "Desplazar fechas", "Horas (negativo para adelantar):", 2, -720, 720, 1)
        if ok:
            n = self.ctx.queue.shift_dates(hours)
            QMessageBox.information(self, "Fechas", f"Desplazadas {n} fechas de publicación.")
            self.refresh()

    def simulate(self):
        plan = self.ctx.queue.plan()
        lines = []
        for it, t in plan:
            when = f"{t:%a %d/%m %H:%M}" if t else "EN PAUSA"
            appr = "" if it["status"] != "esperando_aprobacion" else " (necesita aprobación)"
            lines.append(f"{when} · [{it['canal']}] {it['title']}{appr} → publica: {it['publish_at'] or 'al subir'}")
        dlg = QDialog(self)
        dlg.setWindowTitle("Simulación: qué se publicaría y cuándo (no se sube nada)")
        l = QVBoxLayout(dlg)
        txt = QPlainTextEdit("\n".join(lines) or "La cola está vacía.")
        txt.setReadOnly(True)
        l.addWidget(txt)
        dlg.resize(800, 500)
        dlg.exec()

    def edit_meta(self):
        ids = self.selected_ids()
        if not ids:
            return
        item = self.ctx.queue.get(ids[0])
        dlg = MetaDialog(item, self)
        if dlg.exec():
            self.ctx.queue.update_meta(ids[0], **dlg.values())
            self.refresh()

    def retry(self):
        for i in self.selected_ids():
            self.ctx.queue.reset(i)
        self.refresh()

    def remove(self):
        ids = self.selected_ids()
        if ids and QMessageBox.question(self, "Quitar", f"¿Quitar {len(ids)} elemento(s) de la cola?") == QMessageBox.Yes:
            for i in ids:
                self.ctx.queue.remove(i)
            self.refresh()

    def tick_now(self):
        if self.main:
            self.main.queue_tick(force=True)
