"""Ventana principal: pestañas, tema, búsqueda global, notificaciones y temporizador de la cola."""
from __future__ import annotations

import logging

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QKeySequence, QPalette
from PySide6.QtWidgets import (QApplication, QDialog, QDockWidget, QLineEdit, QListWidget, QListWidgetItem,
                               QMainWindow, QStyle, QSystemTrayIcon, QTabWidget, QVBoxLayout)

from ..upload.runner import QueueRunner
from .tabs.edit_tab import EditTab
from .tabs.log_tab import LogTab
from .tabs.queue_tab import QueueTab
from .tabs.review_tab import ReviewTab
from .tabs.settings_tab import SettingsTab
from .tabs.sheets_tab import SheetsTab
from .tabs.videos_tab import VideosTab
from .workers import TaskPanel, run_bg


class LogBridge(QObject):
    line = Signal(str)


class QtLogHandler(logging.Handler):
    def __init__(self, bridge: LogBridge):
        super().__init__(level=logging.INFO)
        self.bridge = bridge

    def emit(self, record):
        try:
            self.bridge.line.emit(f"{self.formatTime(record)}\t{record.levelname}\t{record.name}\t{record.getMessage()}")
        except RuntimeError:
            pass


class SearchDialog(QDialog):
    """Búsqueda global de vídeos, filas de planilla, elementos de la cola y registros."""

    def __init__(self, main):
        super().__init__(main)
        self.main = main
        self.setWindowTitle("Búsqueda global")
        self.resize(700, 450)
        lay = QVBoxLayout(self)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText("Buscar en vídeos, planillas, cola y registros…")
        self.edit.textChanged.connect(self.search)
        self.results = QListWidget()
        self.results.itemActivated.connect(self.go)
        lay.addWidget(self.edit)
        lay.addWidget(self.results)

    def search(self, text: str):
        self.results.clear()
        t = text.strip().lower()
        if len(t) < 2:
            return
        db = self.main.ctx.db
        q = f"%{t}%"
        for p in db.get_setting("sources", []) or []:
            if t in p.lower():
                self._add(f"🎬 Vídeo: {p}", "videos")
        for r in db.query("SELECT archivo_video, titulo FROM sheet_rows WHERE lower(titulo) LIKE ? OR "
                          "lower(archivo_video) LIKE ? LIMIT 50", (q, q)):
            self._add(f"📄 Planilla: {r['titulo']} ({r['archivo_video']})", "sheets")
        for r in db.query("SELECT title, status FROM queue_items WHERE lower(title) LIKE ? OR lower(video_path) LIKE ? "
                          "LIMIT 50", (q, q)):
            self._add(f"⏱ Cola: {r['title']} [{r['status']}]", "queue")
        for r in db.query("SELECT at, message FROM app_log WHERE lower(message) LIKE ? ORDER BY id DESC LIMIT 30", (q,)):
            self._add(f"📝 Registro {r['at']}: {r['message'][:120]}", "log")

    def _add(self, text, tab):
        it = QListWidgetItem(text)
        it.setData(Qt.UserRole, tab)
        self.results.addItem(it)

    def go(self, item):
        tabs = {"videos": self.main.videos_tab, "sheets": self.main.sheets_tab, "queue": self.main.queue_tab,
                "log": self.main.log_tab}
        self.main.tabs.setCurrentWidget(tabs[item.data(Qt.UserRole)])
        self.close()


class MainWindow(QMainWindow):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self.setWindowTitle("ShortsFactory — historias por partes y Shorts")
        self.resize(1500, 950)
        self.tasks = TaskPanel()
        dock = QDockWidget("Tareas en curso", self)
        dock.setWidget(self.tasks)
        dock.setAllowedAreas(Qt.BottomDockWidgetArea | Qt.TopDockWidgetArea)
        self.addDockWidget(Qt.BottomDockWidgetArea, dock)

        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)
        self.videos_tab = VideosTab(ctx, self.tasks, self)
        self.edit_tab = EditTab(ctx, self.tasks, self)
        self.sheets_tab = SheetsTab(ctx, self.tasks, self)
        self.queue_tab = QueueTab(ctx, self.tasks, self)
        self.review_tab = ReviewTab(ctx, self.tasks, self)
        self.log_tab = LogTab(ctx, self.tasks, self)
        self.settings_tab = SettingsTab(ctx, self.tasks, self)
        for w, name in ((self.videos_tab, "Vídeos"), (self.edit_tab, "Edición"), (self.sheets_tab, "Planillas"),
                        (self.queue_tab, "Cola"), (self.review_tab, "Revisión"), (self.log_tab, "Registro"),
                        (self.settings_tab, "Ajustes")):
            self.tabs.addTab(w, name)
        self.tabs.currentChanged.connect(lambda _: self.refresh_current())

        self.bridge = LogBridge()
        self.bridge.line.connect(self.log_tab.append_live)
        self._qt_handler = QtLogHandler(self.bridge)
        logging.getLogger().addHandler(self._qt_handler)

        menu = self.menuBar().addMenu("&Archivo")
        for text, key, fn in (("Búsqueda global", (ctx.db.get_setting("shortcuts", {}) or {}).get("search", "Ctrl+F"),
                               self.open_search),
                              ("Copia de seguridad", None, lambda: self.settings_tab.backup()),
                              ("Salir", "Ctrl+Q", self.close)):
            a = QAction(text, self)
            if key:
                a.setShortcut(QKeySequence(key))
            a.triggered.connect(fn)
            menu.addAction(a)

        self.tray = None
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = QSystemTrayIcon(self.style().standardIcon(QStyle.SP_MediaPlay), self)
            self.tray.setToolTip("ShortsFactory")
            self.tray.show()

        self.queue_running = False
        self._ticking = False
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.queue_tick)
        self.timer.start(60_000)
        self.backup_timer = QTimer(self)
        self.backup_timer.timeout.connect(self.auto_backup)
        self.backup_timer.start(6 * 3600 * 1000)
        self.apply_theme()
        if ctx.recovered:
            self.statusBar().showMessage(f"{ctx.recovered} subida(s) interrumpida(s) devueltas a pendientes.", 15000)

    # --- tema ----------------------------------------------------------------
    def apply_theme(self):
        app = QApplication.instance()
        app.setStyle("Fusion")
        if self.ctx.db.get_setting("theme", "dark") == "dark":
            p = QPalette()
            for role, c in ((QPalette.Window, "#2b2b2b"), (QPalette.WindowText, "#e0e0e0"),
                            (QPalette.Base, "#1e1e1e"), (QPalette.AlternateBase, "#2b2b2b"),
                            (QPalette.Text, "#e0e0e0"), (QPalette.Button, "#3a3a3a"),
                            (QPalette.ButtonText, "#e0e0e0"), (QPalette.Highlight, "#0288d1"),
                            (QPalette.HighlightedText, "#ffffff"), (QPalette.ToolTipBase, "#3a3a3a"),
                            (QPalette.ToolTipText, "#e0e0e0"), (QPalette.PlaceholderText, "#888888")):
                p.setColor(role, QColor(c))
            app.setPalette(p)
        else:
            app.setPalette(app.style().standardPalette())

    # --- refresco --------------------------------------------------------------
    def refresh_current(self):
        w = self.tabs.currentWidget()
        if hasattr(w, "refresh"):
            w.refresh()

    def refresh_all(self):
        for w in (self.videos_tab, self.sheets_tab, self.queue_tab, self.review_tab, self.log_tab):
            w.refresh()
        self.edit_tab.refresh_sources()

    def open_search(self):
        SearchDialog(self).exec()

    def notify(self, title: str, msg: str):
        logging.getLogger("notificacion").info("%s: %s", title, msg)
        if self.tray:
            self.tray.showMessage(title, msg, QSystemTrayIcon.Information, 8000)
        self.statusBar().showMessage(f"{title}: {msg}", 15000)

    # --- cola ----------------------------------------------------------------
    def set_queue_running(self, on: bool):
        self.queue_running = on
        if on:
            self.queue_tick()

    def queue_tick(self, force: bool = False):
        if self._ticking or not (self.queue_running or force):
            return
        self._ticking = True
        runner = QueueRunner(self.ctx.db, notify=lambda t, m: self.bridge.line.emit(f"\tINFO\tcola\t{t}: {m}"))
        notes: list[tuple[str, str]] = []
        runner.notify = lambda t, m: notes.append((t, m))

        def done(res):
            self._ticking = False
            for t, m in notes:
                self.notify(t, m)
            if res.outcome != "nada" or res.message:
                self.statusBar().showMessage(f"Cola: {res.outcome} {res.message}", 15000)
            self.queue_tab.refresh()

        def fail(err):
            self._ticking = False
            self.notify("Error en la cola", err.splitlines()[0])

        run_bg(runner.tick, on_done=done, on_error=fail)

    def auto_backup(self):
        try:
            self.ctx.backup()
        except Exception as exc:  # noqa: BLE001
            logging.getLogger(__name__).warning("Copia automática fallida: %s", exc)

    def closeEvent(self, e):  # noqa: N802
        self.edit_tab.save_project()
        try:
            self.ctx.backup()
        except Exception:  # noqa: BLE001
            pass
        logging.getLogger().removeHandler(self._qt_handler)
        super().closeEvent(e)
