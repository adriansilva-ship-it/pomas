"""
Plataforma educativa del Politécnico Manuel Acevedo Serrano (Cutupú, La Vega)
API en Flask + SQLite. El frontend (web/) es HTML, CSS y JavaScript puros.
"""
import csv
import io
import json
import os
import re
import secrets
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from functools import wraps
from urllib.parse import urlparse

from flask import Flask, Response, g, jsonify, request, send_file, send_from_directory, session
from werkzeug.exceptions import HTTPException

import config
import db as dbm

# ----------------------------------------------------------------------------
# Utilidades
# ----------------------------------------------------------------------------


class ApiError(Exception):
    def __init__(self, msg, code=400, **extra):
        super().__init__(msg)
        self.msg, self.code, self.extra = msg, code, extra


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def clean(v, n=200):
    if v is None:
        return ""
    return re.sub(r"\s+", " ", str(v)).strip()[:n]


def text(v, n=5000):
    if v is None:
        return ""
    return str(v).replace("\r\n", "\n").strip()[:n]


def ival(v, default, lo=1, hi=10**9):
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return default


def num(v, lo, hi, field="valor"):
    if v is None or v == "":
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise ApiError(f"El {field} debe ser un número.")
    if x != x or x < lo or x > hi:
        raise ApiError(f"El {field} debe estar entre {lo:g} y {hi:g}.")
    return round(x, 2)


def parse_dt(v):
    if not v:
        return None
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        raise ApiError("Fecha no válida.")
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc).isoformat(timespec="seconds")


def body():
    if request.is_json:
        try:
            data = request.get_json(silent=True)
        except RecursionError:  # JSON anidado a propósito para tumbar el servidor
            raise ApiError("Datos no válidos.")
        return data if isinstance(data, dict) else {}
    return request.form.to_dict()


def like(q):
    q = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{q}%"


def rows(cur):
    return [dict(r) for r in cur.fetchall()]


def one(cur):
    r = cur.fetchone()
    return dict(r) if r else None


def default_school_year():
    d = datetime.now()
    y = d.year if d.month >= 8 else d.year - 1
    return f"{y}-{y + 1}"


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
GRADES = ["1ro", "2do", "3ro", "4to", "5to", "6to"]
COLORS = re.compile(r"^#[0-9a-fA-F]{6}$")
ALLOWED_EXT = {
    "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "txt", "csv", "md", "odt", "ods", "odp",
    "png", "jpg", "jpeg", "gif", "webp", "zip", "rar", "7z", "mp4", "mp3", "wav",
    "py", "java", "cpp", "c", "js", "json", "sql", "ino",
}
INLINE_OK = {"pdf", "png", "jpg", "jpeg", "gif", "webp", "mp4", "mp3", "wav"}

USER_COLS = ("id,email,full_name,picture,role,status,id_number,grade,section,specialty,phone,"
             "created_at,approved_at,last_login")


def public_user(u):
    keys = ["id", "email", "full_name", "picture", "role", "status", "id_number", "grade",
            "section", "specialty", "phone", "created_at"]
    return {k: u[k] for k in keys}


# ----------------------------------------------------------------------------
# App
# ----------------------------------------------------------------------------


def _secret():
    key = os.environ.get("SECRET_KEY", "").strip()
    if key:
        return key
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    f = config.DATA_DIR / "secret.key"
    if not f.exists():
        f.write_text(secrets.token_hex(32))
        try:
            os.chmod(f, 0o600)
        except OSError:
            pass
    return f.read_text().strip()


app = Flask(__name__, static_folder=None)
app.config.update(
    SECRET_KEY=_secret(),
    MAX_CONTENT_LENGTH=config.MAX_UPLOAD_MB * 1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=config.COOKIE_SECURE,
    PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
)
app.json.ensure_ascii = False
app.teardown_appcontext(dbm.close)
config.UPLOADS.mkdir(parents=True, exist_ok=True)
dbm.init()


@app.errorhandler(ApiError)
def _api_error(e):
    return jsonify({"error": e.msg, **e.extra}), e.code


@app.errorhandler(HTTPException)
def _http_error(e):
    msgs = {404: "No encontrado.", 405: "Método no permitido.",
            413: f"El archivo supera el límite de {config.MAX_UPLOAD_MB} MB."}
    return jsonify({"error": msgs.get(e.code, e.description)}), e.code


@app.errorhandler(sqlite3.IntegrityError)
def _integrity(e):
    # Dos peticiones simultáneas intentaron crear lo mismo (doble clic, red lenta): la segunda pierde sin romper nada.
    app.logger.info("Conflicto de integridad: %s", e)
    return jsonify({"error": "Esta acción ya se había realizado. Actualiza la página para ver el estado actual."}), 409


@app.errorhandler(OverflowError)
def _overflow(e):
    return jsonify({"error": "No encontrado."}), 404


@app.errorhandler(Exception)
def _unexpected(e):
    app.logger.exception("Error inesperado")
    return jsonify({"error": "Ocurrió un error en el servidor. Intenta de nuevo."}), 500


@app.before_request
def _guard():
    g.user = None
    if not request.path.startswith("/api/"):
        return
    if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get("X-Requested-With") != "fetch":
        raise ApiError("Solicitud no válida.", 403)
    uid = session.get("uid")
    if uid:
        u = dbm.get().execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        if u and u["status"] == "approved":
            g.user = u
        else:
            session.pop("uid", None)


@app.after_request
def _after(resp):
    if "db" in g:
        if resp.status_code < 400:
            g.db.commit()
        else:
            g.db.rollback()
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    if request.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    else:
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' https://accounts.google.com/gsi/client; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://accounts.google.com/gsi/style; "
            "font-src https://fonts.gstatic.com; "
            "img-src 'self' data: https:; "
            "frame-src https://accounts.google.com/gsi/ https://www.youtube-nocookie.com https://player.vimeo.com; "
            "connect-src 'self' https://accounts.google.com/gsi/; "
            "media-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'"
        )
        resp.headers["Cache-Control"] = "no-cache"
    return resp


def need(*roles):
    def deco(fn):
        @wraps(fn)
        def wrapper(*a, **k):
            if not g.user:
                raise ApiError("Debes iniciar sesión.", 401)
            if roles and g.user["role"] not in roles:
                raise ApiError("No tienes permiso para esta acción.", 403)
            return fn(*a, **k)
        return wrapper
    return deco


def audit(action, detail=""):
    dbm.get().execute("INSERT INTO audit_log(actor_id,action,detail,created_at) VALUES(?,?,?,?)",
                      (g.user["id"] if g.user else None, action, detail[:400], now()))


def notify(user_ids, msg, link=None):
    ids = {int(i) for i in user_ids if i}
    if ids:
        dbm.get().executemany(
            "INSERT INTO notifications(user_id,text,link,created_at) VALUES(?,?,?,?)",
            [(i, msg[:300], link, now()) for i in ids])


# ----------------------------------------------------------------------------
# Archivos
# ----------------------------------------------------------------------------


def save_upload(field="file"):
    f = request.files.get(field)
    if not f or not f.filename:
        return None, None
    orig = os.path.basename(f.filename.replace("\\", "/"))[:120]
    ext = orig.rsplit(".", 1)[-1].lower() if "." in orig else ""
    if ext not in ALLOWED_EXT:
        raise ApiError("Tipo de archivo no permitido. Usa PDF, Office, imágenes, ZIP, audio o video.")
    stored = f"{uuid.uuid4().hex}.{ext}"
    f.save(config.UPLOADS / stored)
    return orig, stored


def rm_file(stored):
    if stored:
        try:
            (config.UPLOADS / stored).unlink()
        except OSError:
            pass


def send_stored(stored, orig):
    p = config.UPLOADS / (stored or "")
    if not stored or not p.is_file():
        raise ApiError("Archivo no encontrado.", 404)
    ext = stored.rsplit(".", 1)[-1]
    attach = ext not in INLINE_OK or request.args.get("download") == "1"
    return send_file(p, as_attachment=attach, download_name=orig or stored)


# ----------------------------------------------------------------------------
# Acceso a cursos
# ----------------------------------------------------------------------------

COURSE_SQL = """
SELECT c.*, u.full_name AS teacher_name,
  (SELECT COUNT(*) FROM enrollments e WHERE e.course_id=c.id) AS student_count,
  (SELECT COUNT(*) FROM lessons l WHERE l.course_id=c.id) AS lesson_count,
  (SELECT COUNT(*) FROM assignments a WHERE a.course_id=c.id) AS assignment_count
FROM courses c JOIN users u ON u.id=c.teacher_id
"""


def get_course(cid, manage=False):
    d = dbm.get()
    c = one(d.execute(COURSE_SQL + " WHERE c.id=?", (cid,)))
    if not c:
        raise ApiError("Curso no encontrado.", 404)
    u = g.user
    is_mgr = u["role"] == "admin" or c["teacher_id"] == u["id"]
    if manage and not is_mgr:
        raise ApiError("Solo el docente del curso puede hacer esto.", 403)
    if not is_mgr:
        enrolled = d.execute("SELECT 1 FROM enrollments WHERE course_id=? AND student_id=?",
                             (cid, u["id"])).fetchone()
        if u["role"] != "student" or not enrolled:
            raise ApiError("No tienes acceso a este curso.", 403)
    c["can_manage"] = is_mgr
    return c


def course_files(cid):
    d = dbm.get()
    files = []
    files += [r[0] for r in d.execute("SELECT file_path FROM lessons WHERE course_id=? AND file_path IS NOT NULL", (cid,))]
    files += [r[0] for r in d.execute("SELECT file_path FROM assignments WHERE course_id=? AND file_path IS NOT NULL", (cid,))]
    files += [r[0] for r in d.execute(
        "SELECT s.file_path FROM submissions s JOIN assignments a ON a.id=s.assignment_id "
        "WHERE a.course_id=? AND s.file_path IS NOT NULL", (cid,))]
    return files


# ----------------------------------------------------------------------------
# Sitio estático
# ----------------------------------------------------------------------------


@app.get("/")
def index():
    return send_from_directory(config.WEB, "index.html")


@app.get("/<path:p>")
def static_files(p):
    if p.startswith("api/"):
        raise ApiError("No encontrado.", 404)
    return send_from_directory(config.WEB, p)


# ----------------------------------------------------------------------------
# Autenticación (solo Google)
# ----------------------------------------------------------------------------


@app.get("/api/config")
def api_config():
    return jsonify(googleClientId=config.GOOGLE_CLIENT_ID, devLogin=config.DEV_LOGIN,
                   school=config.SCHOOL_NAME, grades=GRADES)


def verify_google(token):
    if not config.GOOGLE_CLIENT_ID:
        raise ApiError("El servidor no tiene configurado GOOGLE_CLIENT_ID.", 503)
    try:
        from google.auth.transport import requests as greq
        from google.oauth2 import id_token
    except ImportError:
        raise ApiError("Falta instalar google-auth en el servidor (pip install -r requirements.txt).", 500)
    try:
        info = id_token.verify_oauth2_token(token, greq.Request(), config.GOOGLE_CLIENT_ID)
    except ValueError:
        raise ApiError("No se pudo verificar tu cuenta de Google. Intenta de nuevo.", 401)
    if not info.get("email_verified"):
        raise ApiError("Tu correo de Google no está verificado.", 401)
    email = info["email"]
    return email, info.get("name") or email.split("@")[0], info.get("picture")


def finish_identity(email, name, picture):
    """Ya sabemos que el correo es real. Decide qué sigue: entrar, registrarse o esperar."""
    email = email.strip().lower()
    if config.ALLOWED_DOMAINS and email.split("@")[-1] not in config.ALLOWED_DOMAINS \
            and email not in config.ADMIN_EMAILS:
        raise ApiError("Esta plataforma es solo para cuentas autorizadas por el centro.", 403)
    d = dbm.get()
    u = one(d.execute("SELECT * FROM users WHERE email=?", (email,)))
    if email in config.ADMIN_EMAILS:
        if not u:
            d.execute("INSERT INTO users(email,full_name,picture,role,status,created_at,approved_at) "
                      "VALUES(?,?,?,'admin','approved',?,?)", (email, clean(name, 120), picture, now(), now()))
        elif u["role"] != "admin" or u["status"] != "approved":
            d.execute("UPDATE users SET role='admin', status='approved', approved_at=? WHERE id=?", (now(), u["id"]))
        u = one(d.execute("SELECT * FROM users WHERE email=?", (email,)))
    session.clear()
    if u:
        if u["status"] == "approved":
            session.permanent = True
            session["uid"] = u["id"]
            d.execute("UPDATE users SET last_login=?, picture=COALESCE(?,picture) WHERE id=?", (now(), picture, u["id"]))
            return jsonify(state="approved", user=public_user(u))
        return jsonify(state=u["status"], user={"full_name": u["full_name"], "email": u["email"],
                                                 "role": u["role"], "reason": u["reject_reason"]})
    session["identity"] = {"email": email, "name": clean(name, 120), "picture": picture}
    return jsonify(state="register", identity=session["identity"])


@app.post("/api/auth/google")
def auth_google():
    token = clean(body().get("credential"), 4000)
    if not token:
        raise ApiError("Falta la credencial de Google.")
    email, name, picture = verify_google(token)
    return finish_identity(email, name, picture)


@app.get("/api/dev/accounts")
def dev_accounts():
    if not config.DEV_LOGIN:
        raise ApiError("No encontrado.", 404)
    d = dbm.get()
    out = {}
    for role in ("admin", "teacher", "student"):
        out[role] = rows(d.execute(
            "SELECT email,full_name FROM users WHERE role=? AND status='approved' ORDER BY id LIMIT 4", (role,)))
    out["pending"] = rows(d.execute("SELECT email,full_name FROM users WHERE status='pending' ORDER BY id LIMIT 2"))
    return jsonify(out)


@app.post("/api/auth/dev")
def auth_dev():
    """Simula el inicio con Google. Solo existe si DEV_LOGIN=1 (pruebas locales)."""
    if not config.DEV_LOGIN:
        raise ApiError("No encontrado.", 404)
    data = body()
    email = clean(data.get("email"), 120).lower()
    if not EMAIL_RE.match(email):
        raise ApiError("Escribe un correo válido.")
    return finish_identity(email, clean(data.get("name"), 120) or email.split("@")[0].replace(".", " ").title(), None)


@app.post("/api/register")
def register():
    ident = session.get("identity")
    if not ident:
        raise ApiError("Primero inicia sesión con Google.", 401)
    data = body()
    role = data.get("role")
    if role not in ("student", "teacher"):
        raise ApiError("Elige si eres estudiante o docente.")
    full_name = clean(data.get("full_name"), 120) or ident["name"]
    if len(full_name) < 5 or " " not in full_name:
        raise ApiError("Escribe tu nombre completo (nombre y apellido).")
    id_number = clean(data.get("id_number"), 30)
    specialty = clean(data.get("specialty"), 80)
    phone = clean(data.get("phone"), 30)
    grade = section = None
    if role == "student":
        grade = clean(data.get("grade"), 10)
        section = clean(data.get("section"), 10).upper()
        if grade not in GRADES:
            raise ApiError("Elige tu grado.")
        if not section:
            raise ApiError("Escribe tu sección (A, B, C...).")
    d = dbm.get()
    if d.execute("SELECT 1 FROM users WHERE email=?", (ident["email"],)).fetchone():
        raise ApiError("Ya existe una solicitud con este correo.", 409)
    cur = d.execute(
        "INSERT INTO users(email,full_name,picture,role,status,id_number,grade,section,specialty,phone,created_at) "
        "VALUES(?,?,?,?, 'pending',?,?,?,?,?,?)",
        (ident["email"], full_name, ident.get("picture"), role, id_number, grade, section, specialty, phone, now()))
    admins = [r[0] for r in d.execute("SELECT id FROM users WHERE role='admin' AND status='approved'")]
    who = "docente" if role == "teacher" else "estudiante"
    notify(admins, f"Nueva solicitud de {who}: {full_name}", "#/requests")
    d.execute("INSERT INTO audit_log(actor_id,action,detail,created_at) VALUES(?,?,?,?)",
              (cur.lastrowid, "register", f"{full_name} ({role}) pidió acceso", now()))
    session.clear()
    return jsonify(state="pending", user={"full_name": full_name, "email": ident["email"], "role": role})


@app.get("/api/me")
def me():
    if not g.user:
        raise ApiError("No has iniciado sesión.", 401)
    return jsonify(user=public_user(g.user))


@app.post("/api/logout")
def logout():
    session.clear()
    return jsonify(ok=True)


@app.patch("/api/me")
@need()
def update_me():
    data = body()
    u = g.user
    phone = clean(data.get("phone"), 30)
    specialty = clean(data.get("specialty"), 80)
    dbm.get().execute("UPDATE users SET phone=?, specialty=? WHERE id=?", (phone, specialty, u["id"]))
    return jsonify(ok=True)


# ----------------------------------------------------------------------------
# Administración
# ----------------------------------------------------------------------------


@app.get("/api/admin/stats")
@need("admin")
def admin_stats():
    d = dbm.get()
    c = lambda sql, *a: d.execute(sql, a).fetchone()[0]
    return jsonify(
        pending=c("SELECT COUNT(*) FROM users WHERE status='pending'"),
        pending_students=c("SELECT COUNT(*) FROM users WHERE status='pending' AND role='student'"),
        pending_teachers=c("SELECT COUNT(*) FROM users WHERE status='pending' AND role='teacher'"),
        students=c("SELECT COUNT(*) FROM users WHERE role='student' AND status='approved'"),
        teachers=c("SELECT COUNT(*) FROM users WHERE role='teacher' AND status='approved'"),
        courses=c("SELECT COUNT(*) FROM courses"),
        enrollments=c("SELECT COUNT(*) FROM enrollments"),
        grades_submitted=c("SELECT COUNT(*) FROM courses WHERE grade_status='submitted'"),
        grades_open=c("SELECT COUNT(*) FROM courses WHERE grade_status='open'"),
        by_grade=rows(d.execute(
            "SELECT grade, COUNT(*) AS n FROM users WHERE role='student' AND status='approved' AND grade IS NOT NULL "
            "GROUP BY grade ORDER BY grade")),
    )


@app.get("/api/admin/users")
@need("admin")
def admin_users():
    a = request.args
    where, args = ["1=1"], []
    for key in ("status", "role", "grade", "section"):
        if a.get(key):
            where.append(f"{key}=?")
            args.append(a[key])
    if a.get("q"):
        where.append("(full_name LIKE ? ESCAPE '\\' OR email LIKE ? ESCAPE '\\' OR id_number LIKE ? ESCAPE '\\')")
        args += [like(a["q"])] * 3
    page, per = ival(a.get("page"), 1), ival(a.get("per"), 25, 1, 100)
    d = dbm.get()
    w = " AND ".join(where)
    total = d.execute(f"SELECT COUNT(*) FROM users WHERE {w}", args).fetchone()[0]
    items = rows(d.execute(
        f"SELECT {USER_COLS},reject_reason FROM users WHERE {w} "
        "ORDER BY CASE status WHEN 'pending' THEN 0 ELSE 1 END, created_at DESC, id DESC LIMIT ? OFFSET ?",
        args + [per, (page - 1) * per]))
    return jsonify(items=items, total=total, page=page, per=per)


def _set_status(uid, status, reason=""):
    d = dbm.get()
    u = one(d.execute("SELECT * FROM users WHERE id=?", (uid,)))
    if not u:
        raise ApiError("Usuario no encontrado.", 404)
    if u["id"] == g.user["id"]:
        raise ApiError("No puedes cambiar tu propio estado.", 400)
    if status == "approved":
        d.execute("UPDATE users SET status='approved', approved_at=?, approved_by=?, reject_reason=NULL WHERE id=?",
                  (now(), g.user["id"], uid))
        notify([uid], "¡Tu cuenta fue aprobada! Bienvenido a la plataforma.", "#/dashboard")
    else:
        d.execute("UPDATE users SET status=?, reject_reason=? WHERE id=?", (status, reason or None, uid))
    audit(f"user_{status}", f"{u['full_name']} <{u['email']}>")
    return u


@app.post("/api/admin/users/<int:uid>/status")
@need("admin")
def admin_user_status(uid):
    data = body()
    status = data.get("status")
    if status not in ("approved", "rejected", "disabled", "pending"):
        raise ApiError("Estado no válido.")
    _set_status(uid, status, clean(data.get("reason"), 200))
    return jsonify(ok=True)


@app.post("/api/admin/users/bulk-status")
@need("admin")
def admin_bulk_status():
    data = body()
    status = data.get("status")
    ids = data.get("ids")
    if status not in ("approved", "rejected") or not isinstance(ids, list) or not ids:
        raise ApiError("Selecciona al menos una solicitud.")
    n = 0
    for uid in ids[:500]:
        try:
            _set_status(int(uid), status, clean(data.get("reason"), 200))
            n += 1
        except (ApiError, ValueError, TypeError):
            continue
    return jsonify(ok=True, changed=n)


@app.patch("/api/admin/users/<int:uid>")
@need("admin")
def admin_user_edit(uid):
    data = body()
    d = dbm.get()
    u = one(d.execute("SELECT * FROM users WHERE id=?", (uid,)))
    if not u:
        raise ApiError("Usuario no encontrado.", 404)
    role = data.get("role", u["role"])
    if role not in ("student", "teacher", "admin"):
        raise ApiError("Rol no válido.")
    if u["id"] == g.user["id"] and role != "admin":
        raise ApiError("No puedes quitarte tu propio rol de administrador.")
    grade = clean(data.get("grade", u["grade"]), 10) or None
    if role == "student" and grade and grade not in GRADES:
        raise ApiError("Grado no válido.")
    d.execute("UPDATE users SET full_name=?, role=?, grade=?, section=?, specialty=?, id_number=? WHERE id=?",
              (clean(data.get("full_name", u["full_name"]), 120) or u["full_name"], role, grade,
               (clean(data.get("section", u["section"]), 10).upper() or None), clean(data.get("specialty", u["specialty"]), 80),
               clean(data.get("id_number", u["id_number"]), 30), uid))
    audit("user_edit", f"{u['full_name']} <{u['email']}>")
    return jsonify(ok=True)


@app.post("/api/admin/users/import")
@need("admin")
def admin_import():
    """Carga masiva (por ejemplo los ~800 estudiantes). Quedan aprobados; entran con su Google."""
    data = body()
    items = data.get("rows")
    if not isinstance(items, list) or not items:
        raise ApiError("No hay filas para importar.")
    if len(items) > 1500:
        raise ApiError("Máximo 1500 filas por carga.")
    d = dbm.get()
    created, skipped, errors = 0, 0, []
    for i, r in enumerate(items, start=1):
        if not isinstance(r, dict):
            continue
        email = clean(r.get("email"), 120).lower()
        name = clean(r.get("full_name"), 120)
        role = clean(r.get("role"), 10).lower() or "student"
        role = {"estudiante": "student", "docente": "teacher", "maestro": "teacher"}.get(role, role)
        grade = clean(r.get("grade"), 10).lower()
        if not EMAIL_RE.match(email) or len(name) < 3 or role not in ("student", "teacher"):
            errors.append(f"Fila {i}: datos incompletos o correo inválido")
            continue
        if role == "student" and grade not in GRADES:
            errors.append(f"Fila {i}: grado inválido para {name}")
            continue
        if d.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
            skipped += 1
            continue
        d.execute(
            "INSERT INTO users(email,full_name,role,status,id_number,grade,section,specialty,created_at,approved_at,approved_by) "
            "VALUES(?,?,?,'approved',?,?,?,?,?,?,?)",
            (email, name, role, clean(r.get("id_number"), 30), grade if role == "student" else None,
             clean(r.get("section"), 10).upper() or None, clean(r.get("specialty"), 80), now(), now(), g.user["id"]))
        created += 1
    audit("import", f"{created} creados, {skipped} existentes")
    return jsonify(created=created, skipped=skipped, errors=errors[:20], error_count=len(errors))


@app.get("/api/admin/courses")
@need("admin")
def admin_courses():
    d = dbm.get()
    q = request.args.get("q", "")
    sql = COURSE_SQL
    args = []
    if q:
        sql += " WHERE c.name LIKE ? ESCAPE '\\' OR u.full_name LIKE ? ESCAPE '\\'"
        args = [like(q), like(q)]
    return jsonify(items=rows(d.execute(sql + " ORDER BY c.created_at DESC LIMIT 300", args)))


@app.post("/api/admin/courses/<int:cid>/grades/reopen")
@need("admin")
def admin_reopen(cid):
    c = get_course(cid)
    dbm.get().execute("UPDATE courses SET grade_status='open', grades_submitted_at=NULL WHERE id=?", (cid,))
    notify([c["teacher_id"]], f"Se reabrieron las calificaciones de «{c['name']}» para corregirlas.", f"#/courses/{cid}/grades")
    audit("grades_reopen", c["name"])
    return jsonify(ok=True)


@app.get("/api/admin/audit")
@need("admin")
def admin_audit():
    return jsonify(items=rows(dbm.get().execute(
        "SELECT a.id,a.action,a.detail,a.created_at,u.full_name AS actor FROM audit_log a "
        "LEFT JOIN users u ON u.id=a.actor_id ORDER BY a.id DESC LIMIT 100")))


@app.get("/api/teachers")
@need("admin")
def list_teachers():
    return jsonify(items=rows(dbm.get().execute(
        "SELECT id,full_name,email FROM users WHERE role='teacher' AND status='approved' ORDER BY full_name")))


# ----------------------------------------------------------------------------
# Cursos
# ----------------------------------------------------------------------------


@app.get("/api/courses")
@need()
def list_courses():
    u, d = g.user, dbm.get()
    if u["role"] == "admin":
        cur = d.execute(COURSE_SQL + " ORDER BY c.created_at DESC LIMIT 300")
    elif u["role"] == "teacher":
        cur = d.execute(COURSE_SQL + " WHERE c.teacher_id=? ORDER BY c.created_at DESC", (u["id"],))
    else:
        cur = d.execute(COURSE_SQL + " WHERE c.id IN (SELECT course_id FROM enrollments WHERE student_id=?) "
                        "ORDER BY c.name", (u["id"],))
    return jsonify(items=rows(cur))


def _course_fields(data, existing=None):
    ex = existing or {}
    name = clean(data.get("name", ex.get("name")), 100)
    if len(name) < 3:
        raise ApiError("Escribe el nombre del curso (mínimo 3 letras).")
    grade = clean(data.get("grade", ex.get("grade")), 10)
    if grade and grade not in GRADES:
        raise ApiError("Grado no válido.")
    color = clean(data.get("color", ex.get("color")), 7) or "#1c5aa8"
    if not COLORS.match(color):
        raise ApiError("Color no válido.")
    return dict(
        name=name, subject=clean(data.get("subject", ex.get("subject")), 80),
        code=clean(data.get("code", ex.get("code")), 20).upper(), grade=grade or None,
        section=clean(data.get("section", ex.get("section")), 10).upper() or None,
        school_year=clean(data.get("school_year", ex.get("school_year")), 12) or default_school_year(),
        description=text(data.get("description", ex.get("description")), 1000), color=color)


@app.post("/api/courses")
@need("teacher", "admin")
def create_course():
    data = body()
    f = _course_fields(data)
    teacher_id = g.user["id"]
    if g.user["role"] == "admin":
        teacher_id = ival(data.get("teacher_id"), 0, 0)
        t = dbm.get().execute("SELECT 1 FROM users WHERE id=? AND role='teacher' AND status='approved'", (teacher_id,)).fetchone()
        if not t:
            raise ApiError("Elige el docente responsable del curso.")
    cur = dbm.get().execute(
        "INSERT INTO courses(teacher_id,name,subject,code,grade,section,school_year,description,color,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?)",
        (teacher_id, f["name"], f["subject"], f["code"], f["grade"], f["section"], f["school_year"],
         f["description"], f["color"], now()))
    audit("course_create", f["name"])
    return jsonify(id=cur.lastrowid), 201


@app.get("/api/courses/<int:cid>")
@need()
def course_detail(cid):
    return jsonify(course=get_course(cid))


@app.patch("/api/courses/<int:cid>")
@need("teacher", "admin")
def course_update(cid):
    c = get_course(cid, manage=True)
    f = _course_fields(body(), c)
    dbm.get().execute(
        "UPDATE courses SET name=?,subject=?,code=?,grade=?,section=?,school_year=?,description=?,color=? WHERE id=?",
        (f["name"], f["subject"], f["code"], f["grade"], f["section"], f["school_year"], f["description"], f["color"], cid))
    return jsonify(ok=True)


@app.delete("/api/courses/<int:cid>")
@need("teacher", "admin")
def course_delete(cid):
    c = get_course(cid, manage=True)
    files = course_files(cid)
    dbm.get().execute("DELETE FROM courses WHERE id=?", (cid,))
    for f in files:
        rm_file(f)
    audit("course_delete", c["name"])
    return jsonify(ok=True)


# --- Estudiantes del curso ---------------------------------------------------


@app.get("/api/students")
@need("teacher", "admin")
def search_students():
    """Los docentes buscan estudiantes (ya aprobados) por nombre, curso/grado, sección o matrícula."""
    a = request.args
    where = ["role='student'", "status='approved'"]
    args = []
    if a.get("q"):
        where.append("(full_name LIKE ? ESCAPE '\\' OR email LIKE ? ESCAPE '\\' OR id_number LIKE ? ESCAPE '\\')")
        args += [like(a["q"])] * 3
    if a.get("grade"):
        where.append("grade=?")
        args.append(a["grade"])
    if a.get("section"):
        where.append("section=?")
        args.append(a["section"].upper())
    exclude = ival(a.get("exclude_course"), 0, 0)
    if exclude:
        where.append("id NOT IN (SELECT student_id FROM enrollments WHERE course_id=?)")
        args.append(exclude)
    w = " AND ".join(where)
    d = dbm.get()
    total = d.execute(f"SELECT COUNT(*) FROM users WHERE {w}", args).fetchone()[0]
    items = rows(d.execute(
        f"SELECT id,full_name,email,grade,section,specialty,id_number,picture FROM users WHERE {w} "
        "ORDER BY grade, section, full_name LIMIT 60", args))
    return jsonify(items=items, total=total)


@app.get("/api/courses/<int:cid>/students")
@need()
def course_students(cid):
    c = get_course(cid)
    items = rows(dbm.get().execute(
        "SELECT u.id,u.full_name,u.email,u.grade,u.section,u.specialty,u.id_number,u.picture,e.added_at "
        "FROM enrollments e JOIN users u ON u.id=e.student_id WHERE e.course_id=? ORDER BY u.full_name", (cid,)))
    if not c["can_manage"]:  # los estudiantes solo ven nombre y foto de sus compañeros
        items = [{"id": i["id"], "full_name": i["full_name"], "picture": i["picture"], "grade": i["grade"],
                  "section": i["section"]} for i in items]
    return jsonify(items=items)


@app.post("/api/courses/<int:cid>/students")
@need("teacher", "admin")
def course_add_students(cid):
    c = get_course(cid, manage=True)
    ids = body().get("student_ids")
    if not isinstance(ids, list) or not ids:
        raise ApiError("Selecciona al menos un estudiante.")
    d = dbm.get()
    added = []
    for sid in ids[:300]:
        sid = ival(sid, 0, 0)
        ok = d.execute("SELECT 1 FROM users WHERE id=? AND role='student' AND status='approved'", (sid,)).fetchone()
        if ok:
            cur = d.execute("INSERT OR IGNORE INTO enrollments(course_id,student_id,added_at) VALUES(?,?,?)", (cid, sid, now()))
            if cur.rowcount:
                added.append(sid)
    notify(added, f"Te agregaron al curso «{c['name']}».", f"#/courses/{cid}")
    return jsonify(added=len(added))


@app.post("/api/courses/<int:cid>/students/bulk")
@need("teacher", "admin")
def course_add_group(cid):
    """Agrega todo un grado/sección de una vez."""
    c = get_course(cid, manage=True)
    data = body()
    grade = clean(data.get("grade"), 10)
    section = clean(data.get("section"), 10).upper()
    if grade not in GRADES:
        raise ApiError("Elige el grado.")
    d = dbm.get()
    sql = "SELECT id FROM users WHERE role='student' AND status='approved' AND grade=?"
    args = [grade]
    if section:
        sql += " AND section=?"
        args.append(section)
    ids = [r[0] for r in d.execute(sql, args)]
    added = []
    for sid in ids:
        cur = d.execute("INSERT OR IGNORE INTO enrollments(course_id,student_id,added_at) VALUES(?,?,?)", (cid, sid, now()))
        if cur.rowcount:
            added.append(sid)
    notify(added, f"Te agregaron al curso «{c['name']}».", f"#/courses/{cid}")
    return jsonify(added=len(added), found=len(ids))


@app.delete("/api/courses/<int:cid>/students/<int:sid>")
@need("teacher", "admin")
def course_remove_student(cid, sid):
    get_course(cid, manage=True)
    d = dbm.get()
    d.execute("DELETE FROM enrollments WHERE course_id=? AND student_id=?", (cid, sid))
    d.execute("DELETE FROM final_grades WHERE course_id=? AND student_id=?", (cid, sid))
    return jsonify(ok=True)


# --- Lecciones / materiales ---------------------------------------------------


def _safe_url(v):
    v = clean(v, 500)
    if not v:
        return ""
    p = urlparse(v)
    if p.scheme not in ("http", "https") or not p.netloc:
        raise ApiError("El enlace debe comenzar con http:// o https://")
    return v


def _lesson_kind(url, has_file):
    if url:
        host = urlparse(url).netloc.lower()
        if any(h in host for h in ("youtube.com", "youtu.be", "vimeo.com")):
            return "video"
        return "link"
    return "file" if has_file else "text"


@app.get("/api/courses/<int:cid>/lessons")
@need()
def lessons_list(cid):
    c = get_course(cid)
    sql = "SELECT id,course_id,title,description,kind,url,file_name,published,position,created_at FROM lessons WHERE course_id=?"
    if not c["can_manage"]:
        sql += " AND published=1"
    return jsonify(items=rows(dbm.get().execute(sql + " ORDER BY position, id", (cid,))))


@app.post("/api/courses/<int:cid>/lessons")
@need("teacher", "admin")
def lesson_create(cid):
    get_course(cid, manage=True)
    data = body()
    title = clean(data.get("title"), 120)
    if len(title) < 3:
        raise ApiError("Escribe el título de la clase.")
    url = _safe_url(data.get("url"))
    orig, stored = save_upload()
    desc = text(data.get("description"), 6000)
    if not (url or stored or desc):
        raise ApiError("Agrega contenido: texto, un enlace o un archivo.")
    d = dbm.get()
    pos = d.execute("SELECT COALESCE(MAX(position),0)+1 FROM lessons WHERE course_id=?", (cid,)).fetchone()[0]
    published = 0 if str(data.get("published", "1")) in ("0", "false", "False") else 1
    cur = d.execute(
        "INSERT INTO lessons(course_id,title,description,kind,url,file_name,file_path,published,position,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?)",
        (cid, title, desc, _lesson_kind(url, bool(stored)), url or None, orig, stored, published, pos, now()))
    if published:
        c = get_course(cid)
        ids = [r[0] for r in d.execute("SELECT student_id FROM enrollments WHERE course_id=?", (cid,))]
        notify(ids, f"Nueva clase en {c['name']}: {title}", f"#/courses/{cid}/lessons")
    return jsonify(id=cur.lastrowid), 201


def _lesson(lid, manage=False):
    d = dbm.get()
    l = one(d.execute("SELECT * FROM lessons WHERE id=?", (lid,)))
    if not l:
        raise ApiError("Clase no encontrada.", 404)
    c = get_course(l["course_id"], manage=manage)
    if not c["can_manage"] and not l["published"]:
        raise ApiError("Clase no encontrada.", 404)
    return l


@app.patch("/api/lessons/<int:lid>")
@need("teacher", "admin")
def lesson_update(lid):
    l = _lesson(lid, manage=True)
    data = body()
    title = clean(data.get("title", l["title"]), 120)
    if len(title) < 3:
        raise ApiError("Escribe el título de la clase.")
    url = _safe_url(data["url"]) if "url" in data else (l["url"] or "")
    orig, stored = save_upload()
    file_name, file_path = l["file_name"], l["file_path"]
    if stored:
        rm_file(file_path)
        file_name, file_path = orig, stored
    pub = l["published"]
    if "published" in data:
        pub = 0 if str(data["published"]) in ("0", "false", "False") else 1
    dbm.get().execute(
        "UPDATE lessons SET title=?,description=?,kind=?,url=?,file_name=?,file_path=?,published=? WHERE id=?",
        (title, text(data.get("description", l["description"]), 6000), _lesson_kind(url, bool(file_path)),
         url or None, file_name, file_path, pub, lid))
    return jsonify(ok=True)


@app.delete("/api/lessons/<int:lid>")
@need("teacher", "admin")
def lesson_delete(lid):
    l = _lesson(lid, manage=True)
    dbm.get().execute("DELETE FROM lessons WHERE id=?", (lid,))
    rm_file(l["file_path"])
    return jsonify(ok=True)


@app.get("/api/lessons/<int:lid>/file")
@need()
def lesson_file(lid):
    l = _lesson(lid)
    return send_stored(l["file_path"], l["file_name"])


# --- Anuncios ----------------------------------------------------------------


@app.get("/api/courses/<int:cid>/announcements")
@need()
def ann_list(cid):
    get_course(cid)
    return jsonify(items=rows(dbm.get().execute(
        "SELECT a.id,a.title,a.body,a.created_at,u.full_name AS author FROM announcements a "
        "JOIN users u ON u.id=a.author_id WHERE a.course_id=? ORDER BY a.id DESC LIMIT 50", (cid,))))


@app.post("/api/courses/<int:cid>/announcements")
@need("teacher", "admin")
def ann_create(cid):
    c = get_course(cid, manage=True)
    data = body()
    title = clean(data.get("title"), 120)
    if len(title) < 3:
        raise ApiError("Escribe el título del anuncio.")
    d = dbm.get()
    cur = d.execute("INSERT INTO announcements(course_id,author_id,title,body,created_at) VALUES(?,?,?,?,?)",
                    (cid, g.user["id"], title, text(data.get("body"), 3000), now()))
    ids = [r[0] for r in d.execute("SELECT student_id FROM enrollments WHERE course_id=?", (cid,))]
    notify(ids, f"Anuncio en {c['name']}: {title}", f"#/courses/{cid}")
    return jsonify(id=cur.lastrowid), 201


@app.delete("/api/announcements/<int:aid>")
@need("teacher", "admin")
def ann_delete(aid):
    d = dbm.get()
    a = one(d.execute("SELECT * FROM announcements WHERE id=?", (aid,)))
    if not a:
        raise ApiError("Anuncio no encontrado.", 404)
    get_course(a["course_id"], manage=True)
    d.execute("DELETE FROM announcements WHERE id=?", (aid,))
    return jsonify(ok=True)


# --- Tareas ------------------------------------------------------------------


@app.get("/api/courses/<int:cid>/assignments")
@need()
def assignments_list(cid):
    c = get_course(cid)
    d = dbm.get()
    if c["can_manage"]:
        cur = d.execute(
            "SELECT a.id,a.course_id,a.title,a.instructions,a.due_at,a.max_points,a.file_name,a.created_at,"
            "(SELECT COUNT(*) FROM submissions s WHERE s.assignment_id=a.id) AS submitted_count,"
            "(SELECT COUNT(*) FROM submissions s WHERE s.assignment_id=a.id AND s.score IS NOT NULL) AS graded_count "
            "FROM assignments a WHERE a.course_id=? ORDER BY a.due_at IS NULL, a.due_at, a.id", (cid,))
    else:
        cur = d.execute(
            "SELECT a.id,a.course_id,a.title,a.instructions,a.due_at,a.max_points,a.file_name,a.created_at,"
            "s.id AS submission_id,s.submitted_at,s.score,s.feedback,s.graded_at "
            "FROM assignments a LEFT JOIN submissions s ON s.assignment_id=a.id AND s.student_id=? "
            "WHERE a.course_id=? ORDER BY a.due_at IS NULL, a.due_at, a.id", (g.user["id"], cid))
    return jsonify(items=rows(cur), enrolled=c["student_count"])


@app.post("/api/courses/<int:cid>/assignments")
@need("teacher", "admin")
def assignment_create(cid):
    c = get_course(cid, manage=True)
    data = body()
    title = clean(data.get("title"), 120)
    if len(title) < 3:
        raise ApiError("Escribe el título de la tarea.")
    points = num(data.get("max_points", 100), 1, 1000, "puntaje") or 100
    orig, stored = save_upload()
    d = dbm.get()
    cur = d.execute(
        "INSERT INTO assignments(course_id,title,instructions,due_at,max_points,file_name,file_path,created_at) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (cid, title, text(data.get("instructions"), 6000), parse_dt(data.get("due_at")), points, orig, stored, now()))
    ids = [r[0] for r in d.execute("SELECT student_id FROM enrollments WHERE course_id=?", (cid,))]
    notify(ids, f"Nueva tarea en {c['name']}: {title}", f"#/courses/{cid}/assignments")
    return jsonify(id=cur.lastrowid), 201


def _assignment(aid, manage=False):
    a = one(dbm.get().execute("SELECT * FROM assignments WHERE id=?", (aid,)))
    if not a:
        raise ApiError("Tarea no encontrada.", 404)
    return a, get_course(a["course_id"], manage=manage)


@app.patch("/api/assignments/<int:aid>")
@need("teacher", "admin")
def assignment_update(aid):
    a, _ = _assignment(aid, manage=True)
    data = body()
    title = clean(data.get("title", a["title"]), 120)
    if len(title) < 3:
        raise ApiError("Escribe el título de la tarea.")
    orig, stored = save_upload()
    fname, fpath = a["file_name"], a["file_path"]
    if stored:
        rm_file(fpath)
        fname, fpath = orig, stored
    due = parse_dt(data["due_at"]) if "due_at" in data else a["due_at"]
    points = num(data.get("max_points", a["max_points"]), 1, 1000, "puntaje") or a["max_points"]
    dbm.get().execute(
        "UPDATE assignments SET title=?,instructions=?,due_at=?,max_points=?,file_name=?,file_path=? WHERE id=?",
        (title, text(data.get("instructions", a["instructions"]), 6000), due, points, fname, fpath, aid))
    return jsonify(ok=True)


@app.delete("/api/assignments/<int:aid>")
@need("teacher", "admin")
def assignment_delete(aid):
    a, _ = _assignment(aid, manage=True)
    d = dbm.get()
    files = [r[0] for r in d.execute("SELECT file_path FROM submissions WHERE assignment_id=? AND file_path IS NOT NULL", (aid,))]
    d.execute("DELETE FROM assignments WHERE id=?", (aid,))
    for f in files + [a["file_path"]]:
        rm_file(f)
    return jsonify(ok=True)


@app.get("/api/assignments/<int:aid>/file")
@need()
def assignment_file(aid):
    a, _ = _assignment(aid)
    return send_stored(a["file_path"], a["file_name"])


@app.get("/api/assignments/<int:aid>/submissions")
@need("teacher", "admin")
def submissions_list(aid):
    a, _ = _assignment(aid, manage=True)
    items = rows(dbm.get().execute(
        "SELECT u.id AS student_id,u.full_name,u.grade,u.section,s.id AS submission_id,s.text,s.file_name,"
        "s.submitted_at,s.score,s.feedback,s.graded_at "
        "FROM enrollments e JOIN users u ON u.id=e.student_id "
        "LEFT JOIN submissions s ON s.assignment_id=? AND s.student_id=u.id "
        "WHERE e.course_id=? ORDER BY (s.id IS NULL), u.full_name", (aid, a["course_id"])))
    return jsonify(assignment=a | {"file_path": None}, items=items)


@app.post("/api/assignments/<int:aid>/submit")
@need("student")
def submit_assignment(aid):
    a, c = _assignment(aid)
    data = body()
    t = text(data.get("text"), 8000)
    orig, stored = save_upload()
    d = dbm.get()
    prev = one(d.execute("SELECT * FROM submissions WHERE assignment_id=? AND student_id=?", (aid, g.user["id"])))
    if prev and prev["score"] is not None:
        if stored:
            rm_file(stored)
        raise ApiError("Esta tarea ya fue calificada y no se puede modificar.", 409)
    if not t and not stored and not (prev and prev["file_path"]):
        raise ApiError("Escribe una respuesta o adjunta un archivo.")
    if prev:
        fname, fpath = prev["file_name"], prev["file_path"]
        if stored:
            rm_file(fpath)
            fname, fpath = orig, stored
        d.execute("UPDATE submissions SET text=?,file_name=?,file_path=?,submitted_at=? WHERE id=?",
                  (t, fname, fpath, now(), prev["id"]))
    else:
        d.execute("INSERT INTO submissions(assignment_id,student_id,text,file_name,file_path,submitted_at) "
                  "VALUES(?,?,?,?,?,?)", (aid, g.user["id"], t, orig, stored, now()))
        notify([c["teacher_id"]], f"{g.user['full_name']} entregó «{a['title']}» ({c['name']})",
               f"#/courses/{c['id']}/assignments")
    return jsonify(ok=True)


def _submission(sid):
    s = one(dbm.get().execute("SELECT * FROM submissions WHERE id=?", (sid,)))
    if not s:
        raise ApiError("Entrega no encontrada.", 404)
    a = one(dbm.get().execute("SELECT * FROM assignments WHERE id=?", (s["assignment_id"],)))
    c = get_course(a["course_id"])
    if not c["can_manage"] and s["student_id"] != g.user["id"]:
        raise ApiError("No tienes acceso a esta entrega.", 403)
    return s, a, c


@app.get("/api/submissions/<int:sid>/file")
@need()
def submission_file(sid):
    s, _, _ = _submission(sid)
    return send_stored(s["file_path"], s["file_name"])


@app.patch("/api/submissions/<int:sid>/grade")
@need("teacher", "admin")
def grade_submission(sid):
    s, a, c = _submission(sid)
    if not c["can_manage"]:
        raise ApiError("Solo el docente del curso puede calificar.", 403)
    data = body()
    score = num(data.get("score"), 0, a["max_points"], "puntaje")
    if score is None:
        raise ApiError("Escribe el puntaje.")
    dbm.get().execute("UPDATE submissions SET score=?,feedback=?,graded_at=? WHERE id=?",
                      (score, text(data.get("feedback"), 2000), now(), sid))
    notify([s["student_id"]], f"Calificaron «{a['title']}»: {score:g}/{a['max_points']:g}", f"#/courses/{c['id']}/assignments")
    return jsonify(ok=True)


@app.get("/api/my/assignments")
@need("student")
def my_assignments():
    return jsonify(items=rows(dbm.get().execute(
        "SELECT a.id,a.title,a.due_at,a.max_points,a.course_id,c.name AS course_name,c.color,"
        "s.submitted_at,s.score,s.graded_at "
        "FROM assignments a JOIN courses c ON c.id=a.course_id "
        "JOIN enrollments e ON e.course_id=c.id AND e.student_id=? "
        "LEFT JOIN submissions s ON s.assignment_id=a.id AND s.student_id=? "
        "ORDER BY a.due_at IS NULL, a.due_at LIMIT 300", (g.user["id"], g.user["id"]))))


# --- Calificaciones finales ----------------------------------------------------

PERIODS = ("p1", "p2", "p3", "p4")


def final_of(r):
    vals = [r.get(k) for k in PERIODS]
    return None if any(v is None for v in vals) else int(sum(vals) / 4 + 0.5)


def _gradebook(c):
    d = dbm.get()
    items = rows(d.execute(
        "SELECT u.id AS student_id,u.full_name,u.grade,u.section,u.id_number,f.p1,f.p2,f.p3,f.p4 "
        "FROM enrollments e JOIN users u ON u.id=e.student_id "
        "LEFT JOIN final_grades f ON f.course_id=e.course_id AND f.student_id=u.id "
        "WHERE e.course_id=? ORDER BY u.full_name", (c["id"],)))
    avgs = {r[0]: r[1] for r in d.execute(
        "SELECT s.student_id, AVG(s.score*100.0/a.max_points) FROM submissions s JOIN assignments a ON a.id=s.assignment_id "
        "WHERE a.course_id=? AND s.score IS NOT NULL GROUP BY s.student_id", (c["id"],))}
    for r in items:
        r["final"] = final_of(r)
        r["tasks_avg"] = round(avgs[r["student_id"]], 1) if r["student_id"] in avgs else None
    return items


@app.get("/api/courses/<int:cid>/grades")
@need("teacher", "admin")
def grades_get(cid):
    c = get_course(cid, manage=True)
    return jsonify(items=_gradebook(c), status=c["grade_status"], submitted_at=c["grades_submitted_at"], pass_mark=70)


@app.put("/api/courses/<int:cid>/grades")
@need("teacher", "admin")
def grades_save(cid):
    c = get_course(cid, manage=True)
    if c["grade_status"] == "submitted":
        raise ApiError("Las calificaciones ya fueron enviadas a dirección. Pide que las reabran para corregir.", 409)
    items = body().get("rows")
    if not isinstance(items, list):
        raise ApiError("Datos no válidos.")
    d = dbm.get()
    enrolled = {r[0] for r in d.execute("SELECT student_id FROM enrollments WHERE course_id=?", (cid,))}
    n = 0
    for r in items:
        sid = ival(r.get("student_id"), 0, 0) if isinstance(r, dict) else 0
        if sid not in enrolled:
            continue
        vals = [num(r.get(k), 0, 100, "período") for k in PERIODS]
        d.execute(
            "INSERT INTO final_grades(course_id,student_id,p1,p2,p3,p4,updated_at) VALUES(?,?,?,?,?,?,?) "
            "ON CONFLICT(course_id,student_id) DO UPDATE SET p1=excluded.p1,p2=excluded.p2,p3=excluded.p3,"
            "p4=excluded.p4,updated_at=excluded.updated_at", (cid, sid, *vals, now()))
        n += 1
    return jsonify(saved=n)


@app.post("/api/courses/<int:cid>/grades/submit")
@need("teacher", "admin")
def grades_submit(cid):
    c = get_course(cid, manage=True)
    if c["grade_status"] == "submitted":
        raise ApiError("Ya enviaste estas calificaciones.", 409)
    book = _gradebook(c)
    if not book:
        raise ApiError("El curso no tiene estudiantes.")
    missing = [r["full_name"] for r in book if r["final"] is None]
    if missing:
        raise ApiError(f"Faltan notas de los 4 períodos en {len(missing)} estudiante(s).", 422, missing=missing[:15])
    d = dbm.get()
    d.execute("UPDATE courses SET grade_status='submitted', grades_submitted_at=? WHERE id=?", (now(), cid))
    admins = [r[0] for r in d.execute("SELECT id FROM users WHERE role='admin' AND status='approved'")]
    notify(admins, f"{g.user['full_name']} envió las calificaciones finales de «{c['name']}»", "#/grades")
    notify([r["student_id"] for r in book], f"Ya están disponibles tus calificaciones finales de {c['name']}", "#/my-grades")
    audit("grades_submit", c["name"])
    return jsonify(ok=True)


def _csv_cell(v):
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@") else s


@app.get("/api/courses/<int:cid>/grades.csv")
@need("teacher", "admin")
def grades_csv(cid):
    c = get_course(cid, manage=True)
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Estudiante", "Matrícula", "Grado", "Sección", "P1", "P2", "P3", "P4", "Final", "Situación"])
    for r in _gradebook(c):
        w.writerow([_csv_cell(r["full_name"]), _csv_cell(r["id_number"]), r["grade"], r["section"],
                    r["p1"], r["p2"], r["p3"], r["p4"], r["final"],
                    "" if r["final"] is None else ("Aprobado" if r["final"] >= 70 else "Reprobado")])
    name = re.sub(r"[^A-Za-z0-9_-]+", "_", c["name"]) or "curso"
    return Response("﻿" + out.getvalue(), mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="calificaciones_{name}.csv"'})


def _grades_for(sid):
    """Notas finales de un estudiante. Solo se ven cuando el docente las envió a dirección."""
    items = rows(dbm.get().execute(
        "SELECT c.id AS course_id,c.name,c.color,c.grade_status,u.full_name AS teacher_name,f.p1,f.p2,f.p3,f.p4 "
        "FROM enrollments e JOIN courses c ON c.id=e.course_id JOIN users u ON u.id=c.teacher_id "
        "LEFT JOIN final_grades f ON f.course_id=c.id AND f.student_id=e.student_id "
        "WHERE e.student_id=? ORDER BY c.name", (sid,)))
    for r in items:
        if r["grade_status"] == "submitted":
            r["final"] = final_of(r)
            r["published"] = True
        else:
            r.update(p1=None, p2=None, p3=None, p4=None, final=None, published=False)
    return items


@app.get("/api/my/grades")
@need("student")
def my_grades():
    return jsonify(items=_grades_for(g.user["id"]), pass_mark=70)


# --- Asistencia ----------------------------------------------------------------

ATT = ("P", "A", "T", "E")
DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _day(v):
    v = clean(v, 10)
    try:
        d = datetime.strptime(v, "%Y-%m-%d").date() if DAY_RE.match(v) else None
    except ValueError:
        d = None
    if not d or d.year < 2020:
        raise ApiError("Fecha no válida.")
    if d > (datetime.now(timezone.utc) + timedelta(days=1)).date():
        raise ApiError("No puedes tomar asistencia de una fecha futura.")
    return v


def att_stats(p, a, t, e):
    """% de asistencia = (presentes + tardanzas) / (días - excusas)."""
    n = p + a + t + e
    den = n - e
    return {"present": p, "absent": a, "late": t, "excused": e, "days": n,
            "percent": None if den <= 0 else round((p + t) * 100 / den)}


@app.get("/api/courses/<int:cid>/attendance")
@need()
def attendance_get(cid):
    c = get_course(cid)
    d = dbm.get()
    if not c["can_manage"]:
        recs = rows(d.execute("SELECT day,status FROM attendance WHERE course_id=? AND student_id=? ORDER BY day DESC",
                              (cid, g.user["id"])))
        cnt = {k: sum(1 for r in recs if r["status"] == k) for k in ATT}
        return jsonify(records=recs, **att_stats(cnt["P"], cnt["A"], cnt["T"], cnt["E"]))
    day = _day(request.args.get("day") or datetime.now(timezone.utc).date().isoformat())
    items = rows(d.execute(
        "SELECT u.id AS student_id,u.full_name,u.grade,u.section,a.status FROM enrollments e "
        "JOIN users u ON u.id=e.student_id LEFT JOIN attendance a ON a.course_id=e.course_id "
        "AND a.student_id=u.id AND a.day=? WHERE e.course_id=? ORDER BY u.full_name", (day, cid)))
    tot = {r["student_id"]: r for r in rows(d.execute(
        "SELECT student_id,SUM(status='P') p,SUM(status='A') a,SUM(status='T') t,SUM(status='E') e "
        "FROM attendance WHERE course_id=? GROUP BY student_id", (cid,)))}
    for i in items:
        t = tot.get(i["student_id"]) or {"p": 0, "a": 0, "t": 0, "e": 0}
        i.update(att_stats(t["p"], t["a"], t["t"], t["e"]))
    days = [r[0] for r in d.execute("SELECT DISTINCT day FROM attendance WHERE course_id=? ORDER BY day DESC LIMIT 60", (cid,))]
    return jsonify(day=day, items=items, days=days)


@app.put("/api/courses/<int:cid>/attendance")
@need("teacher", "admin")
def attendance_put(cid):
    get_course(cid, manage=True)
    data = body()
    day = _day(data.get("day"))
    items = data.get("rows")
    if not isinstance(items, list):
        raise ApiError("Datos no válidos.")
    d = dbm.get()
    enrolled = {r[0] for r in d.execute("SELECT student_id FROM enrollments WHERE course_id=?", (cid,))}
    n = 0
    for r in items:
        if not isinstance(r, dict):
            continue
        sid = ival(r.get("student_id"), 0, 0)
        st = r.get("status")
        if sid not in enrolled:
            continue
        if st in ATT:
            d.execute("INSERT INTO attendance(course_id,student_id,day,status) VALUES(?,?,?,?) "
                      "ON CONFLICT(course_id,student_id,day) DO UPDATE SET status=excluded.status", (cid, sid, day, st))
        elif st in (None, ""):
            d.execute("DELETE FROM attendance WHERE course_id=? AND student_id=? AND day=?", (cid, sid, day))
        else:
            raise ApiError("Estado de asistencia no válido.")
        n += 1
    return jsonify(saved=n)


# --- Quizzes ---------------------------------------------------------------------


def _parse_questions(qs):
    if not isinstance(qs, list) or not qs:
        raise ApiError("Agrega al menos una pregunta.")
    if len(qs) > 60:
        raise ApiError("Máximo 60 preguntas por quiz.")
    out = []
    for i, q in enumerate(qs, 1):
        if not isinstance(q, dict):
            raise ApiError(f"La pregunta {i} no es válida.")
        t = text(q.get("text"), 500)
        if len(t) < 2:
            raise ApiError(f"Escribe el enunciado de la pregunta {i}.")
        raw = q.get("options") if isinstance(q.get("options"), list) else []
        opts, remap = [], {}
        for j, o in enumerate(raw[:12]):
            s = clean(o, 200) if isinstance(o, str) else ""
            if s:
                remap[j] = len(opts)
                opts.append(s)
        if not 2 <= len(opts) <= 6:
            raise ApiError(f"La pregunta {i} necesita entre 2 y 6 opciones.")
        try:
            ci = int(q.get("correct"))
        except (TypeError, ValueError):
            ci = -1
        if ci not in remap:
            raise ApiError(f"Marca la respuesta correcta de la pregunta {i}.")
        pts = num(q.get("points", 1), 0.5, 100, "puntaje") or 1
        out.append((t, json.dumps(opts, ensure_ascii=False), remap[ci], pts))
    return out


def _save_questions(qid, parsed):
    d = dbm.get()
    d.execute("DELETE FROM quiz_questions WHERE quiz_id=?", (qid,))
    d.executemany("INSERT INTO quiz_questions(quiz_id,position,text,options,correct,points) VALUES(?,?,?,?,?,?)",
                  [(qid, i, t, o, c, p) for i, (t, o, c, p) in enumerate(parsed)])


def _quiz(qid, manage=False):
    z = one(dbm.get().execute("SELECT * FROM quizzes WHERE id=?", (qid,)))
    if not z:
        raise ApiError("Quiz no encontrado.", 404)
    c = get_course(z["course_id"], manage=manage)
    if not c["can_manage"] and not z["published"]:
        raise ApiError("Quiz no encontrado.", 404)
    return z, c


def _questions(qid):
    out = rows(dbm.get().execute("SELECT id,text,options,correct,points FROM quiz_questions WHERE quiz_id=? ORDER BY position,id", (qid,)))
    for r in out:
        r["options"] = json.loads(r["options"])
    return out


def _past_due(z):
    return bool(z["due_at"]) and datetime.fromisoformat(z["due_at"]) < datetime.now(timezone.utc)


@app.get("/api/courses/<int:cid>/quizzes")
@need()
def quizzes_list(cid):
    c = get_course(cid)
    d = dbm.get()
    if c["can_manage"]:
        cur = d.execute(
            "SELECT z.id,z.title,z.description,z.due_at,z.published,z.created_at,"
            "(SELECT COUNT(*) FROM quiz_questions q WHERE q.quiz_id=z.id) AS question_count,"
            "(SELECT COALESCE(SUM(points),0) FROM quiz_questions q WHERE q.quiz_id=z.id) AS total_points,"
            "(SELECT COUNT(*) FROM quiz_attempts a WHERE a.quiz_id=z.id) AS attempts "
            "FROM quizzes z WHERE z.course_id=? ORDER BY z.id DESC", (cid,))
    else:
        cur = d.execute(
            "SELECT z.id,z.title,z.description,z.due_at,z.created_at,"
            "(SELECT COUNT(*) FROM quiz_questions q WHERE q.quiz_id=z.id) AS question_count,"
            "a.score,a.max_score,a.submitted_at FROM quizzes z "
            "LEFT JOIN quiz_attempts a ON a.quiz_id=z.id AND a.student_id=? "
            "WHERE z.course_id=? AND z.published=1 ORDER BY z.id DESC", (g.user["id"], cid))
    return jsonify(items=rows(cur), enrolled=c["student_count"])


@app.post("/api/courses/<int:cid>/quizzes")
@need("teacher", "admin")
def quiz_create(cid):
    c = get_course(cid, manage=True)
    data = body()
    title = clean(data.get("title"), 120)
    if len(title) < 3:
        raise ApiError("Escribe el título del quiz.")
    parsed = _parse_questions(data.get("questions"))
    d = dbm.get()
    published = 0 if str(data.get("published", "1")) in ("0", "false", "False") else 1
    cur = d.execute("INSERT INTO quizzes(course_id,title,description,due_at,published,created_at) VALUES(?,?,?,?,?,?)",
                    (cid, title, text(data.get("description"), 2000), parse_dt(data.get("due_at")), published, now()))
    _save_questions(cur.lastrowid, parsed)
    if published:
        ids = [r[0] for r in d.execute("SELECT student_id FROM enrollments WHERE course_id=?", (cid,))]
        notify(ids, f"Nuevo quiz en {c['name']}: {title}", f"#/quizzes/{cur.lastrowid}")
    return jsonify(id=cur.lastrowid), 201


@app.get("/api/quizzes/<int:qid>")
@need()
def quiz_detail(qid):
    z, c = _quiz(qid)
    qs = _questions(qid)
    if c["can_manage"]:
        return jsonify(quiz=z, questions=qs, course={"id": c["id"], "name": c["name"], "color": c["color"]}, can_manage=True)
    a = one(dbm.get().execute("SELECT answers,score,max_score,submitted_at FROM quiz_attempts WHERE quiz_id=? AND student_id=?",
                               (qid, g.user["id"])))
    if a:
        a["answers"] = json.loads(a["answers"])
    else:
        for q in qs:
            del q["correct"]  # no se revela la respuesta hasta que el estudiante entregue
    return jsonify(quiz=z, questions=qs, attempt=a, closed=bool(not a and _past_due(z)),
                   course={"id": c["id"], "name": c["name"], "color": c["color"]}, can_manage=False)


@app.patch("/api/quizzes/<int:qid>")
@need("teacher", "admin")
def quiz_update(qid):
    z, _ = _quiz(qid, manage=True)
    data = body()
    d = dbm.get()
    title = clean(data.get("title", z["title"]), 120)
    if len(title) < 3:
        raise ApiError("Escribe el título del quiz.")
    if "questions" in data:
        if d.execute("SELECT 1 FROM quiz_attempts WHERE quiz_id=?", (qid,)).fetchone():
            raise ApiError("Ya hay estudiantes que lo respondieron: no se pueden cambiar las preguntas.", 409)
        _save_questions(qid, _parse_questions(data.get("questions")))
    pub = z["published"]
    if "published" in data:
        pub = 0 if str(data["published"]) in ("0", "false", "False") else 1
    due = parse_dt(data["due_at"]) if "due_at" in data else z["due_at"]
    d.execute("UPDATE quizzes SET title=?,description=?,due_at=?,published=? WHERE id=?",
              (title, text(data.get("description", z["description"]), 2000), due, pub, qid))
    return jsonify(ok=True)


@app.delete("/api/quizzes/<int:qid>")
@need("teacher", "admin")
def quiz_delete(qid):
    _quiz(qid, manage=True)
    dbm.get().execute("DELETE FROM quizzes WHERE id=?", (qid,))
    return jsonify(ok=True)


@app.post("/api/quizzes/<int:qid>/attempt")
@need("student")
def quiz_attempt(qid):
    z, c = _quiz(qid)
    d = dbm.get()
    if d.execute("SELECT 1 FROM quiz_attempts WHERE quiz_id=? AND student_id=?", (qid, g.user["id"])).fetchone():
        raise ApiError("Ya respondiste este quiz.", 409)
    if _past_due(z):
        raise ApiError("La fecha límite de este quiz ya pasó.", 409)
    qs = _questions(qid)
    answers = body().get("answers")
    if not isinstance(answers, list) or len(answers) != len(qs):
        raise ApiError("Respuestas no válidas.")
    clean_ans, score, total = [], 0.0, 0.0
    for q, a in zip(qs, answers):
        try:
            a = int(a) if a is not None else None
        except (TypeError, ValueError):
            a = None
        if a is not None and not 0 <= a < len(q["options"]):
            a = None
        clean_ans.append(a)
        total += q["points"]
        if a == q["correct"]:
            score += q["points"]
    d.execute("INSERT INTO quiz_attempts(quiz_id,student_id,answers,score,max_score,submitted_at) VALUES(?,?,?,?,?,?)",
              (qid, g.user["id"], json.dumps(clean_ans), round(score, 2), round(total, 2), now()))
    notify([c["teacher_id"]], f"{g.user['full_name']} respondió el quiz «{z['title']}» ({round(score, 2):g}/{round(total, 2):g})",
           f"#/quizzes/{qid}")
    return jsonify(score=round(score, 2), max_score=round(total, 2))


@app.get("/api/quizzes/<int:qid>/results")
@need("teacher", "admin")
def quiz_results(qid):
    z, c = _quiz(qid, manage=True)
    items = rows(dbm.get().execute(
        "SELECT u.id AS student_id,u.full_name,u.grade,u.section,a.score,a.max_score,a.submitted_at "
        "FROM enrollments e JOIN users u ON u.id=e.student_id LEFT JOIN quiz_attempts a ON a.quiz_id=? AND a.student_id=u.id "
        "WHERE e.course_id=? ORDER BY (a.id IS NULL), u.full_name", (qid, c["id"])))
    done = [i for i in items if i["score"] is not None]
    avg = round(sum(i["score"] * 100 / i["max_score"] for i in done) / len(done)) if done else None
    return jsonify(items=items, average=avg, answered=len(done))


# --- Boletín (para imprimir) ---------------------------------------------------


def _report(sid):
    d = dbm.get()
    st = one(d.execute("SELECT id,full_name,email,grade,section,id_number FROM users WHERE id=? AND role='student'", (sid,)))
    if not st:
        raise ApiError("Estudiante no encontrado.", 404)
    items = _grades_for(sid)
    att = {r["course_id"]: r for r in rows(d.execute(
        "SELECT course_id,SUM(status='P') p,SUM(status='A') a,SUM(status='T') t,SUM(status='E') e "
        "FROM attendance WHERE student_id=? GROUP BY course_id", (sid,)))}
    for r in items:
        t = att.get(r["course_id"])
        r["attendance"] = att_stats(t["p"], t["a"], t["t"], t["e"]) if t else None
    done = [r["final"] for r in items if r["final"] is not None]
    return {"student": st, "items": items, "pass_mark": 70, "school_year": default_school_year(),
            "average": round(sum(done) / len(done)) if done else None, "generated_at": now()}


@app.get("/api/my/report")
@need("student")
def my_report():
    return jsonify(_report(g.user["id"]))


@app.get("/api/admin/students/<int:sid>/report")
@need("admin")
def admin_report(sid):
    return jsonify(_report(sid))


# --- Calendario ----------------------------------------------------------------


@app.get("/api/calendar")
@need()
def calendar():
    m = clean(request.args.get("month"), 7)
    try:
        first = datetime.strptime(m + "-01", "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        raise ApiError("Mes no válido.")
    if not 2000 <= first.year <= 2100:
        raise ApiError("Mes no válido.")
    last =(first + timedelta(days=32)).replace(day=1)
    lo, hi = (first - timedelta(days=1)).isoformat(timespec="seconds"), (last + timedelta(days=1)).isoformat(timespec="seconds")
    u, d = g.user, dbm.get()
    if u["role"] == "admin":
        scope, args = "1=1", []
    elif u["role"] == "teacher":
        scope, args = "c.teacher_id=?", [u["id"]]
    else:
        scope, args = "c.id IN (SELECT course_id FROM enrollments WHERE student_id=?)", [u["id"]]
    me = u["id"]
    tasks = rows(d.execute(
        "SELECT a.id,a.title,a.due_at,c.id AS course_id,c.name AS course_name,c.color,'task' AS kind,"
        "(SELECT COUNT(*) FROM submissions s WHERE s.assignment_id=a.id AND s.student_id=?) AS done "
        f"FROM assignments a JOIN courses c ON c.id=a.course_id WHERE {scope} AND a.due_at>=? AND a.due_at<?",
        [me] + args + [lo, hi]))
    quizzes = rows(d.execute(
        "SELECT z.id,z.title,z.due_at,c.id AS course_id,c.name AS course_name,c.color,'quiz' AS kind,"
        "(SELECT COUNT(*) FROM quiz_attempts t WHERE t.quiz_id=z.id AND t.student_id=?) AS done "
        f"FROM quizzes z JOIN courses c ON c.id=z.course_id WHERE {scope} AND z.due_at>=? AND z.due_at<? "
        + ("" if u["role"] != "student" else "AND z.published=1"),
        [me] + args + [lo, hi]))
    return jsonify(items=sorted(tasks + quizzes, key=lambda x: x["due_at"]))


@app.get("/api/admin/grades")
@need("admin")
def admin_grades():
    return jsonify(items=rows(dbm.get().execute(
        "SELECT c.id,c.name,c.grade,c.section,c.grade_status,c.grades_submitted_at,u.full_name AS teacher_name,"
        "(SELECT COUNT(*) FROM enrollments e WHERE e.course_id=c.id) AS student_count,"
        "(SELECT COUNT(*) FROM final_grades f WHERE f.course_id=c.id AND f.p1 IS NOT NULL AND f.p2 IS NOT NULL "
        " AND f.p3 IS NOT NULL AND f.p4 IS NOT NULL) AS complete "
        "FROM courses c JOIN users u ON u.id=c.teacher_id "
        "ORDER BY (c.grade_status='submitted'), c.grades_submitted_at DESC, c.name LIMIT 300")))


# ----------------------------------------------------------------------------
# Notificaciones y panel de inicio
# ----------------------------------------------------------------------------


@app.get("/api/notifications")
@need()
def notifications():
    d = dbm.get()
    items = rows(d.execute("SELECT id,text,link,is_read,created_at FROM notifications WHERE user_id=? "
                           "ORDER BY id DESC LIMIT 30", (g.user["id"],)))
    unread = d.execute("SELECT COUNT(*) FROM notifications WHERE user_id=? AND is_read=0", (g.user["id"],)).fetchone()[0]
    return jsonify(items=items, unread=unread)


@app.post("/api/notifications/read")
@need()
def notifications_read():
    dbm.get().execute("UPDATE notifications SET is_read=1 WHERE user_id=?", (g.user["id"],))
    return jsonify(ok=True)


@app.get("/api/dashboard")
@need()
def dashboard():
    u, d = g.user, dbm.get()
    cutoff = now()
    if u["role"] == "admin":
        return jsonify(role="admin", recent=rows(d.execute(
            "SELECT id,full_name,role,grade,section,created_at FROM users WHERE status='pending' "
            "ORDER BY created_at DESC LIMIT 6")))
    if u["role"] == "teacher":
        courses = rows(d.execute(COURSE_SQL + " WHERE c.teacher_id=? ORDER BY c.created_at DESC", (u["id"],)))
        to_grade = rows(d.execute(
            "SELECT a.id,a.title,c.id AS course_id,c.name AS course_name,COUNT(s.id) AS n FROM submissions s "
            "JOIN assignments a ON a.id=s.assignment_id JOIN courses c ON c.id=a.course_id "
            "WHERE c.teacher_id=? AND s.score IS NULL GROUP BY a.id ORDER BY MIN(s.submitted_at) LIMIT 8", (u["id"],)))
        upcoming = rows(d.execute(
            "SELECT a.id,a.title,a.due_at,c.id AS course_id,c.name AS course_name FROM assignments a "
            "JOIN courses c ON c.id=a.course_id WHERE c.teacher_id=? AND a.due_at>=? ORDER BY a.due_at LIMIT 5",
            (u["id"], cutoff)))
        students = d.execute("SELECT COUNT(DISTINCT e.student_id) FROM enrollments e JOIN courses c ON c.id=e.course_id "
                             "WHERE c.teacher_id=?", (u["id"],)).fetchone()[0]
        return jsonify(role="teacher", courses=courses, to_grade=to_grade, upcoming=upcoming, students=students)
    courses = rows(d.execute(COURSE_SQL + " WHERE c.id IN (SELECT course_id FROM enrollments WHERE student_id=?) "
                             "ORDER BY c.name", (u["id"],)))
    pending = rows(d.execute(
        "SELECT a.id,a.title,a.due_at,a.course_id,c.name AS course_name,c.color FROM assignments a "
        "JOIN courses c ON c.id=a.course_id JOIN enrollments e ON e.course_id=c.id AND e.student_id=? "
        "LEFT JOIN submissions s ON s.assignment_id=a.id AND s.student_id=? WHERE s.id IS NULL "
        "ORDER BY a.due_at IS NULL, a.due_at LIMIT 8", (u["id"], u["id"])))
    graded = rows(d.execute(
        "SELECT a.title,a.max_points,s.score,s.graded_at,c.name AS course_name,c.id AS course_id FROM submissions s "
        "JOIN assignments a ON a.id=s.assignment_id JOIN courses c ON c.id=a.course_id "
        "WHERE s.student_id=? AND s.score IS NOT NULL ORDER BY s.graded_at DESC LIMIT 5", (u["id"],)))
    news = rows(d.execute(
        "SELECT n.title,n.created_at,c.name AS course_name,c.id AS course_id FROM announcements n "
        "JOIN courses c ON c.id=n.course_id JOIN enrollments e ON e.course_id=c.id AND e.student_id=? "
        "ORDER BY n.id DESC LIMIT 5", (u["id"],)))
    return jsonify(role="student", courses=courses, pending=pending, graded=graded, news=news)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=config.PORT, debug=False)
