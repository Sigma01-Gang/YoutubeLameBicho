#!/usr/bin/env bash
# Instalación de ShortsFactory en Linux (Debian/Ubuntu y similares)
set -euo pipefail
cd "$(dirname "$0")/.."
PY=${PYTHON:-python3}
$PY -c 'import sys; assert sys.version_info >= (3, 11), "Se necesita Python 3.11+"'
if ! command -v ffmpeg >/dev/null; then
  echo "[AVISO] FFmpeg no está instalado. En Debian/Ubuntu: sudo apt install ffmpeg"
fi
$PY -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
python -m playwright install chromium
# Dependencias del sistema de Chromium/Qt (pueden requerir sudo):
#   python -m playwright install-deps chromium
#   sudo apt install libegl1 libxkbcommon0 libxcb-cursor0
echo "Instalación completada. Para iniciar: scripts/run_linux.sh"
