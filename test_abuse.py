"""
Intenta romper la API: fuzzing de TODAS las rutas con cada rol, accesos indebidos entre cursos,
cuerpos hostiles, archivos peligrosos, recorrido de rutas y cookies falsas.   python -m pytest -q
"""
import io
import itertools
import json
import re

import pytest

from test_api import H, S, appmod, login, register_and_approve  # noqa: F401  (S es una fixture)

BIG = 99999999999999999999
JUNK_BODIES = [
    None, {}, [], "texto", 123, [[[]]], {"a": {"b": {"c": [1, 2, {"d": None}]}}},
    {"title": ["x"], "name": {"a": 1}, "rows": "x", "ids": {"a": 1}, "student_ids": "1", "status": {"x": 1}, "role": ["admin"],
     "score": "NaN", "max_points": "inf", "questions": "x", "answers": {"a": 1}, "grade": 5, "section": [], "url": 7,
     "day": 20260101, "email": ["a@b.c"], "credential": 5, "month": [], "p1": "x", "published": [], "color": 3},
    {"rows": [None, 1, "x", {"student_id": "x", "status": []}, {"student_id": BIG, "p1": 1e999}], "student_ids": [None, "a", BIG, -1],
     "ids": [BIG, "x", None], "answers": [BIG, "x", None, [], {}], "questions": [None, 1, {"text": 1, "options": "x", "correct": "y"}]},
    {"title": "A" * 100000, "description": "\x00‮" * 5000, "text": "😀" * 20000, "q": "' OR 1=1 --"},
]


_n = itertools.count(1)


@pytest.fixture()
def world(S):
    """Dos docentes, tres estudiantes, un curso con de todo, y administración."""
    k = next(_n)
    adm = S("admin")
    login(adm, "jefe@example.com", "Directora General")
    ta, ta_id = register_and_approve(S, f"abuse.ta@{k}.example.com", "Docente Alfa Uno", "teacher")
    tb, tb_id = register_and_approve(S, f"abuse.tb@{k}.example.com", "Docente Beta Dos", "teacher")
    s1, s1_id = register_and_approve(S, f"abuse.s1@{k}.example.com", "Estudiante Uno Perez", "student", grade="4to", section="A")
    s2, s2_id = register_and_approve(S, f"abuse.s2@{k}.example.com", "Estudiante Dos Perez", "student", grade="4to", section="A")
    out, out_id = register_and_approve(S, f"abuse.s3@{k}.example.com", "Estudiante Tres Fuera", "student", grade="4to", section="B")
    cid = ta.post("/api/courses", json={"name": "Curso Privado", "grade": "4to", "section": "A"}, headers=H).get_json()["id"]
    ta.post(f"/api/courses/{cid}/students", json={"student_ids": [s1_id, s2_id]}, headers=H)
    lid = ta.post(f"/api/courses/{cid}/lessons", json={"title": "Clase secreta", "description": "contenido"}, headers=H).get_json()["id"]
    aid = ta.post(f"/api/courses/{cid}/assignments", json={"title": "Tarea secreta"}, headers=H).get_json()["id"]
    Q = [{"text": "¿1+1?", "options": ["1", "2"], "correct": 1}]
    qid = ta.post(f"/api/courses/{cid}/quizzes", json={"title": "Quiz secreto", "questions": Q}, headers=H).get_json()["id"]
    ann = ta.post(f"/api/courses/{cid}/announcements", json={"title": "Aviso secreto"}, headers=H).get_json()["id"]
    s1.post(f"/api/assignments/{aid}/submit", data={"text": "mi respuesta privada", "file": (io.BytesIO(b"privado"), "p.txt")},
            content_type="multipart/form-data", headers=H)
    sub = ta.get(f"/api/assignments/{aid}/submissions").get_json()["items"][0]["submission_id"]
    ta.put(f"/api/courses/{cid}/attendance", json={"day": "2026-09-01", "rows": [{"student_id": s1_id, "status": "P"}]}, headers=H)
    yield dict(adm=adm, ta=ta, tb=tb, s1=s1, s2=s2, out=out, ids=dict(cid=cid, lid=lid, aid=aid, qid=qid, ann=ann, sid=sub, uid=s1_id, tid=ta_id))
    for f in appmod.config.UPLOADS.iterdir():
        f.unlink()


def api_rules():
    for r in appmod.app.url_map.iter_rules():
        if r.rule.startswith("/api/"):
            for m in sorted(r.methods - {"HEAD", "OPTIONS"}):
                yield r, m


def fill(rule, ids, value=None):
    """Sustituye <int:..> por ids reales (value=None) o por un valor raro."""
    real = {"cid": ids["cid"], "lid": ids["lid"], "aid": ids["aid"], "qid": ids["qid"], "uid": ids["uid"], "sid": ids["uid"]}
    out = rule.rule
    for conv, name in re.findall(r"<(?:int:)?(\w+)>", rule.rule) and [(None, n) for n in re.findall(r"<(?:int:)?(\w+)>", rule.rule)]:
        if value is not None:
            v = value
        elif rule.rule.startswith("/api/lessons") or name == "lid":
            v = ids["lid"]
        elif "announcements/" in rule.rule and name == "aid":
            v = ids["ann"]
        elif "/submissions/" in rule.rule and name == "sid":
            v = ids["sid"]
        elif "/students/" in rule.rule and "/courses/" in rule.rule and name == "sid":
            v = ids["uid"]
        elif "/assignments/" in rule.rule and name == "aid":
            v = ids["aid"]
        elif "/quizzes/" in rule.rule and name == "qid":
            v = ids["qid"]
        else:
            v = real.get(name, ids["cid"])
        out = re.sub(rf"<(?:int:)?{name}>", str(v), out, count=1)
    return out


def call(client, method, url, body):
    kw = {"headers": H}
    if isinstance(body, (dict, list, str, int)) and body is not None:
        kw["json"] = body
    return client.open(url, method=method, **kw)


# ---------------------------------------------------------------------------------------------
def test_fuzz_every_route_never_500(world):
    """Cada ruta × cada rol × valores absurdos × cuerpos hostiles: jamás debe responder 5xx."""
    clients = {k: world[k] for k in ("adm", "ta", "tb", "s1", "out")}
    clients["anon"] = appmod.app.test_client()
    ids = world["ids"]
    bad, n = [], 0
    for rule, method in api_rules():
        if rule.rule == "/api/logout":
            continue
        urls = [fill(rule, ids, v) for v in (0, -1, BIG, 2 ** 63)] if "<" in rule.rule else [rule.rule]
        real = fill(rule, ids)
        for who, c in clients.items():
            if who != "anon" and rule.rule.startswith(("/api/auth/", "/api/register")):
                continue  # estas rutas reinician la sesión del cliente; se prueban solo como anónimo
            privileged = who in ("adm", "ta")
            targets = list(urls)
            # sobre ids reales: lecturas siempre; escrituras destructivas solo con roles sin permiso
            if method in ("GET", "POST", "PUT", "PATCH") or not privileged:
                targets.append(real)
            for url, body in itertools.product(targets, JUNK_BODIES if method != "GET" else [None]):
                if privileged and url == real and (method == "DELETE" or (who == "adm" and "/admin/users/" in url and method != "GET")):
                    continue
                n += 1
                r = call(c, method, url + ("?q=%27%20OR%201=1--&month=9999-12&day=x&page=-5&per=99999999999999999999" if method == "GET" else ""), body)
                if r.status_code >= 500 and r.status_code != 503:
                    bad.append((who, method, url, str(body)[:50], r.status_code, r.get_data(as_text=True)[:80]))
    print(f"\n{n} peticiones hostiles enviadas")
    assert not bad, "\n".join(map(str, bad[:15])) + f"\n... {len(bad)} respuestas 5xx en total"


def test_outsiders_never_reach_private_course_data(world):
    """Un estudiante ajeno al curso y un docente de otro curso no deben recibir NINGÚN 2xx en rutas de ese curso."""
    ids = world["ids"]
    leaks = []
    for who in ("out", "tb"):
        c = world[who]
        for rule, method in api_rules():
            if "<" not in rule.rule or rule.rule.startswith("/api/admin") or rule.rule.startswith("/api/dev"):
                continue
            if who == "tb" and rule.rule.startswith("/api/students/"):
                continue
            url = fill(rule, ids)
            for body in (None, {}, JUNK_BODIES[8]):
                if method == "GET" and body is not None:
                    continue
                r = call(c, method, url, body)
                if r.status_code < 300:
                    leaks.append((who, method, url, r.status_code))
    assert not leaks, leaks


def test_enrolled_student_cannot_use_teacher_actions(world):
    ids = world["ids"]
    s = world["s2"]  # inscrito, pero no es el docente ni dueño de la entrega de s1
    allowed = ("/submit", "/attempt", "/api/notifications")
    leaks = []
    for rule, method in api_rules():
        if method == "GET" or "<" not in rule.rule or rule.rule.startswith(("/api/admin", "/api/dev")) or rule.rule.endswith(allowed):
            continue
        r = call(s, method, fill(rule, ids), JUNK_BODIES[8])
        if r.status_code < 300:
            leaks.append((method, rule.rule, r.status_code))
    assert not leaks, leaks
    # y tampoco puede ver la entrega, nota ni archivo de un compañero, ni las notas del curso
    assert s.get(f"/api/submissions/{ids['sid']}/file").status_code == 403
    assert s.get(f"/api/courses/{ids['cid']}/grades").status_code == 403
    assert s.get(f"/api/assignments/{ids['aid']}/submissions").status_code == 403
    assert "email" not in json.dumps(s.get(f"/api/courses/{ids['cid']}/students").get_json())


def test_anonymous_gets_401_everywhere(world):
    anon = appmod.app.test_client()
    public = {"/api/config", "/api/auth/google", "/api/auth/dev", "/api/dev/accounts", "/api/register", "/api/logout"}
    for rule, method in api_rules():
        if rule.rule in public:
            continue
        r = call(anon, method, fill(rule, world["ids"]), {})
        assert r.status_code in (401, 403), (method, rule.rule, r.status_code)


def test_forged_and_tampered_sessions(world):
    c = appmod.app.test_client()
    c.set_cookie("session", "eyJ1aWQiOjF9.AAAAAA.forged")
    assert c.get("/api/me").status_code == 401
    # cookie de otro usuario con una letra cambiada
    cookie = world["s1"].get_cookie("session").value
    c2 = appmod.app.test_client()
    c2.set_cookie("session", cookie[:-3] + ("AAA" if not cookie.endswith("AAA") else "BBB"))
    assert c2.get("/api/me").status_code == 401


def test_csrf_header_required_for_every_write(world):
    s = world["ta"]
    for rule, method in api_rules():
        if method in ("POST", "PUT", "PATCH", "DELETE") and rule.rule not in ("/api/logout",):
            r = s.open(fill(rule, world["ids"]), method=method, json={})
            assert r.status_code == 403, (method, rule.rule, r.status_code)


def test_path_traversal_and_private_files_not_served(world):
    c = appmod.app.test_client()
    for p in ["/../server/config.py", "/%2e%2e/server/app.py", "/css/..%2f..%2fserver/app.py", "/..%2fserver%2fapp.py",
              "/css/../../server/app.py", "/js/%2e%2e/%2e%2e/.env", "/.env", "/server/data/secret.key", "/politecnico.db",
              "/css/..\\..\\server\\app.py", "/%00", "/img/logo.png%00.html"]:
        r = c.get(p)
        body = r.get_data(as_text=True) if r.mimetype.startswith("text") or r.mimetype.endswith("json") else ""
        assert "def _secret" not in body and "SECRET_KEY" not in body and "CREATE TABLE" not in body, p
        assert r.status_code in (400, 404, 308, 405), (p, r.status_code)


def test_dangerous_uploads_rejected(world):
    ta, cid = world["ta"], world["ids"]["cid"]
    def up(name, data=b"x"):
        return ta.post(f"/api/courses/{cid}/lessons", data={"title": "Subida", "file": (io.BytesIO(data), name)},
                       content_type="multipart/form-data", headers=H)
    for bad in ["a.html", "a.htm", "a.svg", "a.exe", "a.php", "a.js.exe", "noext", "a.", ".htaccess", "a.pdf.html", "../../x.html", "a.xhtml", "a.sh", "a.bat"]:
        assert up(bad).status_code == 400, bad
    ok = up("../../../etc/passwd.pdf", b"%PDF-1.4")
    assert ok.status_code == 201  # se queda solo con el nombre base y guarda con nombre aleatorio
    stored = list(appmod.config.UPLOADS.iterdir())
    assert all(re.fullmatch(r"[0-9a-f]{32}\.\w+", p.name) for p in stored)
    lid = ok.get_json()["id"]
    r = world["s1"].get(f"/api/lessons/{lid}/file")
    assert r.status_code == 200 and r.headers["X-Content-Type-Options"] == "nosniff"
    assert "passwd.pdf" in r.headers.get("Content-Disposition", "passwd.pdf")
    # un "pdf" con HTML adentro sigue sirviéndose como pdf, nunca como html
    html_pdf = up("trampa.pdf", b"<html><script>alert(1)</script></html>").get_json()["id"]
    r = world["s1"].get(f"/api/lessons/{html_pdf}/file")
    assert r.mimetype == "application/pdf"


def test_hostile_bodies(world):
    adm = world["adm"]
    # JSON anidado a profundidad extrema (puede provocar RecursionError)
    deep = "[" * 100000 + "]" * 100000
    r = adm.post("/api/admin/users/import", data=deep, content_type="application/json", headers=H)
    assert r.status_code < 500, r.status_code
    # cuerpo gigante
    r = adm.post("/api/admin/users/import", data=b"x" * (27 * 1024 * 1024), content_type="application/json", headers=H)
    assert r.status_code == 413
    # JSON inválido, vacío, sin content-type
    for data, ct in [(b"{", "application/json"), (b"", "application/json"), (b"\xff\xfe\x00", "application/json"), (b"a=1", None)]:
        r = adm.post("/api/admin/users/import", data=data, content_type=ct, headers=H)
        assert r.status_code < 500


def test_injection_strings_are_data_only(world):
    adm = world["adm"]
    before = adm.get("/api/admin/users?q=zzzzqqq").get_json()["total"]
    for q in ["' OR '1'='1", "%", "_", "\\", "'; DROP TABLE users;--", "\" OR \"\"=\"", "%' OR 1=1 --"]:
        r = adm.get("/api/admin/users", query_string={"q": q})
        assert r.status_code == 200
    assert before == 0
    assert adm.get("/api/admin/users?per=5").get_json()["total"] > 5  # la tabla sigue existiendo
    assert adm.get("/api/admin/users?q=%25").get_json()["total"] == 0  # '%' se busca literal, no es comodín


def test_huge_ids_and_dates_are_clean_errors(world):
    ta = world["ta"]
    assert ta.get(f"/api/courses/{BIG}").status_code == 404
    assert ta.get("/api/calendar?month=9999-12").status_code in (200, 400)
    assert ta.get("/api/calendar?month=0001-01").status_code in (200, 400)
    assert ta.get(f"/api/courses/{world['ids']['cid']}/attendance?day=0001-01-01").status_code == 400
    assert ta.get("/api/admin/users?page=99999999999999999999&per=-1").status_code in (200, 403)
