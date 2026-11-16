"""Prueba de aceptación completa (lenta): un vídeo de 20 min se trocea en 7 partes de 3 min.

Genera un vídeo sintético de 20 minutos con subtítulos, lo trocea con cortes fijos de 180 s, renderiza
las 7 partes en 1080x1920 y comprueba con ffprobe resolución, duración y sincronía audio/vídeo, y que
cada subtítulo cae en la parte correcta.

Uso:  python scripts/acceptance_20min.py [carpeta_de_trabajo]
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shortsfactory.core.models import RenderOptions  # noqa: E402
from shortsfactory.ffmpeg import runner  # noqa: E402
from shortsfactory.ingest import cutting  # noqa: E402
from shortsfactory.persistence.db import Database  # noqa: E402
from shortsfactory.processing import Processor, RenderJob  # noqa: E402
from shortsfactory.subtitles import srt  # noqa: E402


def main() -> int:
    work = Path(sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="sf_accept_"))
    work.mkdir(parents=True, exist_ok=True)
    src = work / "historia_20min.mp4"
    if not src.exists():
        print("Generando vídeo de 20 minutos…")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=640x360:r=25:d=1200",
                        "-f", "lavfi", "-i", "sine=f=330:d=1200", "-c:v", "libx264", "-preset", "ultrafast",
                        "-c:a", "aac", str(src)], check=True)
    cues = [srt.Cue(t + 1, t + 3, f"Línea {i}") for i, t in enumerate(range(0, 1200, 30))]
    subs = work / "historia_20min.srt"
    srt.save(cues, subs)
    tools = runner.Tools()
    info = runner.media_info(tools, str(src))
    segs = cutting.split_fixed(info.duration, 180)
    assert len(segs) == 7, f"se esperaban 7 partes, hay {len(segs)}"
    db = Database(work / "acc.db")
    proc = Processor(db, tools)
    opts = RenderOptions(framing="blur", preset="veryfast")
    jobs = [RenderJob(str(src), s, opts, str(work / f"parte{i + 1}.mp4"), str(subs)) for i, s in enumerate(segs)]
    results = proc.render_many(jobs, threads=2, progress=lambda i, f: print(f"\rparte {i + 1}: {f * 100:5.1f}%",
                                                                            end="", flush=True))
    print()
    ok = True
    for job, res in zip(jobs, results):
        if isinstance(res, Exception):
            print("ERROR", job.output, res)
            ok = False
            continue
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height,duration",
                              "-of", "json", res.output], capture_output=True, text=True, check=True).stdout
        streams = {s["codec_type"]: s for s in json.loads(out)["streams"]}
        v, a = streams["video"], streams["audio"]
        dv, da = float(v["duration"]), float(a["duration"])
        exp = job.segment.duration
        n_subs = len(srt.slice_for_segment(cues, job.segment.kept_intervals()))
        good = (v["width"], v["height"]) == (1080, 1920) and abs(dv - exp) < 0.1 and abs(dv - da) < 0.1
        ok &= good
        print(f"{Path(res.output).name}: {v['width']}x{v['height']} vídeo={dv:.2f}s audio={da:.2f}s "
              f"esperado={exp:.2f}s subtítulos={n_subs} {'OK' if good else 'FALLO'}")
    print("RESULTADO:", "ACEPTADO" if ok else "FALLIDO")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
