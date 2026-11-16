#!/usr/bin/env bash
# Ejecuta toda la suite de pruebas con un solo comando
cd "$(dirname "$0")/.."
[ -f .venv/bin/activate ] && source .venv/bin/activate
exec python -m pytest "$@"
