"""Tareas en segundo plano con progreso y cancelación para la interfaz."""
from __future__ import annotations

import threading
import traceback
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout, QWidget)


class WorkerSignals(QObject):
    progress = Signal(float)
    message = Signal(str)
    finished = Signal(object)
    error = Signal(str)


class Worker(QRunnable):
    """Ejecuta fn(progress, cancel, *args) en el pool. progress(fracción 0..1)."""

    def __init__(self, fn: Callable[..., Any], *args, **kwargs):
        super().__init__()
        self.fn, self.args, self.kwargs = fn, args, kwargs
        self.signals = WorkerSignals()
        self.cancel = threading.Event()
        self.setAutoDelete(True)

    @Slot()
    def run(self) -> None:
        try:
            res = self.fn(self.signals.progress.emit, self.cancel, *self.args, **self.kwargs)
        except Exception as exc:  # noqa: BLE001
            self.signals.error.emit(f"{exc}\n\n{traceback.format_exc(limit=4)}")
        else:
            self.signals.finished.emit(res)


class TaskRow(QWidget):
    def __init__(self, title: str, worker: Worker, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 2, 4, 2)
        self.label = QLabel(title)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.btn = QPushButton("Cancelar")
        self.btn.clicked.connect(lambda: (worker.cancel.set(), self.label.setText(f"{title} — cancelando…")))
        lay.addWidget(self.label, 2)
        lay.addWidget(self.bar, 3)
        lay.addWidget(self.btn)
        worker.signals.progress.connect(lambda f: self.bar.setValue(int(f * 1000)))


class TaskPanel(QWidget):
    """Lista de tareas largas activas, cada una con barra de progreso y botón de cancelar."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pool = QThreadPool.globalInstance()
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.empty = QLabel("Sin tareas en curso")
        self.lay.addWidget(self.empty)
        self.lay.addStretch()

    def start(self, title: str, fn: Callable, *args, on_done: Callable | None = None,
              on_error: Callable | None = None, **kwargs) -> Worker:
        w = Worker(fn, *args, **kwargs)
        row = TaskRow(title, w)
        self.lay.insertWidget(0, row)
        self.empty.hide()

        def cleanup():
            row.setParent(None)
            row.deleteLater()
            if self.lay.count() <= 2:
                self.empty.show()

        w.signals.finished.connect(lambda r: (cleanup(), on_done and on_done(r)))
        w.signals.error.connect(lambda e: (cleanup(), on_error and on_error(e)))
        self.pool.start(w)
        return w


def run_bg(fn: Callable, *args, on_done: Callable | None = None, on_error: Callable | None = None) -> Worker:
    """Tarea corta sin fila en el panel."""
    w = Worker(lambda p, c: fn(*args))
    if on_done:
        w.signals.finished.connect(on_done)
    if on_error:
        w.signals.error.connect(on_error)
    QThreadPool.globalInstance().start(w)
    return w
