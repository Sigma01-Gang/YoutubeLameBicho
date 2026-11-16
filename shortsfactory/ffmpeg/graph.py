"""Construcción de filtergraphs de FFmpeg y escapado de argumentos (funciones puras)."""
from __future__ import annotations


class Graph:
    """Acumula cadenas de filtros con etiquetas para -filter_complex."""

    def __init__(self) -> None:
        self.parts: list[str] = []
        self._n = 0

    def new(self, prefix: str = "x") -> str:
        self._n += 1
        return f"{prefix}{self._n}"

    def chain(self, inputs: list[str], filters: list[str] | str, out: str | list[str] | None = None, prefix: str = "x"):
        """Añade una cadena lineal. Devuelve la etiqueta (o etiquetas) de salida."""
        if isinstance(filters, str):
            filters = [filters]
        filters = [f for f in filters if f]
        if not filters:
            if len(inputs) == 1 and out is None:
                return inputs[0]
            filters = ["null"]
        if out is None:
            out = self.new(prefix)
        outs = out if isinstance(out, list) else [out]
        ins = "".join(f"[{i}]" for i in inputs)
        self.parts.append(ins + ",".join(filters) + "".join(f"[{o}]" for o in outs))
        return out

    def source(self, filt: str, prefix: str = "src") -> str:
        out = self.new(prefix)
        self.parts.append(f"{filt}[{out}]")
        return out

    def render(self) -> str:
        return ";".join(self.parts)


def escape_path(path: str) -> str:
    """Escapa una ruta para usarla como valor de opción dentro de un filtergraph, entre comillas simples."""
    p = str(path).replace("\\", "/")
    p = p.replace(":", "\\:").replace("'", "\\'")
    return f"'{p}'"


def escape_text(text: str) -> str:
    """Escapa texto para drawtext (con expansion=none)."""
    t = str(text).replace("\\", "\\\\").replace(":", "\\:").replace("'", "’").replace("%", "\\%")
    t = t.replace("\n", " ")
    return f"'{t}'"


def ff_color(color: str) -> str:
    """'#RRGGBB' -> '0xRRGGBB'; nombres de color se devuelven tal cual."""
    c = str(color).strip()
    if c.startswith("#") and len(c) in (7, 9):
        return "0x" + c[1:]
    return c


def num(x: float) -> str:
    """Formato numérico compacto y estable para filtros."""
    s = f"{x:.4f}".rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"
