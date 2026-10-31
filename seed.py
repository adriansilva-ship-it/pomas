"""
Datos de DEMOSTRACIÓN para probar la plataforma (no usar en producción).
    python server/seed.py          -> crea datos si la base está vacía
    python server/seed.py --reset  -> borra la base y la vuelve a crear con datos de demo
Todos los correos son @example.com y solo funcionan con DEV_LOGIN=1.
"""
import random
import sys
from datetime import datetime, timedelta, timezone

import config
import db as dbm

NOMBRES = ["Juan", "María", "Luis", "Ana", "Carlos", "Rosa", "Miguel", "Laura", "José", "Carmen", "Pedro", "Yaneris",
           "Starlin", "Yulissa", "Wander", "Esmeralda", "Kelvin", "Darlenny", "Johan", "Génesis", "Brayan", "Nairobi"]
APELLIDOS = ["Rodríguez", "Pérez", "García", "Martínez", "Santos", "De la Cruz", "Núñez", "Reyes", "Peña", "Almonte",
             "Jiménez", "Fernández", "Tavárez", "Polanco", "Ureña", "Mejía", "Collado", "Paulino", "Rosario", "Batista"]


def iso(days=0, hours=0):
    return (datetime.now(timezone.utc) + timedelta(days=days, hours=hours)).isoformat(timespec="seconds")


def main(reset=False):
    if reset and config.DB_PATH.exists():
        for ext in ("", "-wal", "-shm"):
            p = config.DB_PATH.with_name(config.DB_PATH.name + ext)
            if p.exists():
                p.unlink()
    dbm.init()
    conn = dbm.connect()
    if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] > 0:
        print("La base ya tiene datos. Usa --reset para recrearla.")
        return
    rnd = random.Random(1963)
    now = iso()

    def user(email, name, role, status="approved", grade=None, section=None, idn=None):
        cur = conn.execute(
            "INSERT INTO users(email,full_name,role,status,id_number,grade,section,created_at,approved_at) "
            "VALUES(?,?,?,?,?,?,?,?,?)", (email, name, role, status, idn, grade, section, now,
                                           now if status == "approved" else None))
        return cur.lastrowid

    user("admin.demo@example.com", "Dirección Académica (Demo)", "admin")
    t1 = user("prof.informatica@example.com", "Ramón Antonio Báez", "teacher")
    t2 = user("prof.matematica@example.com", "Altagracia Mercedes Ureña", "teacher")
    t3 = user("prof.lengua@example.com", "Felipe de Jesús Cabrera", "teacher")

    students = {}
    n = 0
    for grade in ("4to", "5to", "6to"):
        for section in ("A", "B"):
            for _ in range(10):
                n += 1
                name = f"{rnd.choice(NOMBRES)} {rnd.choice(APELLIDOS)} {rnd.choice(APELLIDOS)}"
                sid = user(f"estudiante{n}@example.com", name, "student", grade=grade, section=section,
                           idn=f"MAS-{2026}{n:04d}")
                students.setdefault((grade, section), []).append(sid)
    user("pendiente.estudiante@example.com", "Nairobi Sofía Peña Collado", "student", "pending", "4to", "A")
    user("pendiente.estudiante2@example.com", "Kelvin Manuel Jiménez Tavárez", "student", "pending", "5to", "B")
    user("pendiente.docente@example.com", "Yulissa Altagracia Polanco", "teacher", "pending")

    def course(teacher, name, subject, grade, section, color, desc):
        return conn.execute(
            "INSERT INTO courses(teacher_id,name,subject,code,grade,section,school_year,description,color,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (teacher, name, subject, f"{subject[:3].upper()}-{grade[0]}{section}", grade, section, "2026-2027", desc,
             color, now)).lastrowid

    c1 = course(t1, "Programación Web", "Informática", "5to", "A", "#1c5aa8",
                "HTML, CSS, JavaScript y Python aplicados a proyectos reales.")
    c2 = course(t2, "Matemática", "Matemática", "5to", "A", "#e3242b", "Funciones, trigonometría y geometría analítica.")
    c3 = course(t3, "Lengua Española", "Lengua", "5to", "A", "#0f9d82", "Comprensión lectora y producción de textos.")
    for c in (c1, c2, c3):
        for sid in students[("5to", "A")]:
            conn.execute("INSERT INTO enrollments(course_id,student_id,added_at) VALUES(?,?,?)", (c, sid, now))

    lessons = [
        (c1, "Bienvenida y reglas del curso", "Conoce el programa, los criterios de evaluación y las herramientas que usaremos.", None),
        (c1, "Estructura de una página HTML", "Etiquetas básicas, semántica y accesibilidad. Practica con el video.", "https://www.youtube.com/watch?v=UB1O30fR-EE"),
        (c1, "Documentación de referencia (MDN)", "Consulta esta guía cuando tengas dudas.", "https://developer.mozilla.org/es/docs/Learn"),
        (c2, "Funciones lineales y cuadráticas", "Repaso de dominio, rango y gráficas.", None),
        (c3, "El texto argumentativo", "Tesis, argumentos y conclusión. Lee el ejemplo en clase.", None),
    ]
    for i, (cid, title, desc, url) in enumerate(lessons, 1):
        kind = "video" if url and "youtube" in url else ("link" if url else "text")
        conn.execute("INSERT INTO lessons(course_id,title,description,kind,url,published,position,created_at) "
                     "VALUES(?,?,?,?,?,1,?,?)", (cid, title, desc, kind, url, i, now))

    a1 = conn.execute("INSERT INTO assignments(course_id,title,instructions,due_at,max_points,created_at) VALUES(?,?,?,?,?,?)",
                      (c1, "Mi primera página personal", "Crea una página con tu presentación usando HTML y CSS. Sube el .zip.",
                       iso(days=5), 100, now)).lastrowid
    conn.execute("INSERT INTO assignments(course_id,title,instructions,due_at,max_points,created_at) VALUES(?,?,?,?,?,?)",
                 (c1, "Quiz: etiquetas HTML", "Responde en un párrafo qué hace cada etiqueta vista.", iso(days=-2), 20, now))
    conn.execute("INSERT INTO assignments(course_id,title,instructions,due_at,max_points,created_at) VALUES(?,?,?,?,?,?)",
                 (c2, "Práctica de funciones", "Resuelve los ejercicios 1 al 10 y sube una foto o PDF.", iso(days=3), 50, now))
    for k, sid in enumerate(students[("5to", "A")][:6]):
        conn.execute("INSERT INTO submissions(assignment_id,student_id,text,submitted_at,score,feedback,graded_at) "
                     "VALUES(?,?,?,?,?,?,?)",
                     (a1, sid, "Adjunto mi página. ¡Gracias profe!", iso(hours=-k - 3),
                      (rnd.randint(70, 98) if k < 3 else None), "Buen trabajo, cuida la ortografía." if k < 3 else None,
                      iso(hours=-1) if k < 3 else None))
    conn.execute("INSERT INTO announcements(course_id,author_id,title,body,created_at) VALUES(?,?,?,?,?)",
                 (c1, t1, "Traer laptop el jueves", "Trabajaremos en el laboratorio. Quien no tenga laptop avise con tiempo.", now))

    # Quiz de demostración (con algunas respuestas) y asistencia de la última semana
    import json
    qz = conn.execute("INSERT INTO quizzes(course_id,title,description,due_at,published,created_at) VALUES(?,?,?,?,1,?)",
                      (c1, "Quiz: fundamentos de HTML", "Selecciona la mejor respuesta. Solo tienes un intento.", iso(days=6), now)).lastrowid
    preguntas = [
        ("¿Qué etiqueta crea un enlace?", ["<link>", "<a>", "<href>", "<url>"], 1, 1),
        ("¿Cuál es la etiqueta del título principal de una página?", ["<h1>", "<title>", "<head>", "<big>"], 0, 1),
        ("¿Qué lenguaje da estilo (colores, tamaños) a una página web?", ["Python", "HTML", "CSS", "SQL"], 2, 2),
    ]
    for pos, (t, o, c, pts) in enumerate(preguntas):
        conn.execute("INSERT INTO quiz_questions(quiz_id,position,text,options,correct,points) VALUES(?,?,?,?,?,?)",
                     (qz, pos, t, json.dumps(o, ensure_ascii=False), c, pts))
    for k, sid in enumerate(students[("5to", "A")][:4]):
        ans = [1, 0, 2] if k % 2 == 0 else [1, 1, 0]
        sc = sum(p[3] for p, a in zip(preguntas, ans) if a == p[2])
        conn.execute("INSERT INTO quiz_attempts(quiz_id,student_id,answers,score,max_score,submitted_at) VALUES(?,?,?,?,?,?)",
                     (qz, sid, json.dumps(ans), sc, 4, iso(hours=-k - 2)))
    for back in range(1, 6):
        day = (datetime.now(timezone.utc) - timedelta(days=back)).date()
        if day.weekday() > 4:
            continue
        for sid in students[("5to", "A")]:
            st = rnd.choices(["P", "A", "T", "E"], weights=[82, 8, 7, 3])[0]
            conn.execute("INSERT INTO attendance(course_id,student_id,day,status) VALUES(?,?,?,?)", (c1, sid, day.isoformat(), st))
    conn.commit()
    conn.close()
    print(f"Datos de demostración creados en {config.DB_PATH}")
    print("Entra con DEV_LOGIN=1 usando: admin.demo@example.com | prof.informatica@example.com | estudiante1..60@example.com")


if __name__ == "__main__":
    main(reset="--reset" in sys.argv)
