# Aula virtual · Politécnico Manuel Acevedo Serrano

Plataforma tipo Moodle para el centro (Fe y Alegría, Cutupú, La Vega).
Acceso **solo con Google** y aprobación de la dirección. Portales de administración, docente y estudiante.

## Cómo funciona el registro (y dónde quedan los datos)

```
1. Persona entra con Google ─► Google confirma su correo (el servidor lo verifica)
2. Si es nueva ─► llena su solicitud (estudiante: grado y sección · docente: área)
        └─ queda en la tabla  users  con status = "pending"      (base de datos)
3. El administrador ve la solicitud ─► Aprobar / Rechazar         (status = "approved")
4. La persona vuelve a entrar con Google ─► ya tiene su cuenta, sus cursos y su progreso
```

**Todo se guarda en una base de datos SQLite**: un solo archivo, `server/data/politecnico.db`
(sencillo, sin instalar nada, aguanta de sobra 800+ estudiantes con sus tareas y notas).
Los archivos que suben docentes y estudiantes quedan en `server/data/uploads/`.
Para respaldar: `python backup.py` (genera un .zip con todo).

| Tabla | Qué guarda |
|---|---|
| `users` | Correo de Google, nombre, rol (admin/docente/estudiante), grado, sección, **status** (pendiente/aprobado/rechazado/deshabilitado) |
| `courses` · `enrollments` | Cursos del docente y qué estudiantes están en cada uno |
| `lessons` · `announcements` | Clases (texto, enlace, video, archivo) y anuncios |
| `assignments` · `submissions` | Tareas, entregas de los estudiantes, nota y comentario |
| `final_grades` | Notas de los 4 períodos por estudiante y curso (final = promedio, aprobado desde 70) |
| `quizzes` · `quiz_questions` · `quiz_attempts` | Quizzes de selección múltiple, sus preguntas y las respuestas de cada estudiante (un intento, nota calculada por el servidor) |
| `attendance` | Asistencia por curso, estudiante y día (P presente · A ausente · T tardanza · E excusa) |
| `notifications` · `audit_log` | Avisos dentro de la plataforma y registro de acciones de dirección |

**Privacidad:** los estudiantes no tienen ninguna forma de ver la lista de otros estudiantes ni sus correos.
Los docentes sí buscan estudiantes **ya aprobados** por nombre, grado, sección o matrícula para agregarlos a sus clases.

## Qué puede hacer cada rol

- **Administración:** aprobar/rechazar solicitudes (una por una o varias), editar y deshabilitar usuarios, **cargar los ~800 estudiantes de golpe desde un CSV** (quedan aprobados), ver todos los cursos, recibir las calificaciones finales enviadas por los docentes y reabrirlas si hay que corregir, ver el registro de actividad.
- **Docente:** crear cursos, buscar y agregar estudiantes (uno por uno o un grado/sección completo), subir clases (texto, enlaces, videos de YouTube/Vimeo, PDF, Word, etc.), publicar anuncios, crear tareas con fecha límite, calificar entregas con comentarios, llenar las notas de 4 períodos y **enviar las calificaciones finales a dirección** (también CSV para Excel).
- **Estudiante:** ver sus cursos y clases, entregar tareas (texto + archivo), responder quizzes y ver su nota al instante con la corrección, ver su asistencia, ver notas y comentarios, y sus calificaciones finales cuando el docente las envía. Notificaciones en la campana.

### Funciones interactivas

- **Quizzes autocalificados:** el docente arma preguntas de 2 a 6 opciones con puntos; el estudiante tiene un intento, ve su porcentaje y la revisión pregunta por pregunta. La respuesta correcta nunca llega al navegador del estudiante antes de entregar. Si ya hay respuestas, las preguntas quedan bloqueadas para no alterar las notas.
- **Asistencia:** el docente marca P/A/T/E por día (o «Todos presentes»); cada estudiante ve su porcentaje. El % cuenta presentes y tardanzas, y las excusas no restan.
- **Boletín imprimible:** cada estudiante (y dirección, desde Usuarios) abre su boletín con materias, períodos, nota final, asistencia y situación, y lo imprime o guarda como PDF. Solo incluye materias cuyas notas el docente ya envió. Es un documento informativo, sin sello oficial.
- **Calendario** mensual con las fechas de entrega de tareas y quizzes, y **modo oscuro** (se recuerda por navegador).

## Puesta en marcha

Requiere Python 3.10+.

1. `cp .env.example .env` y completa los valores (ver abajo).
2. `./run.sh` (Linux/Mac) o `run.bat` (Windows). Abre `http://localhost:5000`.

### Configurar Google (una sola vez)

1. Entra a <https://console.cloud.google.com> → crea un proyecto → **APIs y servicios → Pantalla de consentimiento** (tipo *Externo*; si el centro tiene Google Workspace, *Interno*).
2. **Credenciales → Crear credenciales → ID de cliente de OAuth → Aplicación web**.
3. En **Orígenes de JavaScript autorizados** agrega la dirección de tu sitio (`https://aula.tucentro.edu.do`, y `http://localhost:5000` para pruebas).
4. Copia el *Client ID* en `.env` → `GOOGLE_CLIENT_ID=...`
5. Pon tu correo en `ADMIN_EMAILS=` : al entrar con Google esa cuenta es administradora automáticamente y desde ahí apruebas a los demás.

### Probar sin Google (solo en tu computadora)

```bash
DEV_LOGIN=1 ADMIN_EMAILS=admin.demo@example.com python server/seed.py --reset   # datos de ejemplo
DEV_LOGIN=1 ADMIN_EMAILS=admin.demo@example.com python server/app.py
```
Aparece un recuadro "Modo demostración" para entrar como administrador, docente o estudiante de ejemplo.
**En producción deja `DEV_LOGIN=0`** (ese modo permitiría entrar con cualquier correo).

### Publicarlo en internet

Necesita HTTPS para que Google funcione. Opciones: un VPS pequeño con Nginx + `run.sh` y Let's Encrypt, o servicios como Render/Railway/Fly.io (con un disco persistente apuntando a `DATA_DIR`). Con HTTPS pon `COOKIE_SECURE=1`.

## Estructura

```
web/        index.html (login) · app.html (plataforma)
  css/      base.css · login.css · app.css · views.css · dark.css
  js/       config · api · ui · theme · login · app (router) · views/{dashboard,courses,course,grades,admin,attendance,quizzes,calendar,report}.js
server/     app.py (API Flask) · db.py · schema.sql · config.py · seed.py
tests/      test_api.py · test_abuse.py   →  python -m pytest -q
            loadtest.py (carga: ~940 usuarios, 60 conectados a la vez)
```

## Seguridad incorporada

Verificación del token de Google en el servidor · sesión en cookie HttpOnly · protección CSRF (cabecera obligatoria) · permisos por rol y por curso en cada endpoint · consultas SQL parametrizadas · escape de HTML en toda la interfaz · lista blanca de tipos de archivo, con nombres aleatorios en disco · Content-Security-Policy · límites de tamaño.

### Pruebas de abuso y de carga (resultado)

- **`test_abuse.py`**: ~9 000 peticiones hostiles (ids gigantes, JSON anidado, cuerpos de 27 MB, inyección SQL, rutas `../`, cookies falsas, archivos `.html/.exe/.svg`, falta de cabecera CSRF) con cada rol sobre **todas** las rutas. Resultado: ningún error 500; un estudiante o docente ajeno a un curso nunca recibe datos de él.
- **`loadtest.py`**: 941 usuarios, 120 cursos, 15 000 entregas, 144 000 asistencias. Endpoints de 3–11 ms; 60 usuarios simultáneos ≈ 230 peticiones/s sin errores; 180 escrituras en paralelo y CSV de 1 500 filas sin problemas.
- Defectos encontrados y corregidos: doble envío simultáneo daba error 500 (ahora responde 409 claro), JSON anidado a propósito tumbaba la petición, fechas/ids enormes, botones «Reintentar» bloqueados por la política de seguridad y un `alert()` del navegador.

