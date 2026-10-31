#!/usr/bin/env bash
# Inicia la plataforma (Linux / macOS).  Uso: ./run.sh
cd "$(dirname "$0")"
[ -f .env ] || { echo "Falta el archivo .env (copia .env.example y complétalo)"; exit 1; }
pip install -q -r requirements.txt
cd server
exec gunicorn -w 3 -b 0.0.0.0:${PORT:-5000} --timeout 120 app:app
