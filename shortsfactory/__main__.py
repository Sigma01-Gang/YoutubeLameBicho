"""Punto de entrada: `python -m shortsfactory` (interfaz) o `python -m shortsfactory --cli ...`."""
from __future__ import annotations

import argparse
import sys


def run_gui() -> int:
    from PySide6.QtWidgets import QApplication

    from .app_context import AppContext, setup_logging
    from .ui.main_window import MainWindow
    from .ui.wizard import FirstRunWizard

    app = QApplication(sys.argv)
    app.setApplicationName("ShortsFactory")
    ctx = AppContext()
    setup_logging(ctx.db)
    if not ctx.db.get_setting("first_run_done", False):
        FirstRunWizard(ctx).exec()
    win = MainWindow(ctx)
    win.show()
    return app.exec()


def run_cli(argv: list[str]) -> int:
    from .app_context import AppContext, setup_logging

    p = argparse.ArgumentParser(prog="shortsfactory --cli")
    sub = p.add_subparsers(dest="cmd", required=True)
    oc = sub.add_parser("one-click", help="Procesar un vídeo y encolarlo con los ajustes por defecto")
    oc.add_argument("video")
    oc.add_argument("--canal")
    oc.add_argument("--modo", choices=["parts", "shorts"], default="parts")
    sub.add_parser("simular", help="Mostrar qué se publicaría y cuándo")
    sub.add_parser("tick", help="Ejecutar una iteración de la cola")
    ls = sub.add_parser("planilla", help="Cargar una planilla")
    ls.add_argument("ruta")
    ls.add_argument("--canal")
    args = p.parse_args(argv)
    ctx = AppContext()
    setup_logging(ctx.db)
    if args.cmd == "one-click":
        from .pipeline import one_click
        ch = ctx.channels.by_name(args.canal) if args.canal else None
        res = one_click(ctx, args.video, ch.id if ch else None, args.modo,
                        lambda f: print(f"\r{f * 100:5.1f}%", end="", flush=True))
        print("\n", res)
    elif args.cmd == "simular":
        for it, t in ctx.queue.plan():
            print(f"{t:%Y-%m-%d %H:%M}" if t else "EN PAUSA", f"[{it['canal']}]", it["title"], f"({it['status']})")
    elif args.cmd == "tick":
        from .upload.runner import QueueRunner
        print(QueueRunner(ctx.db).tick())
    elif args.cmd == "planilla":
        rep = ctx.sheets.load_source(args.ruta, args.canal)
        print(f"nuevas={rep.inserted} actualizadas={rep.updated} errores={rep.error_count}")
        for r in rep.results:
            if r.errors:
                print(f"  fila {r.index}: {'; '.join(r.errors)}")
    return 0


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--cli":
        return run_cli(sys.argv[2:])
    return run_gui()


if __name__ == "__main__":
    sys.exit(main())
