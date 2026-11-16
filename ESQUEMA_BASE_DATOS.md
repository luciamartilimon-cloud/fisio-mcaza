# 🏥 FisioTeam — Arquitectura de Base de Datos y Autenticación

## 1. Esquema Relacional de Base de Datos (PostgreSQL en Supabase)

### Tabla `usuarios` (Auth & Perfiles)
| Columna | Tipo | Descripción |
| :--- | :--- | :--- |
| `id` | `UUID (PK)` | ID de autenticación (generado por Supabase Auth) |
| `email` | `TEXT (UNIQUE)` | Correo de inicio de sesión |
| `rol` | `TEXT` | `'fisio_admin'` o `'jugadora'` |
| `nombre` | `TEXT` | Nombre visible |
| `creado_en` | `TIMESTAMP` | Fecha de registro |

---

### Tabla `pacientes` (Fichas creadas por la Fisio)
| Columna | Tipo | Privacidad | Descripción |
| :--- | :--- | :--- | :--- |
| `id` | `UUID (PK)` | Pública / Fisio | Identificador único de la ficha |
| `nombre` | `TEXT` | Pública | Nombre de la jugadora (ej. *Inés Fernández*) |
| `dorsal` | `INT` | Pública | Número de dorsal |
| `posicion` | `TEXT` | Pública | Demarcación (ej. *Centrocampista*) |
| `estado_fisico` | `TEXT` | Pública | *Disponible*, *Molestia leve*, *En readaptación*, *Baja médica* |
| `indicacion_publica` | `TEXT` | **Visible Jugadora** | Pauta médica activa (ej. *"Hielo 15 min y nada de saltos"*) |
| `antecedentes_medicos` | `TEXT` | 🔒 **Solo Fisio** | Historial de lesiones antiguas, cirugías, etc. |
| `sensaciones_clinicas` | `TEXT` | 🔒 **Solo Fisio** | Notas de evolución interna confidencial |
| `email_vinculado` | `TEXT` | **Fisio** | Correo con el que se asocia a la cuenta de usuario |
| `acceso_activo` | `BOOLEAN` | **Fisio** | `false` en Fase 1, `true` cuando se le permite entrar |

---

### Tabla `visitas` (Historial de Camilla)
| Columna | Tipo | Privacidad | Descripción |
| :--- | :--- | :--- | :--- |
| `id` | `UUID (PK)` | Pública / Fisio | Identificador de la visita |
| `paciente_id` | `UUID (FK)` | Relación | Apunta a `pacientes.id` |
| `fecha` | `DATE` | Pública | Día de la sesión |
| `diagnostico` | `TEXT` | Pública | Motivo de consulta |
| `tratamiento` | `TEXT` | Pública | Técnicas aplicadas en camilla |
| `notas_privadas` | `TEXT` | 🔒 **Solo Fisio** | Observaciones clínicas confidenciales |

---

### Tabla `ejercicios` (Rutinas prescritas)
| Columna | Tipo | Privacidad | Descripción |
| :--- | :--- | :--- | :--- |
| `id` | `UUID (PK)` | Pública | Identificador del ejercicio |
| `paciente_id` | `UUID (FK)` | Relación | Apunta a `pacientes.id` |
| `nombre` | `TEXT` | **Visible Jugadora** | Nombre del ejercicio |
| `series_reps` | `TEXT` | **Visible Jugadora** | Series y repeticiones prescritas |
| `frecuencia` | `TEXT` | **Visible Jugadora** | Cuándo realizarlo |
| `instrucciones` | `TEXT` | **Visible Jugadora** | Pautas técnicas de ejecución |
| `video_url` | `TEXT` | **Visible Jugadora** | Enlace a YouTube, Vimeo o Drive |
| `completado_hoy` | `BOOLEAN` | **Visible Jugadora** | Check diario de cumplimiento |

---

## 2. Políticas de Seguridad por Fila (Row Level Security - RLS)

```sql
-- 1. Regla para la Fisio (Admin): Acceso total a todo
CREATE POLICY "Fisio acceso total" ON pacientes
FOR ALL
USING (auth.jwt() ->> 'email' = 'fisio@club.com');

-- 2. Regla para Jugadoras: Solo leen su propio registro y si acceso_activo es true
CREATE POLICY "Jugadora lee solo su ficha publica" ON pacientes
FOR SELECT
USING (
  email_vinculado = auth.jwt() ->> 'email' 
  AND acceso_activo = TRUE
);

-- 3. Vista protegida para Jugadoras (sin columnas privadas)
CREATE VIEW portal_jugadora AS
SELECT id, nombre, dorsal, posicion, estado_fisico, indicacion_publica
FROM pacientes;
```
