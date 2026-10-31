"""Pruebas del API. Ejecutar:  python -m pytest -q"""
import io
import os
import sys
import tempfile

import pytest

_tmp = tempfile.mkdtemp()
os.environ.update(DATA_DIR=_tmp, DEV_LOGIN="1", ADMIN_EMAILS="jefe@example.com", GOOGLE_CLIENT_ID="", SECRET_KEY="test")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))

import app as appmod  # noqa: E402

H = {"X-Requested-With": "fetch"}


@pytest.fixture(scope="module")
def S():
    class Sessions:
        def __init__(self):
            self.clients = {}

        def __call__(self, who):
            if who not in self.clients:
                self.clients[who] = appmod.app.test_client()
            return self.clients[who]

    return Sessions()


def login(c, email, name="Persona de Prueba"):
    r = c.post("/api/auth/dev", json={"email": email, "name": name}, headers=H)
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def register_and_approve(S, email, name, role, **extra):
    c = S(email)
    assert login(c, email, name)["state"] == "register"
    r = c.post("/api/register", json={"role": role, "full_name": name, **extra}, headers=H)
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["state"] == "pending"
    # mientras está pendiente no puede entrar
    assert login(c, email, name)["state"] == "pending"
    assert c.get("/api/me").status_code == 401
    adm = S("admin")
    pend = adm.get("/api/admin/users?status=pending&q=" + email).get_json()["items"]
    assert len(pend) == 1
    r = adm.post(f"/api/admin/users/{pend[0]['id']}/status", json={"status": "approved"}, headers=H)
    assert r.status_code == 200
    assert login(c, email, name)["state"] == "approved"
    return c, pend[0]["id"]


def test_basics(S):
    c = S("anon")
    assert c.get("/api/me").status_code == 401
    assert c.get("/api/courses").status_code == 401
    assert c.get("/").status_code == 200 or c.get("/").status_code == 404  # index.html aún puede no existir
    assert c.post("/api/auth/dev", json={"email": "x@y.com"}).status_code == 403  # falta cabecera anti-CSRF
    assert c.get("/api/config").get_json()["devLogin"] is True
    assert c.get("/api/nope").status_code == 404
    assert c.post("/api/register", json={"role": "student"}, headers=H).status_code == 401


def test_admin_bootstrap(S):
    c = S("admin")
    d = login(c, "jefe@example.com", "Directora General")
    assert d["state"] == "approved" and d["user"]["role"] == "admin"
    assert c.get("/api/admin/stats").get_json()["pending"] == 0


def test_registration_validation(S):
    c = S("val")
    login(c, "val@example.com", "Val")
    assert c.post("/api/register", json={"role": "student", "full_name": "Ana", "grade": "5to", "section": "A"}, headers=H).status_code == 400
    assert c.post("/api/register", json={"role": "student", "full_name": "Ana Pérez", "grade": "9no", "section": "A"}, headers=H).status_code == 400
    assert c.post("/api/register", json={"role": "admin", "full_name": "Ana Pérez"}, headers=H).status_code == 400


def test_full_flow(S, tmp_path):
    teacher, tid = register_and_approve(S, "profe@example.com", "Ramón Báez Pérez", "teacher", specialty="Informática")
    stu, sid = register_and_approve(S, "est1@example.com", "Juan Pérez Santos", "student", grade="5to", section="A", id_number="M-1")
    stu2, sid2 = register_and_approve(S, "est2@example.com", "María Núñez Reyes", "student", grade="5to", section="B")
    other, _ = register_and_approve(S, "est3@example.com", "Luis Mejía Polanco", "student", grade="4to", section="A")

    # roles: estudiante no puede crear cursos ni usar el panel admin ni buscar estudiantes
    assert stu.post("/api/courses", json={"name": "Hack"}, headers=H).status_code == 403
    assert stu.get("/api/admin/stats").status_code == 403
    assert stu.get("/api/students").status_code == 403
    assert teacher.get("/api/admin/users").status_code == 403

    # docente crea curso
    r = teacher.post("/api/courses", json={"name": "Programación Web", "grade": "5to", "section": "A"}, headers=H)
    assert r.status_code == 201
    cid = r.get_json()["id"]
    assert teacher.post("/api/courses", json={"name": "ab"}, headers=H).status_code == 400

    # busca estudiantes y los agrega
    res = teacher.get("/api/students?q=Juan&grade=5to").get_json()
    assert [i["id"] for i in res["items"]] == [sid]
    assert teacher.post(f"/api/courses/{cid}/students", json={"student_ids": [sid]}, headers=H).get_json()["added"] == 1
    assert teacher.post(f"/api/courses/{cid}/students", json={"student_ids": [sid]}, headers=H).get_json()["added"] == 0
    g = teacher.post(f"/api/courses/{cid}/students/bulk", json={"grade": "5to", "section": "B"}, headers=H).get_json()
    assert g["added"] == 1
    assert teacher.get(f"/api/students?exclude_course={cid}&grade=5to").get_json()["total"] == 0
    assert len(stu.get("/api/courses").get_json()["items"]) == 1
    assert other.get("/api/courses").get_json()["items"] == []
    assert other.get(f"/api/courses/{cid}").status_code == 403
    assert stu.get(f"/api/courses/{cid}/students").get_json()["items"][0].get("email") is None  # sin datos privados

    # clases: texto, enlace malicioso, archivo no permitido, archivo válido, borrador oculto
    assert teacher.post(f"/api/courses/{cid}/lessons", json={"title": "Mala", "url": "javascript:alert(1)"}, headers=H).status_code == 400
    bad = teacher.post(f"/api/courses/{cid}/lessons", data={"title": "Ejecutable", "file": (io.BytesIO(b"MZ"), "virus.exe")},
                       content_type="multipart/form-data", headers=H)
    assert bad.status_code == 400
    ok = teacher.post(f"/api/courses/{cid}/lessons", data={"title": "Clase 1", "description": "Intro", "file": (io.BytesIO(b"%PDF-1.4 demo"), "guia.pdf")},
                      content_type="multipart/form-data", headers=H)
    assert ok.status_code == 201
    lid = ok.get_json()["id"]
    draft = teacher.post(f"/api/courses/{cid}/lessons", json={"title": "Borrador", "description": "oculto", "published": "0"}, headers=H)
    assert draft.status_code == 201
    yt = teacher.post(f"/api/courses/{cid}/lessons", json={"title": "Video", "url": "https://www.youtube.com/watch?v=abc"}, headers=H)
    assert yt.status_code == 201
    titles = [l["title"] for l in stu.get(f"/api/courses/{cid}/lessons").get_json()["items"]]
    assert titles == ["Clase 1", "Video"]
    assert len(teacher.get(f"/api/courses/{cid}/lessons").get_json()["items"]) == 3
    f = stu.get(f"/api/lessons/{lid}/file")
    assert f.status_code == 200 and f.data.startswith(b"%PDF")
    assert other.get(f"/api/lessons/{lid}/file").status_code == 403
    assert stu.get(f"/api/lessons/{draft.get_json()['id']}/file").status_code == 404
    assert stu.post(f"/api/courses/{cid}/lessons", json={"title": "Intruso", "description": "x"}, headers=H).status_code == 403

    # tareas y entregas
    r = teacher.post(f"/api/courses/{cid}/assignments", json={"title": "Tarea 1", "max_points": 50, "due_at": "2030-01-01T12:00:00Z"}, headers=H)
    assert r.status_code == 201
    aid = r.get_json()["id"]
    assert stu.post(f"/api/assignments/{aid}/submit", json={}, headers=H).status_code == 400
    sub = stu.post(f"/api/assignments/{aid}/submit",
                   data={"text": "Mi respuesta", "file": (io.BytesIO(b"hola"), "tarea.txt")},
                   content_type="multipart/form-data", headers=H)
    assert sub.status_code == 200
    assert other.post(f"/api/assignments/{aid}/submit", json={"text": "x"}, headers=H).status_code == 403
    assert stu.get(f"/api/assignments/{aid}/submissions").status_code == 403
    lst = teacher.get(f"/api/assignments/{aid}/submissions").get_json()["items"]
    assert len(lst) == 2 and lst[0]["student_id"] == sid and lst[1]["submission_id"] is None
    subid = lst[0]["submission_id"]
    assert teacher.get(f"/api/submissions/{subid}/file").data == b"hola"
    assert stu2.get(f"/api/submissions/{subid}/file").status_code == 403  # un compañero no ve entregas ajenas
    assert teacher.patch(f"/api/submissions/{subid}/grade", json={"score": 80}, headers=H).status_code == 400  # > máximo
    assert teacher.patch(f"/api/submissions/{subid}/grade", json={"score": 45, "feedback": "Bien"}, headers=H).status_code == 200
    mine = stu.get(f"/api/courses/{cid}/assignments").get_json()["items"][0]
    assert mine["score"] == 45 and mine["feedback"] == "Bien"
    assert stu.post(f"/api/assignments/{aid}/submit", json={"text": "cambio"}, headers=H).status_code == 409  # ya calificada

    # calificaciones finales
    gb = teacher.get(f"/api/courses/{cid}/grades").get_json()
    assert len(gb["items"]) == 2 and gb["status"] == "open"
    rows = [{"student_id": sid, "p1": 80, "p2": 90, "p3": 85, "p4": 95}, {"student_id": sid2, "p1": 60, "p2": 65, "p3": None, "p4": None}]
    assert teacher.put(f"/api/courses/{cid}/grades", json={"rows": [{"student_id": sid, "p1": 101}]}, headers=H).status_code == 400
    assert teacher.put(f"/api/courses/{cid}/grades", json={"rows": rows}, headers=H).get_json()["saved"] == 2
    miss = teacher.post(f"/api/courses/{cid}/grades/submit", headers=H)
    assert miss.status_code == 422 and miss.get_json()["missing"] == ["María Núñez Reyes"]
    assert stu.get("/api/my/grades").get_json()["items"][0]["final"] is None  # aún no publicadas
    rows[1].update(p3=70, p4=75)
    teacher.put(f"/api/courses/{cid}/grades", json={"rows": rows}, headers=H)
    assert teacher.post(f"/api/courses/{cid}/grades/submit", headers=H).status_code == 200
    mg = stu.get("/api/my/grades").get_json()["items"][0]
    assert mg["final"] == 88 and mg["published"] is True  # (80+90+85+95)/4 = 87.5 -> 88
    assert teacher.put(f"/api/courses/{cid}/grades", json={"rows": rows}, headers=H).status_code == 409
    csvr = teacher.get(f"/api/courses/{cid}/grades.csv")
    assert csvr.status_code == 200 and "Juan Pérez Santos" in csvr.data.decode("utf-8-sig")
    assert any(i["id"] == cid and i["grade_status"] == "submitted" for i in S("admin").get("/api/admin/grades").get_json()["items"])
    assert teacher.post(f"/api/admin/courses/{cid}/grades/reopen", headers=H).status_code == 403
    assert S("admin").post(f"/api/admin/courses/{cid}/grades/reopen", headers=H).status_code == 200
    assert teacher.put(f"/api/courses/{cid}/grades", json={"rows": rows}, headers=H).status_code == 200

    # notificaciones
    n = stu.get("/api/notifications").get_json()
    assert n["unread"] > 0
    stu.post("/api/notifications/read", headers=H)
    assert stu.get("/api/notifications").get_json()["unread"] == 0

    # dashboards
    assert teacher.get("/api/dashboard").get_json()["role"] == "teacher"
    assert stu.get("/api/dashboard").get_json()["role"] == "student"
    assert S("admin").get("/api/dashboard").get_json()["role"] == "admin"

    # quitar estudiante, borrar curso (borra archivos)
    assert teacher.delete(f"/api/courses/{cid}/students/{sid2}", headers=H).status_code == 200
    assert teacher.delete(f"/api/courses/{cid}", headers=H).status_code == 200
    assert stu.get(f"/api/courses/{cid}").status_code == 404
    assert list(appmod.config.UPLOADS.iterdir()) == []


def test_admin_tools(S):
    adm = S("admin")
    r = adm.post("/api/admin/users/import", json={"rows": [
        {"email": "imp1@example.com", "full_name": "Pedro Reyes Díaz", "grade": "6to", "section": "a"},
        {"email": "imp1@example.com", "full_name": "Duplicado Reyes", "grade": "6to", "section": "A"},
        {"email": "malo", "full_name": "Sin Correo", "grade": "6to"},
        {"email": "imp2@example.com", "full_name": "Grado Malo", "grade": "9no"},
    ]}, headers=H).get_json()
    assert r["created"] == 1 and r["skipped"] == 1 and r["error_count"] == 2
    imp = login(S("imp"), "imp1@example.com")
    assert imp["state"] == "approved" and imp["user"]["section"] == "A"
    # no puedes deshabilitarte a ti mismo; deshabilitar bloquea el acceso
    me = adm.get("/api/me").get_json()["user"]
    assert adm.post(f"/api/admin/users/{me['id']}/status", json={"status": "disabled"}, headers=H).status_code == 400
    uid = imp["user"]["id"]
    assert adm.post(f"/api/admin/users/{uid}/status", json={"status": "disabled"}, headers=H).status_code == 200
    assert S("imp").get("/api/me").status_code == 401
    assert login(S("imp2"), "imp1@example.com")["state"] == "disabled"
    assert len(adm.get("/api/admin/audit").get_json()["items"]) > 0
    assert adm.get("/api/admin/users?per=5&page=1").get_json()["total"] >= 5


def test_quizzes_attendance_report_calendar(S):
    teacher, _ = register_and_approve(S, "profe2@example.com", "Altagracia Ureña Mejía", "teacher")
    stu, sid = register_and_approve(S, "q1@example.com", "Rosa Almonte Díaz", "student", grade="6to", section="A")
    stu2, sid2 = register_and_approve(S, "q2@example.com", "Pedro Paulino Cruz", "student", grade="6to", section="A")
    outsider, _ = register_and_approve(S, "q3@example.com", "Luis Batista Peña", "student", grade="6to", section="B")
    cid = teacher.post("/api/courses", json={"name": "Matemática 6to", "grade": "6to", "section": "A"}, headers=H).get_json()["id"]
    teacher.post(f"/api/courses/{cid}/students/bulk", json={"grade": "6to", "section": "A"}, headers=H)

    # --- quiz: validaciones
    Q = [{"text": "2 + 2", "options": ["3", "", "4", "5"], "correct": 2, "points": 2},
         {"text": "Capital de RD", "options": ["Santiago", "Santo Domingo"], "correct": 1}]
    assert teacher.post(f"/api/courses/{cid}/quizzes", json={"title": "Q", "questions": Q}, headers=H).status_code == 400
    assert teacher.post(f"/api/courses/{cid}/quizzes", json={"title": "Quiz 1", "questions": []}, headers=H).status_code == 400
    bad = [{"text": "x?", "options": ["a", "b"], "correct": 5}]
    assert teacher.post(f"/api/courses/{cid}/quizzes", json={"title": "Quiz 1", "questions": bad}, headers=H).status_code == 400
    assert stu.post(f"/api/courses/{cid}/quizzes", json={"title": "Quiz 1", "questions": Q}, headers=H).status_code == 403
    r = teacher.post(f"/api/courses/{cid}/quizzes", json={"title": "Quiz 1", "questions": Q}, headers=H)
    assert r.status_code == 201
    qid = r.get_json()["id"]
    draft = teacher.post(f"/api/courses/{cid}/quizzes", json={"title": "Borrador", "questions": Q, "published": "0"}, headers=H).get_json()["id"]

    # el índice correcto sigue apuntando a "4" aunque se descartó una opción en blanco
    full = teacher.get(f"/api/quizzes/{qid}").get_json()
    assert full["questions"][0]["options"] == ["3", "4", "5"] and full["questions"][0]["correct"] == 1

    # --- el estudiante no ve las respuestas correctas, ni los borradores, ni quizzes ajenos
    v = stu.get(f"/api/quizzes/{qid}").get_json()
    assert all("correct" not in q for q in v["questions"]) and v["attempt"] is None
    assert [z["title"] for z in stu.get(f"/api/courses/{cid}/quizzes").get_json()["items"]] == ["Quiz 1"]
    assert stu.get(f"/api/quizzes/{draft}").status_code == 404
    assert outsider.get(f"/api/quizzes/{qid}").status_code == 403
    assert outsider.post(f"/api/quizzes/{qid}/attempt", json={"answers": [1, 1]}, headers=H).status_code == 403

    # --- respuestas: calificación en el servidor
    assert stu.post(f"/api/quizzes/{qid}/attempt", json={"answers": [1]}, headers=H).status_code == 400
    res = stu.post(f"/api/quizzes/{qid}/attempt", json={"answers": [1, 0]}, headers=H).get_json()
    assert res == {"score": 2.0, "max_score": 3.0}
    assert stu.post(f"/api/quizzes/{qid}/attempt", json={"answers": [1, 1]}, headers=H).status_code == 409  # un solo intento
    after = stu.get(f"/api/quizzes/{qid}").get_json()
    assert after["attempt"]["score"] == 2.0 and after["questions"][0]["correct"] == 1  # ahora sí ve la corrección
    assert teacher.post(f"/api/quizzes/{qid}/attempt", json={"answers": [1, 1]}, headers=H).status_code == 403
    out = teacher.get(f"/api/quizzes/{qid}/results").get_json()
    assert out["answered"] == 1 and out["average"] == 67 and len(out["items"]) == 2
    assert stu.get(f"/api/quizzes/{qid}/results").status_code == 403
    # con respuestas ya enviadas no se pueden cambiar las preguntas
    assert teacher.patch(f"/api/quizzes/{qid}", json={"questions": Q}, headers=H).status_code == 409
    assert teacher.patch(f"/api/quizzes/{qid}", json={"title": "Quiz uno"}, headers=H).status_code == 200
    # fecha límite vencida bloquea
    past = teacher.post(f"/api/courses/{cid}/quizzes", json={"title": "Vencido", "questions": Q, "due_at": "2020-01-01T00:00:00Z"}, headers=H).get_json()["id"]
    assert stu2.post(f"/api/quizzes/{past}/attempt", json={"answers": [1, 1]}, headers=H).status_code == 409
    assert stu2.get(f"/api/quizzes/{past}").get_json()["closed"] is True

    # --- asistencia
    assert teacher.put(f"/api/courses/{cid}/attendance", json={"day": "2099-01-01", "rows": []}, headers=H).status_code == 400
    assert teacher.put(f"/api/courses/{cid}/attendance", json={"day": "hoy", "rows": []}, headers=H).status_code == 400
    assert teacher.put(f"/api/courses/{cid}/attendance", json={"day": "2026-09-01", "rows": [{"student_id": sid, "status": "X"}]}, headers=H).status_code == 400
    for day, a, b in (("2026-09-01", "P", "A"), ("2026-09-02", "T", "E"), ("2026-09-03", "P", "P")):
        r = teacher.put(f"/api/courses/{cid}/attendance", json={"day": day, "rows": [
            {"student_id": sid, "status": a}, {"student_id": sid2, "status": b}, {"student_id": 999999, "status": "P"}]}, headers=H)
        assert r.get_json()["saved"] == 2  # el id ajeno al curso se ignora
    assert stu.put(f"/api/courses/{cid}/attendance", json={"day": "2026-09-01", "rows": []}, headers=H).status_code == 403
    day = teacher.get(f"/api/courses/{cid}/attendance?day=2026-09-02").get_json()
    assert {i["student_id"]: i["status"] for i in day["items"]} == {sid: "T", sid2: "E"} and len(day["days"]) == 3
    mine = stu.get(f"/api/courses/{cid}/attendance").get_json()
    assert mine["present"] == 2 and mine["late"] == 1 and mine["percent"] == 100
    other = stu2.get(f"/api/courses/{cid}/attendance").get_json()
    assert other["absent"] == 1 and other["excused"] == 1 and other["percent"] == 50  # 1 presente / (3 días - 1 excusa)
    assert outsider.get(f"/api/courses/{cid}/attendance").status_code == 403
    teacher.put(f"/api/courses/{cid}/attendance", json={"day": "2026-09-01", "rows": [{"student_id": sid2, "status": None}]}, headers=H)
    assert len(teacher.get(f"/api/courses/{cid}/attendance?day=2026-09-01").get_json()["items"]) == 2

    # --- boletín: solo notas publicadas, y solo el propio o el administrador
    rows_ = [{"student_id": sid, "p1": 90, "p2": 80, "p3": 85, "p4": 95}, {"student_id": sid2, "p1": 50, "p2": 60, "p3": 55, "p4": 65}]
    teacher.put(f"/api/courses/{cid}/grades", json={"rows": rows_}, headers=H)
    assert stu.get("/api/my/report").get_json()["items"][0]["final"] is None
    teacher.post(f"/api/courses/{cid}/grades/submit", headers=H)
    rep = stu.get("/api/my/report").get_json()
    assert rep["items"][0]["final"] == 88 and rep["average"] == 88 and rep["items"][0]["attendance"]["percent"] == 100
    assert rep["student"]["full_name"] == "Rosa Almonte Díaz"
    assert S("admin").get(f"/api/admin/students/{sid2}/report").get_json()["items"][0]["final"] == 58
    assert stu.get(f"/api/admin/students/{sid2}/report").status_code == 403
    assert teacher.get("/api/my/report").status_code == 403

    # --- calendario
    teacher.post(f"/api/courses/{cid}/assignments", json={"title": "Tarea sep", "due_at": "2026-09-15T16:00:00Z"}, headers=H)
    teacher.post(f"/api/courses/{cid}/quizzes", json={"title": "Quiz sep", "questions": Q, "due_at": "2026-09-20T16:00:00Z"}, headers=H)
    cal = stu.get("/api/calendar?month=2026-09").get_json()["items"]
    assert [(i["kind"], i["title"]) for i in cal] == [("task", "Tarea sep"), ("quiz", "Quiz sep")]
    assert outsider.get("/api/calendar?month=2026-09").get_json()["items"] == []
    assert len(teacher.get("/api/calendar?month=2026-09").get_json()["items"]) == 2
    assert stu.get("/api/calendar?month=hoy").status_code == 400

    # --- borrar curso limpia todo
    assert teacher.delete(f"/api/quizzes/{draft}", headers=H).status_code == 200
    assert teacher.delete(f"/api/courses/{cid}", headers=H).status_code == 200
    assert stu.get(f"/api/quizzes/{qid}").status_code == 404


def test_logout(S):
    c = S("admin")
    assert c.post("/api/logout", headers=H).status_code == 200
    assert c.get("/api/me").status_code == 401
