"""Configuración del servidor. Todo se controla con variables de entorno o con el archivo .env"""
import os
from pathlib import Path

BASE = Path(__file__).resolve().parent
ROOT = BASE.parent
WEB = ROOT / "web"


def _load_env():
    f = ROOT / ".env"
    if not f.exists():
        return
    for line in f.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()


def _list(name):
    return [x.strip().lower() for x in os.environ.get(name, "").split(",") if x.strip()]


DATA_DIR = Path(os.environ.get("DATA_DIR", BASE / "data"))
UPLOADS = DATA_DIR / "uploads"
DB_PATH = DATA_DIR / "politecnico.db"

# Client ID de Google (Google Cloud Console -> APIs y servicios -> Credenciales)
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
# Correos que entran como administrador automáticamente (el primer admin)
ADMIN_EMAILS = _list("ADMIN_EMAILS")
# Opcional: limitar a ciertos dominios de correo (vacío = cualquiera, el admin decide)
ALLOWED_DOMAINS = _list("ALLOWED_DOMAINS")
# SOLO para pruebas locales: simula el inicio de sesión con Google
DEV_LOGIN = os.environ.get("DEV_LOGIN", "0") == "1"
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "0") == "1"
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "25"))
PORT = int(os.environ.get("PORT", "5000"))
SCHOOL_NAME = "Politécnico Manuel Acevedo Serrano"
