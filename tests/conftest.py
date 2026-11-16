import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

_TMP = tempfile.mkdtemp(prefix="sf_test_data_")
os.environ["SHORTSFACTORY_DATA"] = _TMP
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="FFmpeg no está instalado")


@pytest.fixture
def db(tmp_path):
    from shortsfactory.persistence.db import Database
    d = Database(tmp_path / "test.db")
    yield d
    d.close()


@pytest.fixture
def ctx(tmp_path):
    from shortsfactory.app_context import AppContext
    c = AppContext(tmp_path / "ctx.db")
    yield c
    c.db.close()


@pytest.fixture(scope="session")
def sample_video(tmp_path_factory):
    """Vídeo de prueba de 70 s, 640x360, con tono y un silencio entre 28 y 32 s."""
    if not HAS_FFMPEG:
        pytest.skip("FFmpeg no está instalado")
    d = tmp_path_factory.mktemp("media")
    out = d / "sample.mp4"
    subprocess.run([
        "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=640x360:r=25:d=70",
        "-f", "lavfi", "-i", "sine=f=440:d=70",
        "-filter_complex", "[1:a]volume='if(between(t,28,32),0,1)':eval=frame[a]",
        "-map", "0:v", "-map", "[a]", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(out),
    ], check=True)
    return out
