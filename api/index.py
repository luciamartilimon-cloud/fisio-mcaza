import http.server
import json
import hashlib
import os
import urllib.parse
from datetime import datetime

# Driver PostgreSQL puro en Python (sin binarios C, compatible 100% con Vercel Serverless)
import pg8000.native

# Conexión persistente a Postgres en Vercel
def get_db():
    pg_url = os.environ.get("POSTGRES_URL") or os.environ.get("DATABASE_URL")
    if pg_url:
        parsed = urllib.parse.urlparse(pg_url)
        return pg8000.native.Connection(
            user=parsed.username,
            password=parsed.password,
            host=parsed.hostname,
            port=parsed.port or 5432,
            database=parsed.path.lstrip("/"),
            ssl_context=True
        )
    else:
        # Fallback a credenciales directas de Vercel/Neon
        return pg8000.native.Connection(
            user=os.environ.get("PGUSER", "default"),
            password=os.environ.get("PGPASSWORD", ""),
            host=os.environ.get("PGHOST", "localhost"),
            port=int(os.environ.get("PGPORT", 5432)),
            database=os.environ.get("PGDATABASE", "verceldb"),
            ssl_context=True
        )

def hash_pw(password):
    return hashlib.sha256(password.encode("utf-8")).hexdigest()

_DB_INITIALIZED = False

def init_db():
    global _DB_INITIALIZED
    if _DB_INITIALIZED:
        return
    conn = get_db()
    
    conn.run("""
    CREATE TABLE IF NOT EXISTS users (
        id SERIAL PRIMARY KEY,
        name TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'jugadora',
        approved INTEGER NOT NULL DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    conn.run("""
    CREATE TABLE IF NOT EXISTS sessions (
        token TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    conn.run("""
    CREATE TABLE IF NOT EXISTS patients (
        id SERIAL PRIMARY KEY,
        name TEXT NOT NULL,
        dorsal INTEGER DEFAULT 0,
        position TEXT DEFAULT 'Jugadora',
        status TEXT DEFAULT 'Disponible',
        phone TEXT DEFAULT '',
        antecedents TEXT DEFAULT '',
        physio_notes TEXT DEFAULT '',
        public_instruction TEXT DEFAULT '',
        linked_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    conn.run("""
    CREATE TABLE IF NOT EXISTS visits (
        id SERIAL PRIMARY KEY,
        patient_id INTEGER NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
        date TEXT NOT NULL,
        status_after TEXT DEFAULT '',
        diagnosis TEXT NOT NULL,
        treatment TEXT DEFAULT '',
        private_notes TEXT DEFAULT '',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    conn.run("""
    CREATE TABLE IF NOT EXISTS exercises (
        id SERIAL PRIMARY KEY,
        patient_id INTEGER NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        sets_reps TEXT NOT NULL,
        frequency TEXT DEFAULT '',
        instructions TEXT DEFAULT '',
        video_url TEXT DEFAULT '',
        completed INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # Sembrar paciente de prueba inicial si la tabla está vacía
    res = conn.run("SELECT COUNT(*) FROM patients;")
    if res and res[0][0] == 0:
        ins = conn.run("""
        INSERT INTO patients (name, dorsal, position, status, antecedents, physio_notes, public_instruction)
        VALUES (
            'Inés Fernández', 14, 'Centrocampista', 'En readaptación',
            'Esguince grado II en temporada 2024. Rotura fibrilar menor aductor hace 6 meses.',
            'Cierta reticencia en cambios de ritmo. Sin dolor en camilla. Buena evolución.',
            'Hielo 15 min al acabar gym. Rutina diaria de propiocepción antes de dormir.'
        ) RETURNING id;
        """)
        if ins:
            p_id = ins[0][0]
            conn.run("""
            INSERT INTO visits (patient_id, date, status_after, diagnosis, treatment, private_notes)
            VALUES (:pid, '2026-11-15', 'En readaptación', 'Revisión semana 3 post-esguince tobillo derecho',
                    'Terapia manual miofascial en peroneos y tríceps sural. Movilizaciones de astrágalo.',
                    'Ganados 5 grados de flexión dorsal. Se autoriza carrera continua.');
            """, pid=p_id)
            conn.run("""
            INSERT INTO exercises (patient_id, name, sets_reps, frequency, instructions, video_url, completed)
            VALUES (:pid, 'Equilibrio monopodal en toalla / inestable', '3 series x 45 seg', 'Diario',
                    'Mirada al frente, rodilla ligeramente flexionada sin tambalear.',
                    'https://www.youtube.com/watch?v=dQw4w9WgXcQ', 1);
            """, pid=p_id)
            conn.run("""
            INSERT INTO exercises (patient_id, name, sets_reps, frequency, instructions, video_url, completed)
            VALUES (:pid, 'Excéntrico de gemelos en escalón', '3 series x 12 reps', 'Días alternos',
                    'Subir con dos pies, bajar lento con pierna derecha en 3 segundos.', '', 0);
            """, pid=p_id)

    conn.close()
    _DB_INITIALIZED = True

class handler(http.server.BaseHTTPRequestHandler):
    def send_json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.end_headers()
        self.wfile.write(json.dumps(data, default=str).encode("utf-8"))

    def send_csv(self, filename, content):
        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(content.encode("utf-8-sig"))

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.end_headers()

    def get_user_from_auth(self):
        init_db()
        auth = self.headers.get("Authorization", "")
        token = ""
        if auth.startswith("Bearer "):
            token = auth.split("Bearer ")[1].strip()
        else:
            parsed = urllib.parse.urlparse(self.path)
            query = urllib.parse.parse_qs(parsed.query)
            token = query.get("token", [""])[0]

        if not token:
            return None

        conn = get_db()
        rows = conn.run("""
            SELECT u.id, u.name, u.email, u.role, u.approved
            FROM sessions s
            JOIN users u ON s.user_id = u.id
            WHERE s.token = :tok;
        """, tok=token)
        conn.close()
        if rows:
            r = rows[0]
            return {"id": r[0], "name": r[1], "email": r[2], "role": r[3], "approved": r[4]}
        return None

    def read_json_body(self):
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length > 0:
            raw = self.rfile.read(content_length).decode("utf-8")
            return json.loads(raw)
        return {}

    def do_GET(self):
        init_db()
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        user = self.get_user_from_auth()

        if path == "/api/status":
            conn = get_db()
            count = conn.run("SELECT COUNT(*) FROM users WHERE role = 'fisio_admin';")[0][0]
            conn.close()
            return self.send_json({"has_fisio": count > 0, "authenticated": bool(user), "user": user})

        # Endpoint secreto de reseteo inicial limpio
        if path == "/api/system/reset-users":
            conn = get_db()
            conn.run("DELETE FROM sessions;")
            conn.run("DELETE FROM users;")
            conn.close()
            return self.send_json({"message": "Usuarios reseteados para el registro inicial de la fisio real"})

        if not user:
            return self.send_json({"error": "No autorizado. Inicia sesión."}, 401)

        if path == "/api/me":
            return self.send_json({"user": user})

        if path == "/api/users":
            if user["role"] != "fisio_admin":
                return self.send_json({"error": "Acceso denegado"}, 403)
            conn = get_db()
            rows = conn.run("SELECT id, name, email, role, approved, created_at FROM users ORDER BY created_at DESC;")
            conn.close()
            users = [{"id": r[0], "name": r[1], "email": r[2], "role": r[3], "approved": r[4], "created_at": r[5]} for r in rows]
            return self.send_json({"users": users})

        if path == "/api/patients":
            conn = get_db()
            if user["role"] == "fisio_admin":
                rows = conn.run("""
                    SELECT p.id, p.name, p.dorsal, p.position, p.status, p.phone, p.antecedents, p.physio_notes, p.public_instruction, p.linked_user_id,
                           u.email as linked_email, u.approved as user_approved
                    FROM patients p
                    LEFT JOIN users u ON p.linked_user_id = u.id
                    ORDER BY p.name ASC;
                """)
                conn.close()
                patients = [{
                    "id": r[0], "name": r[1], "dorsal": r[2], "position": r[3], "status": r[4],
                    "phone": r[5], "antecedents": r[6], "physio_notes": r[7], "public_instruction": r[8],
                    "linked_user_id": r[9], "linked_email": r[10], "user_approved": r[11]
                } for r in rows]
                return self.send_json({"patients": patients})
            else:
                rows = conn.run("""
                    SELECT id, name, dorsal, position, status, public_instruction
                    FROM patients
                    WHERE linked_user_id = :uid;
                """, uid=user["id"])
                conn.close()
                if rows:
                    r = rows[0]
                    p = {"id": r[0], "name": r[1], "dorsal": r[2], "position": r[3], "status": r[4], "public_instruction": r[5]}
                    return self.send_json({"patient": p})
                return self.send_json({"patient": None})

        if path.startswith("/api/patients/") and not path.endswith("/export"):
            parts = path.strip("/").split("/")
            if len(parts) == 3 and parts[2].isdigit():
                pid = int(parts[2])
                conn = get_db()
                if user["role"] == "fisio_admin":
                    p_rows = conn.run("""
                        SELECT p.id, p.name, p.dorsal, p.position, p.status, p.phone, p.antecedents, p.physio_notes, p.public_instruction, p.linked_user_id, u.email as linked_email
                        FROM patients p LEFT JOIN users u ON p.linked_user_id = u.id
                        WHERE p.id = :pid;
                    """, pid=pid)
                    if not p_rows:
                        conn.close()
                        return self.send_json({"error": "Paciente no encontrada"}, 404)
                    r = p_rows[0]
                    pat = {"id": r[0], "name": r[1], "dorsal": r[2], "position": r[3], "status": r[4], "phone": r[5], "antecedents": r[6], "physio_notes": r[7], "public_instruction": r[8], "linked_user_id": r[9], "linked_email": r[10]}

                    v_rows = conn.run("SELECT id, patient_id, date, status_after, diagnosis, treatment, private_notes FROM visits WHERE patient_id = :pid ORDER BY date DESC, id DESC;", pid=pid)
                    visits = [{"id": v[0], "patient_id": v[1], "date": v[2], "status_after": v[3], "diagnosis": v[4], "treatment": v[5], "private_notes": v[6]} for v in v_rows]

                    e_rows = conn.run("SELECT id, patient_id, name, sets_reps, frequency, instructions, video_url, completed FROM exercises WHERE patient_id = :pid ORDER BY id DESC;", pid=pid)
                    exercises = [{"id": e[0], "patient_id": e[1], "name": e[2], "sets_reps": e[3], "frequency": e[4], "instructions": e[5], "video_url": e[6], "completed": bool(e[7])} for e in e_rows]
                    conn.close()
                    return self.send_json({"patient": pat, "visits": visits, "exercises": exercises})
                else:
                    p_rows = conn.run("SELECT id, name, dorsal, position, status, public_instruction FROM patients WHERE id = :pid AND linked_user_id = :uid;", pid=pid, uid=user["id"])
                    if not p_rows:
                        conn.close()
                        return self.send_json({"error": "No autorizado"}, 403)
                    r = p_rows[0]
                    pat = {"id": r[0], "name": r[1], "dorsal": r[2], "position": r[3], "status": r[4], "public_instruction": r[5]}

                    v_rows = conn.run("SELECT id, date, status_after, diagnosis, treatment FROM visits WHERE patient_id = :pid ORDER BY date DESC;", pid=pid)
                    visits = [{"id": v[0], "date": v[1], "status_after": v[2], "diagnosis": v[3], "treatment": v[4]} for v in v_rows]

                    e_rows = conn.run("SELECT id, name, sets_reps, frequency, instructions, video_url, completed FROM exercises WHERE patient_id = :pid ORDER BY id DESC;", pid=pid)
                    exercises = [{"id": e[0], "name": e[1], "sets_reps": e[2], "frequency": e[3], "instructions": e[4], "video_url": e[5], "completed": bool(e[6])} for e in e_rows]
                    conn.close()
                    return self.send_json({"patient": pat, "visits": visits, "exercises": exercises})

        if path == "/api/export/visits.csv":
            if user["role"] != "fisio_admin":
                return self.send_json({"error": "Acceso denegado"}, 403)
            
            patient_filter = query.get("patient_id", [None])[0]
            conn = get_db()
            if patient_filter and patient_filter.isdigit():
                rows = conn.run("""
                    SELECT v.date, p.name as patient_name, p.dorsal, v.status_after, v.diagnosis, v.treatment, v.private_notes
                    FROM visits v JOIN patients p ON v.patient_id = p.id
                    WHERE p.id = :pid ORDER BY v.date DESC;
                """, pid=int(patient_filter))
                pname = "paciente_" + patient_filter
            else:
                rows = conn.run("""
                    SELECT v.date, p.name as patient_name, p.dorsal, v.status_after, v.diagnosis, v.treatment, v.private_notes
                    FROM visits v JOIN patients p ON v.patient_id = p.id
                    ORDER BY v.date DESC, p.name ASC;
                """)
                pname = "todas_las_visitas"
            conn.close()

            csv_lines = ['"Fecha","Paciente","Dorsal","Estado Tras Visita","Diagnostico / Motivo","Tratamiento en Camilla","Notas Confidenciales Fisio"']
            for r in rows:
                def esc(val):
                    return '"' + str(val or '').replace('"', '""') + '"'
                csv_lines.append(f"{esc(r[0])},{esc(r[1])},{esc(r[2])},{esc(r[3])},{esc(r[4])},{esc(r[5])},{esc(r[6])}")

            csv_data = "\n".join(csv_lines)
            filename = f"fisioteam_historial_{pname}_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
            return self.send_csv(filename, csv_data)

        return self.send_json({"error": "Ruta no encontrada"}, 404)

    def do_POST(self):
        init_db()
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        body = self.read_json_body()

        if path == "/api/register":
            name = body.get("name", "").strip()
            email = body.get("email", "").strip().lower()
            password = body.get("password", "")

            if not name or not email or not password:
                return self.send_json({"error": "Todos los campos son obligatorios"}, 400)

            conn = get_db()
            existing = conn.run("SELECT id FROM users WHERE email = :email;", email=email)
            if existing:
                conn.close()
                return self.send_json({"error": "Ya existe una cuenta con este correo"}, 400)

            is_first = (conn.run("SELECT COUNT(*) FROM users;")[0][0] == 0)
            role = "fisio_admin" if is_first else "jugadora"
            approved = 1 if is_first else 0

            pw_hash = hash_pw(password)
            user_id = conn.run("""
                INSERT INTO users (name, email, password_hash, role, approved)
                VALUES (:name, :email, :pw, :role, :appr) RETURNING id;
            """, name=name, email=email, pw=pw_hash, role=role, appr=approved)[0][0]

            if role == "jugadora":
                conn.run("UPDATE patients SET linked_user_id = :uid WHERE lower(phone) = :email;", uid=user_id, email=email)

            token = hashlib.sha256(f"{user_id}_{email}_{datetime.now().isoformat()}".encode("utf-8")).hexdigest()
            user_info = {"id": user_id, "name": name, "email": email, "role": role, "approved": approved}
            conn.run("INSERT INTO sessions (token, user_id) VALUES (:tok, :uid);", tok=token, uid=user_id)
            conn.close()

            return self.send_json({
                "message": "Registro completado",
                "token": token,
                "user": user_info,
                "is_first": is_first
            })

        if path == "/api/login":
            email = body.get("email", "").strip().lower()
            password = body.get("password", "")

            conn = get_db()
            rows = conn.run("SELECT id, name, email, password_hash, role, approved FROM users WHERE email = :email;", email=email)

            if not rows or rows[0][3] != hash_pw(password):
                conn.close()
                return self.send_json({"error": "Correo o contraseña incorrectos"}, 401)

            u = rows[0]
            token = hashlib.sha256(f"{u[0]}_{email}_{datetime.now().isoformat()}".encode("utf-8")).hexdigest()
            user_info = {"id": u[0], "name": u[1], "email": u[2], "role": u[4], "approved": u[5]}
            conn.run("INSERT INTO sessions (token, user_id) VALUES (:tok, :uid);", tok=token, uid=u[0])
            conn.close()
            return self.send_json({"token": token, "user": user_info})

        if path == "/api/logout":
            auth = self.headers.get("Authorization", "")
            if auth.startswith("Bearer "):
                token = auth.split("Bearer ")[1].strip()
                conn = get_db()
                conn.run("DELETE FROM sessions WHERE token = :tok;", tok=token)
                conn.close()
            return self.send_json({"message": "Sesión cerrada"})

        user = self.get_user_from_auth()
        if not user:
            return self.send_json({"error": "No autorizado"}, 401)

        if path == "/api/users/update-role":
            if user["role"] != "fisio_admin":
                return self.send_json({"error": "Acceso denegado"}, 403)
            target_id = body.get("user_id")
            new_role = body.get("role")
            new_approved = 1 if body.get("approved") else 0
            conn = get_db()
            conn.run("UPDATE users SET role = :r, approved = :a WHERE id = :id;", r=new_role, a=new_approved, id=target_id)
            conn.close()
            return self.send_json({"message": "Usuario actualizado"})

        if path == "/api/patients":
            if user["role"] != "fisio_admin":
                return self.send_json({"error": "Acceso denegado"}, 403)
            name = body.get("name", "").strip()
            dorsal = int(body.get("dorsal") or 0)
            position = body.get("position", "Jugadora")
            status = body.get("status", "Disponible")
            antecedents = body.get("antecedents", "")
            phone = body.get("phone", "")
            linked_email = body.get("linked_email", "").strip().lower()

            conn = get_db()
            linked_user_id = None
            if linked_email:
                u = conn.run("SELECT id FROM users WHERE email = :email;", email=linked_email)
                if u:
                    linked_user_id = u[0][0]

            pid = conn.run("""
                INSERT INTO patients (name, dorsal, position, status, phone, antecedents, physio_notes, public_instruction, linked_user_id)
                VALUES (:name, :dorsal, :position, :status, :phone, :antecedents, 'Ficha creada. Pendiente de valoración.', 'Completar rutina activa.', :luid)
                RETURNING id;
            """, name=name, dorsal=dorsal, position=position, status=status, phone=phone, antecedents=antecedents, luid=linked_user_id)[0][0]
            conn.close()
            return self.send_json({"message": "Paciente creada", "id": pid})

        if path.startswith("/api/patients/") and not path.endswith("/visits") and not path.endswith("/exercises"):
            parts = path.strip("/").split("/")
            if len(parts) == 3 and parts[2].isdigit():
                pid = int(parts[2])
                if user["role"] != "fisio_admin":
                    return self.send_json({"error": "Acceso denegado"}, 403)

                conn = get_db()
                fields = []
                params = {"pid": pid}
                for k in ["name", "dorsal", "position", "status", "phone", "antecedents", "physio_notes", "public_instruction"]:
                    if k in body:
                        fields.append(f"{k} = :{k}")
                        params[k] = body[k]

                if "linked_email" in body:
                    le = body["linked_email"].strip().lower()
                    if le:
                        u = conn.run("SELECT id FROM users WHERE email = :em;", em=le)
                        fields.append("linked_user_id = :luid")
                        params["luid"] = u[0][0] if u else None
                    else:
                        fields.append("linked_user_id = NULL")

                if fields:
                    conn.run(f"UPDATE patients SET {', '.join(fields)} WHERE id = :pid;", **params)
                conn.close()
                return self.send_json({"message": "Paciente actualizada"})

        if path.startswith("/api/patients/") and path.endswith("/delete"):
            pid = int(path.split("/")[3])
            if user["role"] != "fisio_admin":
                return self.send_json({"error": "Acceso denegado"}, 403)
            conn = get_db()
            conn.run("DELETE FROM patients WHERE id = :pid;", pid=pid)
            conn.close()
            return self.send_json({"message": "Paciente y todo su historial eliminados"})

        if path.startswith("/api/patients/") and path.endswith("/visits"):
            pid = int(path.split("/")[3])
            if user["role"] != "fisio_admin":
                return self.send_json({"error": "Acceso denegado"}, 403)
            
            date = body.get("date", datetime.now().strftime("%Y-%m-%d"))
            status_after = body.get("status_after", "En readaptación")
            diagnosis = body.get("diagnosis", "")
            treatment = body.get("treatment", "")
            private_notes = body.get("private_notes", "")

            conn = get_db()
            conn.run("""
                INSERT INTO visits (patient_id, date, status_after, diagnosis, treatment, private_notes)
                VALUES (:pid, :date, :status_after, :diagnosis, :treatment, :private_notes);
            """, pid=pid, date=date, status_after=status_after, diagnosis=diagnosis, treatment=treatment, private_notes=private_notes)
            conn.run("UPDATE patients SET status = :status WHERE id = :pid;", status=status_after, pid=pid)
            conn.close()
            return self.send_json({"message": "Visita registrada"})

        if path.startswith("/api/visits/") and path.endswith("/delete"):
            vid = int(path.split("/")[3])
            if user["role"] != "fisio_admin":
                return self.send_json({"error": "Acceso denegado"}, 403)
            conn = get_db()
            conn.run("DELETE FROM visits WHERE id = :vid;", vid=vid)
            conn.close()
            return self.send_json({"message": "Visita eliminada"})

        if path.startswith("/api/patients/") and path.endswith("/exercises"):
            pid = int(path.split("/")[3])
            if user["role"] != "fisio_admin":
                return self.send_json({"error": "Acceso denegado"}, 403)
            
            conn = get_db()
            conn.run("""
                INSERT INTO exercises (patient_id, name, sets_reps, frequency, instructions, video_url)
                VALUES (:pid, :name, :sets_reps, :frequency, :instructions, :video_url);
            """, pid=pid, name=body.get("name"), sets_reps=body.get("sets_reps"), frequency=body.get("frequency", ""), instructions=body.get("instructions", ""), video_url=body.get("video_url", ""))
            conn.close()
            return self.send_json({"message": "Ejercicio añadido"})

        if path.startswith("/api/exercises/") and path.endswith("/toggle"):
            eid = int(path.split("/")[3])
            conn = get_db()
            conn.run("UPDATE exercises SET completed = CASE WHEN completed = 1 THEN 0 ELSE 1 END WHERE id = :eid;", eid=eid)
            conn.close()
            return self.send_json({"message": "Estado de ejercicio cambiado"})

        if path.startswith("/api/exercises/") and path.endswith("/delete"):
            eid = int(path.split("/")[3])
            if user["role"] != "fisio_admin":
                return self.send_json({"error": "Acceso denegado"}, 403)
            conn = get_db()
            conn.run("DELETE FROM exercises WHERE id = :eid;", eid=eid)
            conn.close()
            return self.send_json({"message": "Ejercicio eliminado"})

        return self.send_json({"error": "Ruta no encontrada"}, 404)
