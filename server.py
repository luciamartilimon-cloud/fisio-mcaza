#!/usr/bin/env python3
import http.server
import socketserver
import json
import sqlite3
import hashlib
import os
import urllib.parse
from datetime import datetime

PORT = 3344
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fisioteam.db")
STATIC_DIR = os.path.dirname(os.path.abspath(__file__))

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def hash_pw(password):
    return hashlib.sha256(password.encode("utf-8")).hexdigest()

def init_db():
    conn = get_db()
    c = conn.cursor()
    # Tabla de Usuarios
    c.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'jugadora', -- 'fisio_admin' | 'jugadora'
        approved INTEGER NOT NULL DEFAULT 0,  -- 1 if can access
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # Tabla de Sesiones Persistentes (tokens que duran meses)
    c.execute("""
    CREATE TABLE IF NOT EXISTS sessions (
        token TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    );
    """)

    # Tabla de Pacientes
    c.execute("""
    CREATE TABLE IF NOT EXISTS patients (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        dorsal INTEGER DEFAULT 0,
        position TEXT DEFAULT 'Jugadora',
        status TEXT DEFAULT 'Disponible',
        phone TEXT DEFAULT '',
        antecedents TEXT DEFAULT '',
        physio_notes TEXT DEFAULT '',
        public_instruction TEXT DEFAULT '',
        linked_user_id INTEGER,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (linked_user_id) REFERENCES users(id) ON DELETE SET NULL
    );
    """)

    # Tabla de Visitas
    c.execute("""
    CREATE TABLE IF NOT EXISTS visits (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        patient_id INTEGER NOT NULL,
        date TEXT NOT NULL,
        status_after TEXT DEFAULT '',
        diagnosis TEXT NOT NULL,
        treatment TEXT DEFAULT '',
        private_notes TEXT DEFAULT '',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (patient_id) REFERENCES patients(id) ON DELETE CASCADE
    );
    """)

    # Tabla de Ejercicios
    c.execute("""
    CREATE TABLE IF NOT EXISTS exercises (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        patient_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        sets_reps TEXT NOT NULL,
        frequency TEXT DEFAULT '',
        instructions TEXT DEFAULT '',
        video_url TEXT DEFAULT '',
        completed INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (patient_id) REFERENCES patients(id) ON DELETE CASCADE
    );
    """)

    # Sembrar paciente de ejemplo si la tabla está vacía
    c.execute("SELECT COUNT(*) as count FROM patients;")
    if c.fetchone()["count"] == 0:
        c.execute("""
        INSERT INTO patients (name, dorsal, position, status, antecedents, physio_notes, public_instruction)
        VALUES (
            'Inés Fernández', 14, 'Centrocampista', 'En readaptación',
            'Esguince grado II en temporada 2024. Rotura fibrilar menor aductor hace 6 meses.',
            'Cierta reticencia en cambios de ritmo. Sin dolor en camilla. Buena evolución.',
            'Hielo 15 min al acabar gym. Rutina diaria de propiocepción antes de dormir.'
        );
        """)
        p_id = c.lastrowid
        c.execute("""
        INSERT INTO visits (patient_id, date, status_after, diagnosis, treatment, private_notes)
        VALUES (?, '2026-11-15', 'En readaptación', 'Revisión semana 3 post-esguince tobillo derecho',
                'Terapia manual miofascial en peroneos y tríceps sural. Movilizaciones de astrágalo.',
                'Ganados 5 grados de flexión dorsal. Se autoriza carrera continua.');
        """, (p_id,))
        c.execute("""
        INSERT INTO exercises (patient_id, name, sets_reps, frequency, instructions, video_url, completed)
        VALUES (?, 'Equilibrio monopodal en toalla / inestable', '3 series x 45 seg', 'Diario',
                'Mirada al frente, rodilla ligeramente flexionada sin tambalear.',
                'https://www.youtube.com/watch?v=dQw4w9WgXcQ', 1);
        """, (p_id,))
        c.execute("""
        INSERT INTO exercises (patient_id, name, sets_reps, frequency, instructions, video_url, completed)
        VALUES (?, 'Excéntrico de gemelos en escalón', '3 series x 12 reps', 'Días alternos',
                'Subir con dos pies, bajar lento con pierna derecha en 3 segundos.', '', 0);
        """, (p_id,))

    conn.commit()
    conn.close()

# Token simple en memoria: token -> user_dict
SESSIONS = {}

class FisioHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=STATIC_DIR, **kwargs)

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
        auth = self.headers.get("Authorization", "")
        token = ""
        if auth.startswith("Bearer "):
            token = auth.split("Bearer ")[1].strip()
        else:
            # Soportar token por query param (para descargas directas de CSV)
            parsed = urllib.parse.urlparse(self.path)
            query = urllib.parse.parse_qs(parsed.query)
            token = query.get("token", [""])[0]

        if not token:
            return None

        # Consultar en DB persistente
        conn = get_db()
        c = conn.cursor()
        c.execute("""
            SELECT u.id, u.name, u.email, u.role, u.approved
            FROM sessions s
            JOIN users u ON s.user_id = u.id
            WHERE s.token = ?;
        """, (token,))
        row = c.fetchone()
        conn.close()
        return dict(row) if row else None

    def read_json_body(self):
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length > 0:
            raw = self.rfile.read(content_length).decode("utf-8")
            return json.loads(raw)
        return {}

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        # RUTAS DE API
        if path.startswith("/api/"):
            user = self.get_user_from_auth()

            # Estado del sistema (si hay fisio registrada)
            if path == "/api/status":
                conn = get_db()
                c = conn.cursor()
                c.execute("SELECT COUNT(*) as count FROM users WHERE role = 'fisio_admin';")
                has_fisio = c.fetchone()["count"] > 0
                conn.close()
                return self.send_json({"has_fisio": has_fisio, "authenticated": bool(user), "user": user})

            # Requiere login a partir de aquí
            if not user:
                return self.send_json({"error": "No autorizado. Inicia sesión."}, 401)

            # Perfil actual
            if path == "/api/me":
                return self.send_json({"user": user})

            # Listado de usuarios (Solo Fisio)
            if path == "/api/users":
                if user["role"] != "fisio_admin":
                    return self.send_json({"error": "Acceso denegado"}, 403)
                conn = get_db()
                c = conn.cursor()
                c.execute("SELECT id, name, email, role, approved, created_at FROM users ORDER BY created_at DESC;")
                rows = [dict(r) for r in c.fetchall()]
                conn.close()
                return self.send_json({"users": rows})

            # Listado de pacientes
            if path == "/api/patients":
                conn = get_db()
                c = conn.cursor()
                if user["role"] == "fisio_admin":
                    c.execute("""
                        SELECT p.*, u.email as linked_email, u.approved as user_approved
                        FROM patients p
                        LEFT JOIN users u ON p.linked_user_id = u.id
                        ORDER BY p.name ASC;
                    """)
                    patients = [dict(r) for r in c.fetchall()]
                    conn.close()
                    return self.send_json({"patients": patients})
                else:
                    # Jugadora solo ve su propia ficha sin notas privadas
                    c.execute("""
                        SELECT id, name, dorsal, position, status, public_instruction
                        FROM patients
                        WHERE linked_user_id = ?;
                    """, (user["id"],))
                    row = c.fetchone()
                    conn.close()
                    return self.send_json({"patient": dict(row) if row else None})

            # Detalle de una paciente
            if path.startswith("/api/patients/") and not path.endswith("/export"):
                parts = path.strip("/").split("/")
                if len(parts) == 3 and parts[2].isdigit():
                    pid = int(parts[2])
                    conn = get_db()
                    c = conn.cursor()
                    if user["role"] == "fisio_admin":
                        c.execute("SELECT p.*, u.email as linked_email FROM patients p LEFT JOIN users u ON p.linked_user_id = u.id WHERE p.id = ?;", (pid,))
                        pat = c.fetchone()
                        if not pat:
                            conn.close()
                            return self.send_json({"error": "Paciente no encontrada"}, 404)
                        c.execute("SELECT * FROM visits WHERE patient_id = ? ORDER BY date DESC, id DESC;", (pid,))
                        visits = [dict(r) for r in c.fetchall()]
                        c.execute("SELECT * FROM exercises WHERE patient_id = ? ORDER BY id DESC;", (pid,))
                        exercises = [dict(r) for r in c.fetchall()]
                        conn.close()
                        return self.send_json({"patient": dict(pat), "visits": visits, "exercises": exercises})
                    else:
                        # Jugadora consultando
                        c.execute("SELECT * FROM patients WHERE id = ? AND linked_user_id = ?;", (pid, user["id"]))
                        pat = c.fetchone()
                        if not pat:
                            conn.close()
                            return self.send_json({"error": "No autorizado"}, 403)
                        c.execute("SELECT id, date, status_after, diagnosis, treatment FROM visits WHERE patient_id = ? ORDER BY date DESC;", (pid,))
                        visits = [dict(r) for r in c.fetchall()]
                        c.execute("SELECT * FROM exercises WHERE patient_id = ? ORDER BY id DESC;", (pid,))
                        exercises = [dict(r) for r in c.fetchall()]
                        conn.close()
                        res_p = {
                            "id": pat["id"], "name": pat["name"], "dorsal": pat["dorsal"],
                            "position": pat["position"], "status": pat["status"],
                            "public_instruction": pat["public_instruction"]
                        }
                        return self.send_json({"patient": res_p, "visits": visits, "exercises": exercises})

            # EXPORTAR CSV
            if path == "/api/export/visits.csv":
                if user["role"] != "fisio_admin":
                    return self.send_json({"error": "Acceso denegado"}, 403)
                
                patient_filter = query.get("patient_id", [None])[0]
                conn = get_db()
                c = conn.cursor()
                if patient_filter and patient_filter.isdigit():
                    c.execute("""
                        SELECT v.date, p.name as patient_name, p.dorsal, v.status_after, v.diagnosis, v.treatment, v.private_notes
                        FROM visits v
                        JOIN patients p ON v.patient_id = p.id
                        WHERE p.id = ?
                        ORDER BY v.date DESC;
                    """, (int(patient_filter),))
                    pname = "paciente_" + patient_filter
                else:
                    c.execute("""
                        SELECT v.date, p.name as patient_name, p.dorsal, v.status_after, v.diagnosis, v.treatment, v.private_notes
                        FROM visits v
                        JOIN patients p ON v.patient_id = p.id
                        ORDER BY v.date DESC, p.name ASC;
                    """)
                    pname = "todas_las_visitas"
                
                rows = c.fetchall()
                conn.close()

                # Generar CSV con comillas escapadas
                csv_lines = ['"Fecha","Paciente","Dorsal","Estado Tras Visita","Diagnostico / Motivo","Tratamiento en Camilla","Notas Confidenciales Fisio"']
                for r in rows:
                    def esc(val):
                        return '"' + str(val or '').replace('"', '""') + '"'
                    csv_lines.append(f"{esc(r['date'])},{esc(r['patient_name'])},{esc(r['dorsal'])},{esc(r['status_after'])},{esc(r['diagnosis'])},{esc(r['treatment'])},{esc(r['private_notes'])}")

                csv_data = "\n".join(csv_lines)
                filename = f"fisioteam_historial_{pname}_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
                return self.send_csv(filename, csv_data)

        # Si no es API, servir archivos estáticos (index.html, etc.)
        if path == "/":
            self.path = "/index.html"
        return super().do_GET()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        body = self.read_json_body()

        # REGISTRO
        if path == "/api/register":
            name = body.get("name", "").strip()
            email = body.get("email", "").strip().lower()
            password = body.get("password", "")

            if not name or not email or not password:
                return self.send_json({"error": "Todos los campos son obligatorios"}, 400)

            conn = get_db()
            c = conn.cursor()
            c.execute("SELECT id FROM users WHERE email = ?;", (email,))
            if c.fetchone():
                conn.close()
                return self.send_json({"error": "Ya existe una cuenta con este correo"}, 400)

            # Comprobar si es el primer usuario del sistema
            c.execute("SELECT COUNT(*) as count FROM users;")
            is_first = (c.fetchone()["count"] == 0)

            role = "fisio_admin" if is_first else "jugadora"
            approved = 1 if is_first else 0 # La fisio tiene acceso inmediato; las jugadoras esperan aprobación

            pw_hash = hash_pw(password)
            c.execute("""
                INSERT INTO users (name, email, password_hash, role, approved)
                VALUES (?, ?, ?, ?, ?);
            """, (name, email, pw_hash, role, approved))
            user_id = c.lastrowid
            conn.commit()

            # Si es jugadora y hay un paciente creado con este email exacto, vincularlo
            if role == "jugadora":
                c.execute("UPDATE patients SET linked_user_id = ? WHERE lower(phone) = ?;", (user_id, email))
                conn.commit()

            conn.close()

            # Crear token de sesión y guardar en DB persistente
            token = hashlib.sha256(f"{user_id}_{email}_{datetime.now().isoformat()}".encode("utf-8")).hexdigest()
            user_info = {"id": user_id, "name": name, "email": email, "role": role, "approved": approved}
            
            conn = get_db()
            c = conn.cursor()
            c.execute("INSERT INTO sessions (token, user_id) VALUES (?, ?);", (token, user_id))
            conn.commit()
            conn.close()

            return self.send_json({
                "message": "Registro completado",
                "token": token,
                "user": user_info,
                "is_first": is_first
            })

        # LOGIN
        if path == "/api/login":
            email = body.get("email", "").strip().lower()
            password = body.get("password", "")

            conn = get_db()
            c = conn.cursor()
            c.execute("SELECT * FROM users WHERE email = ?;", (email,))
            user = c.fetchone()
            conn.close()

            if not user or user["password_hash"] != hash_pw(password):
                return self.send_json({"error": "Correo o contraseña incorrectos"}, 401)

            token = hashlib.sha256(f"{user['id']}_{email}_{datetime.now().isoformat()}".encode("utf-8")).hexdigest()
            user_info = {
                "id": user["id"],
                "name": user["name"],
                "email": user["email"],
                "role": user["role"],
                "approved": user["approved"]
            }
            conn = get_db()
            c = conn.cursor()
            c.execute("INSERT INTO sessions (token, user_id) VALUES (?, ?);", (token, user["id"]))
            conn.commit()
            conn.close()
            return self.send_json({"token": token, "user": user_info})

        # CERRAR SESIÓN
        if path == "/api/logout":
            auth = self.headers.get("Authorization", "")
            if auth.startswith("Bearer "):
                token = auth.split("Bearer ")[1].strip()
                conn = get_db()
                c = conn.cursor()
                c.execute("DELETE FROM sessions WHERE token = ?;", (token,))
                conn.commit()
                conn.close()
            return self.send_json({"message": "Sesión cerrada"})

        # RUTAS QUE REQUIEREN LOGIN
        user = self.get_user_from_auth()
        if not user:
            return self.send_json({"error": "No autorizado"}, 401)

        # GESTIÓN DE USUARIOS (SOLO FISIO)
        if path == "/api/users/update-role":
            if user["role"] != "fisio_admin":
                return self.send_json({"error": "Acceso denegado"}, 403)
            target_id = body.get("user_id")
            new_role = body.get("role")
            new_approved = 1 if body.get("approved") else 0
            conn = get_db()
            c = conn.cursor()
            c.execute("UPDATE users SET role = ?, approved = ? WHERE id = ?;", (new_role, new_approved, target_id))
            conn.commit()
            conn.close()
            return self.send_json({"message": "Usuario actualizado"})

        # CREAR PACIENTE (SOLO FISIO)
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
            c = conn.cursor()
            linked_user_id = None
            if linked_email:
                c.execute("SELECT id FROM users WHERE email = ?;", (linked_email,))
                u = c.fetchone()
                if u:
                    linked_user_id = u["id"]

            c.execute("""
                INSERT INTO patients (name, dorsal, position, status, phone, antecedents, physio_notes, public_instruction, linked_user_id)
                VALUES (?, ?, ?, ?, ?, ?, 'Ficha creada. Pendiente de valoración.', 'Completar rutina activa.', ?);
            """, (name, dorsal, position, status, phone, antecedents, linked_user_id))
            pid = c.lastrowid
            conn.commit()
            conn.close()
            return self.send_json({"message": "Paciente creada", "id": pid})

        # EDITAR PACIENTE (SOLO FISIO)
        if path.startswith("/api/patients/") and not path.endswith("/visits") and not path.endswith("/exercises"):
            parts = path.strip("/").split("/")
            if len(parts) == 3 and parts[2].isdigit():
                pid = int(parts[2])
                if user["role"] != "fisio_admin":
                    return self.send_json({"error": "Acceso denegado"}, 403)

                conn = get_db()
                c = conn.cursor()
                
                # Campos opcionales a actualizar
                fields = []
                vals = []
                for k in ["name", "dorsal", "position", "status", "phone", "antecedents", "physio_notes", "public_instruction"]:
                    if k in body:
                        fields.append(f"{k} = ?")
                        vals.append(body[k])

                if "linked_email" in body:
                    le = body["linked_email"].strip().lower()
                    if le:
                        c.execute("SELECT id FROM users WHERE email = ?;", (le,))
                        u = c.fetchone()
                        fields.append("linked_user_id = ?")
                        vals.append(u["id"] if u else None)
                    else:
                        fields.append("linked_user_id = NULL")

                if fields:
                    vals.append(pid)
                    c.execute(f"UPDATE patients SET {', '.join(fields)} WHERE id = ?;", vals)
                    conn.commit()

                conn.close()
                return self.send_json({"message": "Paciente actualizada"})

        # ELIMINAR PACIENTE (SOLO FISIO)
        if path.startswith("/api/patients/") and path.endswith("/delete"):
            pid = int(path.split("/")[3])
            if user["role"] != "fisio_admin":
                return self.send_json({"error": "Acceso denegado"}, 403)
            conn = get_db()
            c = conn.cursor()
            c.execute("DELETE FROM patients WHERE id = ?;", (pid,))
            conn.commit()
            conn.close()
            return self.send_json({"message": "Paciente y todo su historial eliminados"})

        # CREAR VISITA (SOLO FISIO)
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
            c = conn.cursor()
            c.execute("""
                INSERT INTO visits (patient_id, date, status_after, diagnosis, treatment, private_notes)
                VALUES (?, ?, ?, ?, ?, ?);
            """, (pid, date, status_after, diagnosis, treatment, private_notes))
            
            # Actualizar también el estado general de la paciente
            c.execute("UPDATE patients SET status = ? WHERE id = ?;", (status_after, pid))
            conn.commit()
            conn.close()
            return self.send_json({"message": "Visita registrada"})

        # ELIMINAR VISITA (SOLO FISIO)
        if path.startswith("/api/visits/") and path.endswith("/delete"):
            vid = int(path.split("/")[3])
            if user["role"] != "fisio_admin":
                return self.send_json({"error": "Acceso denegado"}, 403)
            conn = get_db()
            c = conn.cursor()
            c.execute("DELETE FROM visits WHERE id = ?;", (vid,))
            conn.commit()
            conn.close()
            return self.send_json({"message": "Visita eliminada"})

        # CREAR EJERCICIO (SOLO FISIO)
        if path.startswith("/api/patients/") and path.endswith("/exercises"):
            pid = int(path.split("/")[3])
            if user["role"] != "fisio_admin":
                return self.send_json({"error": "Acceso denegado"}, 403)
            
            conn = get_db()
            c = conn.cursor()
            c.execute("""
                INSERT INTO exercises (patient_id, name, sets_reps, frequency, instructions, video_url)
                VALUES (?, ?, ?, ?, ?, ?);
            """, (pid, body.get("name"), body.get("sets_reps"), body.get("frequency", ""), body.get("instructions", ""), body.get("video_url", "")))
            conn.commit()
            conn.close()
            return self.send_json({"message": "Ejercicio añadido"})

        # TOGGLE COMPLETAR EJERCICIO (JUGADORA O FISIO)
        if path.startswith("/api/exercises/") and path.endswith("/toggle"):
            eid = int(path.split("/")[3])
            conn = get_db()
            c = conn.cursor()
            c.execute("UPDATE exercises SET completed = NOT completed WHERE id = ?;", (eid,))
            conn.commit()
            conn.close()
            return self.send_json({"message": "Estado de ejercicio cambiado"})

        # ELIMINAR EJERCICIO (SOLO FISIO)
        if path.startswith("/api/exercises/") and path.endswith("/delete"):
            eid = int(path.split("/")[3])
            if user["role"] != "fisio_admin":
                return self.send_json({"error": "Acceso denegado"}, 403)
            conn = get_db()
            c = conn.cursor()
            c.execute("DELETE FROM exercises WHERE id = ?;", (eid,))
            conn.commit()
            conn.close()
            return self.send_json({"message": "Ejercicio eliminado"})

        return self.send_json({"error": "Ruta no encontrada"}, 404)

if __name__ == "__main__":
    init_db()
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("", PORT), FisioHandler) as httpd:
        print(f"FisioTeam Backend corriendo en puerto {PORT} con SQLite")
        httpd.serve_forever()
