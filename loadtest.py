"""
Prueba de carga y estrés (no la recoge pytest). Uso:   python tests/loadtest.py
Crea una base temporal con volumen realista (900 estudiantes, 40 docentes, 120 cursos, ~15 mil entregas,
~150 mil registros de asistencia), levanta el servidor y lo castiga con lecturas y escrituras en paralelo.
"""
import os
import random
import sqlite3
import statistics
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import requests

TMP = tempfile.mkdtemp()
os.environ.update(DATA_DIR=TMP, DEV_LOGIN="1", ADMIN_EMAILS="jefe@example.com", SECRET_KEY="load", GOOGLE_CLIENT_ID="")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
import app as appmod  # noqa: E402
import config  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

PORT = 5099
BASE = f"http://127.0.0.1:{PORT}"
H = {"X-Requested-With": "fetch"}
rnd = random.Random(7)
now = datetime.now(timezone.utc)
iso = lambda d=0: (now + timedelta(days=d)).isoformat(timespec="seconds")

# ------------------------------------------------------------------ poblar
t0 = time.time()
con = sqlite3.connect(config.DB_PATH)
con.execute("PRAGMA foreign_keys=ON")
N_T, N_S, N_C = 40, 900, 120
con.execute("INSERT INTO users(email,full_name,role,status,created_at,approved_at) VALUES('jefe@example.com','Directora','admin','approved',?,?)", (iso(), iso()))
teachers = [con.execute("INSERT INTO users(email,full_name,role,status,created_at,approved_at) VALUES(?,?,'teacher','approved',?,?)",
                        (f"t{i}@example.com", f"Docente Número {i}", iso(), iso())).lastrowid for i in range(N_T)]
students = []
for i in range(N_S):
    g, s = ["1ro", "2do", "3ro", "4to", "5to", "6to"][i % 6], "ABCD"[(i // 6) % 4]
    students.append((con.execute("INSERT INTO users(email,full_name,role,status,grade,section,id_number,created_at,approved_at) VALUES(?,?,'student','approved',?,?,?,?,?)",
                                 (f"e{i}@example.com", f"Estudiante Apellido{i} Segundo{i % 50}", g, s, f"M-{i:05d}", iso(), iso())).lastrowid, g, s))
courses = []
for i in range(N_C):
    c = con.execute("INSERT INTO courses(teacher_id,name,grade,section,school_year,created_at) VALUES(?,?,?,?,?,?)",
                    (teachers[i % N_T], f"Materia {i}", ["1ro", "2do", "3ro", "4to", "5to", "6to"][i % 6], "ABCD"[i % 4], "2026-2027", iso())).lastrowid
    courses.append(c)
enroll = {}
for c in courses:
    pick = rnd.sample(students, 30)
    enroll[c] = [p[0] for p in pick]
    con.executemany("INSERT INTO enrollments(course_id,student_id,added_at) VALUES(?,?,?)", [(c, sid, iso()) for sid, _, _ in pick])
    con.executemany("INSERT INTO lessons(course_id,title,description,kind,published,position,created_at) VALUES(?,?,?,'text',1,?,?)",
                    [(c, f"Clase {k}", "Contenido " * 20, k, iso()) for k in range(8)])
    for k in range(6):
        a = con.execute("INSERT INTO assignments(course_id,title,instructions,due_at,max_points,created_at) VALUES(?,?,?,?,100,?)",
                        (c, f"Tarea {k}", "Instrucciones " * 10, iso(k - 2), iso())).lastrowid
        con.executemany("INSERT INTO submissions(assignment_id,student_id,text,submitted_at,score,graded_at) VALUES(?,?,?,?,?,?)",
                        [(a, sid, "respuesta", iso(), rnd.randint(50, 100), iso()) for sid in enroll[c] if rnd.random() < .7])
    con.executemany("INSERT INTO attendance(course_id,student_id,day,status) VALUES(?,?,?,?)",
                    [(c, sid, (now - timedelta(days=d)).date().isoformat(), rnd.choice("PPPPPATE")) for sid in enroll[c] for d in range(40)])
    con.executemany("INSERT INTO final_grades(course_id,student_id,p1,p2,p3,p4,updated_at) VALUES(?,?,?,?,?,?,?)",
                    [(c, sid, rnd.randint(55, 100), rnd.randint(55, 100), None, None, iso()) for sid in enroll[c]])
    con.executemany("INSERT INTO notifications(user_id,text,created_at) VALUES(?,?,?)", [(sid, "Aviso de prueba", iso()) for sid in enroll[c][:10]])
con.commit()
tot = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("users", "courses", "enrollments", "assignments", "submissions", "attendance")}
con.close()
print(f"Base poblada en {time.time() - t0:.1f}s: {tot}  ({os.path.getsize(config.DB_PATH) / 1e6:.1f} MB)\n")

# ------------------------------------------------------------------ servidor
srv = make_server("127.0.0.1", PORT, appmod.app, threaded=True)
threading.Thread(target=srv.serve_forever, daemon=True).start()
time.sleep(0.5)


def session(email):
    s = requests.Session()
    s.headers.update(H)
    r = s.post(BASE + "/api/auth/dev", json={"email": email})
    assert r.json()["state"] == "approved", r.text
    return s


admin = session("jefe@example.com")
teacher = session("t0@example.com")
tcourse = next(c for c in courses if courses.index(c) % N_T == 0)
tc_students = enroll[tcourse]
stu_idx = [i for i, p in enumerate(students) if p[0] in tc_students][:30]
stu_sessions = [session(f"e{i}@example.com") for i in stu_idx]
stu = stu_sessions[0]
assign_id = appmod.dbm.connect().execute("SELECT id FROM assignments WHERE course_id=? LIMIT 1", (tcourse,)).fetchone()[0]

# ------------------------------------------------------------------ 1. latencia en frío (una a una)
ENDPOINTS = [
    ("admin  · estadísticas", admin, "GET", "/api/admin/stats"),
    ("admin  · usuarios (pág. 1)", admin, "GET", "/api/admin/users?per=25"),
    ("admin  · buscar usuario 'Segundo7'", admin, "GET", "/api/admin/users?q=Segundo7"),
    ("admin  · cursos", admin, "GET", "/api/admin/courses"),
    ("admin  · notas por curso", admin, "GET", "/api/admin/grades"),
    ("admin  · boletín de un estudiante", admin, "GET", f"/api/admin/students/{students[0][0]}/report"),
    ("docente· inicio", teacher, "GET", "/api/dashboard"),
    ("docente· buscar estudiantes 'Apellido1'", teacher, "GET", "/api/students?q=Apellido1"),
    ("docente· buscar por grado/sección", teacher, "GET", "/api/students?grade=5to&section=B"),
    ("docente· libro de notas (30 est.)", teacher, "GET", f"/api/courses/{tcourse}/grades"),
    ("docente· asistencia del día", teacher, "GET", f"/api/courses/{tcourse}/attendance"),
    ("docente· entregas de una tarea", teacher, "GET", f"/api/assignments/{assign_id}/submissions"),
    ("estudiante· inicio", stu, "GET", "/api/dashboard"),
    ("estudiante· mis tareas", stu, "GET", "/api/my/assignments"),
    ("estudiante· boletín", stu, "GET", "/api/my/report"),
    ("estudiante· calendario", stu, "GET", f"/api/calendar?month={now:%Y-%m}"),
]
print(f"{'Pantalla / endpoint':45s} {'ms':>7s}")
slow = []
for name, s, m, url in ENDPOINTS:
    t = time.perf_counter()
    r = s.request(m, BASE + url)
    ms = (time.perf_counter() - t) * 1000
    flag = "" if r.status_code == 200 and ms < 250 else "  <-- LENTO/ERROR"
    if flag:
        slow.append((name, ms, r.status_code))
    print(f"{name:45s} {ms:7.1f}  {r.status_code}{flag}")

# ------------------------------------------------------------------ 2. concurrencia de lectura
print("\nCarga: 60 usuarios simultáneos, 15 s, mezcla de pantallas...")
lat, errs = [], []
stop = time.time() + 15
sessions_pool = stu_sessions + [teacher, admin]


def worker(k):
    s = sessions_pool[k % len(sessions_pool)]
    mine = [e for e in ENDPOINTS if e[1] is s] or [e for e in ENDPOINTS if e[1] is stu]
    while time.time() < stop:
        _, _, m, url = rnd.choice(mine)
        t = time.perf_counter()
        try:
            r = s.request(m, BASE + url, timeout=30)
            if r.status_code != 200:
                errs.append((url, r.status_code, r.text[:100]))
        except Exception as e:  # noqa: BLE001
            errs.append((url, "EXC", str(e)[:100]))
        lat.append((time.perf_counter() - t) * 1000)


with ThreadPoolExecutor(60) as ex:
    list(ex.map(worker, range(60)))
lat.sort()
print(f"  {len(lat)} peticiones ({len(lat) / 15:.0f}/s) · p50 {statistics.median(lat):.0f} ms · p95 {lat[int(len(lat) * .95)]:.0f} ms · máx {lat[-1]:.0f} ms · errores {len(errs)}")
for e in errs[:5]:
    print("   ERR", e)

# ------------------------------------------------------------------ 3. escrituras simultáneas
print("\nEscrituras en paralelo (donde SQLite suele fallar con 'database is locked')...")
wr_err = []


def submit(s):
    r = s.post(BASE + f"/api/assignments/{assign_id}/submit", json={"text": "entrega concurrente"})
    return r.status_code, r.text[:90]


def save_att(i):
    rows_ = [{"student_id": sid, "status": rnd.choice("PATE")} for sid in tc_students]
    r = teacher.put(BASE + f"/api/courses/{tcourse}/attendance", json={"day": (now - timedelta(days=i % 5)).date().isoformat(), "rows": rows_})
    return r.status_code, r.text[:90]


def save_grades(i):
    rows_ = [{"student_id": sid, "p1": rnd.randint(50, 100), "p2": 80, "p3": 70, "p4": 90} for sid in tc_students]
    r = teacher.put(BASE + f"/api/courses/{tcourse}/grades", json={"rows": rows_})
    return r.status_code, r.text[:90]


def reg(i):
    c = requests.Session(); c.headers.update(H)
    c.post(BASE + "/api/auth/dev", json={"email": f"nuevo{i}@example.com", "name": f"Nuevo Usuario {i}"})
    r = c.post(BASE + "/api/register", json={"role": "student", "full_name": f"Nuevo Usuario {i}", "grade": "4to", "section": "A"})
    return r.status_code, r.text[:90]


jobs = [(submit, s) for s in stu_sessions] * 2 + [(save_att, i) for i in range(40)] + [(save_grades, i) for i in range(20)] + [(reg, i) for i in range(60)]
rnd.shuffle(jobs)
t = time.time()
with ThreadPoolExecutor(60) as ex:
    results = list(ex.map(lambda j: j[0](j[1]), jobs))
bad = [r for r in results if r[0] >= 500 or "locked" in r[1]]
codes = {}
for c_, _ in results:
    codes[c_] = codes.get(c_, 0) + 1
print(f"  {len(jobs)} escrituras en {time.time() - t:.1f}s · códigos {codes} · fallos de servidor/bloqueo: {len(bad)}")
for b in bad[:5]:
    print("   ", b)
chk = sqlite3.connect(config.DB_PATH)
nsub = chk.execute("SELECT COUNT(*) FROM submissions WHERE assignment_id=? AND text='entrega concurrente'", (assign_id,)).fetchone()[0]
print(f"  Integridad: entregas guardadas={nsub} (esperado ≤ 30, una por estudiante) · pendientes registrados={chk.execute(chr(83)+'ELECT COUNT(*) FROM users WHERE status=' + chr(39)+'pending'+chr(39)).fetchone()[0]} (esperado 60)")
print("  PRAGMA integrity_check:", chk.execute("PRAGMA integrity_check").fetchone()[0])

# ------------------------------------------------------------------ 4. importación masiva
print("\nImportación de 1500 filas por CSV...")
rows_ = [{"email": f"imp{i}@gmail.com", "full_name": f"Importado Numero {i}", "grade": "3ro", "section": "B"} for i in range(1500)]
t = time.time()
r = admin.post(BASE + "/api/admin/users/import", json={"rows": rows_})
print(f"  {r.status_code} en {time.time() - t:.1f}s -> {r.json() if r.status_code == 200 else r.text[:120]}")

print("\nRESUMEN:", "TODO BIEN" if not (slow or errs or bad) else f"HALLAZGOS -> lentos/errores: {slow} · errores lectura: {len(errs)} · escrituras fallidas: {len(bad)}")
os._exit(0)
