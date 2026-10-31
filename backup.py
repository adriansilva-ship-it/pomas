"""Copia de seguridad: base de datos + archivos subidos en un .zip.   Uso: python backup.py"""
import shutil, sqlite3, sys, tempfile, zipfile
from datetime import datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / "server"))
import config

dest = Path(f"respaldo_{datetime.now():%Y%m%d_%H%M}.zip")
with tempfile.TemporaryDirectory() as t:
    snap = Path(t) / "politecnico.db"
    src = sqlite3.connect(config.DB_PATH); dst = sqlite3.connect(snap)
    src.backup(dst); dst.close(); src.close()  # copia consistente aunque el sistema esté en uso
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(snap, "politecnico.db")
        for f in config.UPLOADS.glob("*"):
            z.write(f, f"uploads/{f.name}")
print("Respaldo creado:", dest)
