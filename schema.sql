-- Base de datos de la plataforma del Politécnico Manuel Acevedo Serrano (SQLite)
-- Fechas en ISO-8601 UTC (texto). Se muestran en hora de República Dominicana en el navegador.

CREATE TABLE IF NOT EXISTS users (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    email        TEXT NOT NULL UNIQUE COLLATE NOCASE,
    full_name    TEXT NOT NULL,
    picture      TEXT,
    role         TEXT NOT NULL CHECK (role IN ('admin','teacher','student')),
    status       TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected','disabled')),
    id_number    TEXT,          -- cédula o matrícula
    grade        TEXT,          -- 1ro ... 6to (estudiantes)
    section      TEXT,          -- A, B, C ...
    specialty    TEXT,          -- mención / área técnica (opcional)
    phone        TEXT,
    reject_reason TEXT,
    created_at   TEXT NOT NULL,
    approved_at  TEXT,
    approved_by  INTEGER REFERENCES users(id) ON DELETE SET NULL,
    last_login   TEXT
);
CREATE INDEX IF NOT EXISTS idx_users_status_role ON users(status, role);
CREATE INDEX IF NOT EXISTS idx_users_grade ON users(grade, section);
CREATE INDEX IF NOT EXISTS idx_users_name ON users(full_name COLLATE NOCASE);

CREATE TABLE IF NOT EXISTS courses (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    teacher_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name         TEXT NOT NULL,
    subject      TEXT,
    code         TEXT,
    grade        TEXT,
    section      TEXT,
    school_year  TEXT,
    description  TEXT,
    color        TEXT NOT NULL DEFAULT '#1c5aa8',
    grade_status TEXT NOT NULL DEFAULT 'open' CHECK (grade_status IN ('open','submitted')),
    grades_submitted_at TEXT,
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_courses_teacher ON courses(teacher_id);

CREATE TABLE IF NOT EXISTS enrollments (
    course_id  INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    student_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    added_at   TEXT NOT NULL,
    PRIMARY KEY (course_id, student_id)
);
CREATE INDEX IF NOT EXISTS idx_enroll_student ON enrollments(student_id);

CREATE TABLE IF NOT EXISTS lessons (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id   INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    title       TEXT NOT NULL,
    description TEXT,
    kind        TEXT NOT NULL DEFAULT 'text',   -- text | link | video | file
    url         TEXT,
    file_name   TEXT,
    file_path   TEXT,
    published   INTEGER NOT NULL DEFAULT 1,
    position    INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_lessons_course ON lessons(course_id, position);

CREATE TABLE IF NOT EXISTS assignments (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id    INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    title        TEXT NOT NULL,
    instructions TEXT,
    due_at       TEXT,
    max_points   REAL NOT NULL DEFAULT 100,
    file_name    TEXT,
    file_path    TEXT,
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_assign_course ON assignments(course_id);

CREATE TABLE IF NOT EXISTS submissions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    assignment_id INTEGER NOT NULL REFERENCES assignments(id) ON DELETE CASCADE,
    student_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    text          TEXT,
    file_name     TEXT,
    file_path     TEXT,
    submitted_at  TEXT NOT NULL,
    score         REAL,
    feedback      TEXT,
    graded_at     TEXT,
    UNIQUE (assignment_id, student_id)
);
CREATE INDEX IF NOT EXISTS idx_sub_student ON submissions(student_id);

CREATE TABLE IF NOT EXISTS announcements (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id  INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    author_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title      TEXT NOT NULL,
    body       TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ann_course ON announcements(course_id, created_at);

-- Calificaciones finales: cuatro períodos, escala 0-100, aprobado desde 70
CREATE TABLE IF NOT EXISTS final_grades (
    course_id  INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    student_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    p1 REAL, p2 REAL, p3 REAL, p4 REAL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (course_id, student_id)
);

CREATE TABLE IF NOT EXISTS notifications (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    text       TEXT NOT NULL,
    link       TEXT,
    is_read    INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notif_user ON notifications(user_id, is_read, id);

CREATE TABLE IF NOT EXISTS audit_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_id   INTEGER REFERENCES users(id) ON DELETE SET NULL,
    action     TEXT NOT NULL,
    detail     TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_id ON audit_log(id);

-- Asistencia: P presente, A ausente, T tardanza, E excusa
CREATE TABLE IF NOT EXISTS attendance (
    course_id  INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    student_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    day        TEXT NOT NULL,
    status     TEXT NOT NULL CHECK (status IN ('P','A','T','E')),
    PRIMARY KEY (course_id, student_id, day)
);
CREATE INDEX IF NOT EXISTS idx_att_student ON attendance(student_id);

-- Quizzes de selección múltiple, calificados automáticamente (un intento por estudiante)
CREATE TABLE IF NOT EXISTS quizzes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id   INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    title       TEXT NOT NULL,
    description TEXT,
    due_at      TEXT,
    published   INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_quiz_course ON quizzes(course_id);

CREATE TABLE IF NOT EXISTS quiz_questions (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    quiz_id  INTEGER NOT NULL REFERENCES quizzes(id) ON DELETE CASCADE,
    position INTEGER NOT NULL DEFAULT 0,
    text     TEXT NOT NULL,
    options  TEXT NOT NULL,          -- JSON: ["A","B","C"]
    correct  INTEGER NOT NULL,       -- índice de la opción correcta
    points   REAL NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_qq_quiz ON quiz_questions(quiz_id, position);

CREATE TABLE IF NOT EXISTS quiz_attempts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    quiz_id      INTEGER NOT NULL REFERENCES quizzes(id) ON DELETE CASCADE,
    student_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    answers      TEXT NOT NULL,      -- JSON: [2,0,null,...]
    score        REAL NOT NULL,
    max_score    REAL NOT NULL,
    submitted_at TEXT NOT NULL,
    UNIQUE (quiz_id, student_id)
);
CREATE INDEX IF NOT EXISTS idx_qa_student ON quiz_attempts(student_id);
