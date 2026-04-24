# Módulo 14 — Problemas reales y postmortem

## Objetivo

Documentar los 4 incidentes concretos durante el deploy 2026-04-23, su síntoma, causa raíz, fix y lección. Aprender de errores específicos en lugar de listas genéricas de "best practices".

---

## Incidente #1 — `pg_dump` version mismatch

**Síntoma:**
```
pg_dump: error: server version: 16.1; pg_dump version: 14.x
pg_dump: error: aborting because of server version mismatch
```

**Contexto:** intentaba hacer backup pre-reset de la RDS antes de correr `run_bootstrap.py` en F0 del deploy.

**Causa raíz:** Homebrew tenía `postgresql@14` instalado como default, y `pg_dump` solo puede dumpear versiones **≤** a la suya. El protocolo es forward-compatible en lectura de datos, pero `pg_dump` explícitamente bloquea por seguridad.

**Fix:**
```bash
brew install postgresql@16
export PATH="/opt/homebrew/opt/postgresql@16/bin:$PATH"
pg_dump --version  # confirma 16.x
pg_dump -h ml-monitoring-db.cepye8aei35e.us-east-1.rds.amazonaws.com -U mlmonitor_admin -d mlmonitor -f backup.sql
```

**Tiempo perdido:** ~20 min (primera reacción fue buscar flags para ignorar mismatch, no existen).

**Lección:** al inicio de cualquier proyecto con RDS, confirma que tu `pg_dump` local hace match con la versión del server. Agrégalo a la checklist del módulo 00.

---

## Incidente #2 — `libgdk-pixbuf2.0-0` no existe en Debian bookworm

**Síntoma:** primer `docker build` del Dockerfile:
```
E: Unable to locate package libgdk-pixbuf2.0-0
```

**Contexto:** copié la lista de libs nativas de WeasyPrint de una guía vieja (basada en Debian buster/bullseye).

**Causa raíz:** Debian 12 "bookworm" renombró varios paquetes para consistencia. `libgdk-pixbuf2.0-0` (sin guión entre `pixbuf` y `2.0`) → `libgdk-pixbuf-2.0-0` (con guión). Semánticamente idéntico; el paquete solo cambió de nombre.

**Fix:** editar la línea del Dockerfile con guión. Un carácter.

**Cómo diagnostiqué:**
```bash
docker run --rm python:3.11-slim apt-cache search gdk-pixbuf
# libgdk-pixbuf-2.0-0 - GDK Pixbuf library
```

**Tiempo perdido:** ~5 min.

**Lección:** cuando `apt-get install` falla con "unable to locate", lo primero es `apt-cache search <palabra-clave>` para encontrar el nombre real.

---

## Incidente #3 — Docker Hub 503 intermitente

**Síntoma:**
```
Error response from daemon: received unexpected HTTP status: 503 Service Unavailable
```
al correr `docker pull postgres:16` (necesario para restaurar el dump en un contenedor local de prueba).

**Contexto:** quería validar el `pg_dump` en un Postgres 16 aislado antes de tocar RDS.

**Causa raíz:** Docker Hub tuvo outage parcial esa tarde (confirmado en status page). No es un problema del proyecto.

**Fix:** usar `brew install postgresql@16` como alternativa (cliente suficiente para mi caso; no necesitaba server aislado).

**Tiempo perdido:** ~15 min antes de cambiar de estrategia.

**Lección:** dependencias externas fallan. Ten un plan B (brew, GitHub Container Registry, imagen cacheada local). Si un recurso externo bloquea tu trabajo >10 min, cambia de camino.

---

## Incidente #4 — SES AccessDenied en smoke test final

**Síntoma:** primer `aws ecs run-task` end-to-end:
```
botocore.exceptions.ClientError: An error occurred (AccessDenied) when calling the SendRawEmail operation:
User: arn:aws:sts::930067561911:assumed-role/mlmonitor-task/<session>
is not authorized to perform: ses:SendRawEmail on resource:
arn:aws:ses:us-east-1:930067561911:identity/samsalriu@gmail.com
because no identity-based policy allows the ses:SendRawEmail action
```

**Contexto:** F7 del plan de deploy. Todo lo demás funcionó (S3, Bedrock, RDS) — solo el envío final falló.

**Lectura confusa del error:** al principio pensé que el problema era el **sender** (`1206029@...`), pero el mensaje menciona la identity del **recipient** (`samsalriu@...`). Cuesta unos minutos parsear que SES valida permisos sobre **ambas** identities de la operación.

**Causa raíz:** mi policy inicial era:
```json
{ "Action": "ses:SendRawEmail",
  "Resource": "arn:aws:ses:us-east-1:930067561911:identity/1206029@onuriscp.com" }
```
Solo autorizaba la identity del sender. En SES sandbox, cada `SendRawEmail` se valida contra **todas** las identities involucradas (sender + cada recipient) si `Resource != "*"`.

**Fix** ([`deploy/iam/mlmonitor-task-policy.json`](../../deploy/iam/mlmonitor-task-policy.json)):
```json
{
  "Action": "ses:SendRawEmail",
  "Resource": [
    "arn:aws:ses:us-east-1:930067561911:identity/1206029@onuriscp.com",
    "arn:aws:ses:us-east-1:930067561911:identity/samsalriu@gmail.com"
  ],
  "Condition": { "StringEquals": { "ses:FromAddress": "1206029@onuriscp.com" } }
}
```

Dos cosas:
1. Ambas identities en `Resource`.
2. `Condition` ancla qué address puede usarse como sender (de las dos autorizadas, solo 1206029 como FROM).

**Tiempo perdido:** ~25 min (principalmente en leer mal el error).

**Lección doble:**
- Las policies de IAM evalúan contra **todos los recursos que la acción toca**, no solo el objeto primario. Google "X AccessDenied explain" casi siempre revela esta lógica.
- Cuando agregues destinatarios nuevos, tienes que extender `Resource` (además de verificar la identity en SES). Documentado en `aws_iam.md §2`.

---

## Patrones recurrentes

Mirando los 4 incidentes:

1. **Dos versiones distintas del mismo tool** (pg_dump 14 vs pg 16, Debian buster vs bookworm) → checklist de versiones al inicio del proyecto.
2. **Mensaje de error que confunde al principiante** (SES menciona recipient, no sender) → leerlo despacio y extractar: `User X is not authorized to perform Y on resource Z`. Z es la clave.
3. **Dependencia externa frágil** (Docker Hub) → tener alternativas.

## Ejercicios

1. Reproduce el incidente #2: corre `docker build` con una línea `libgdk-pixbuf2.0-0` (sin guión). Lee el error. Corrige. Observa build exitoso.
2. Reproduce el incidente #4 en modo seguro: crea un IAM user de prueba con policy que liste solo una identity SES, intenta `send-raw-email` a dos recipients. Captura el error.
3. Escribe en tus propias palabras (1 párrafo) por qué `Condition: ses:FromAddress` es importante aunque ya tengas ambas identities.

## Checklist de dominio

- [ ] Puedo explicar las 4 causas raíz sin mirar.
- [ ] Sé qué preguntar primero cuando IAM reporta AccessDenied.
- [ ] Tengo plan B para dependencias externas.
- [ ] Verifico versiones de herramientas al arrancar proyectos con RDS.

## Referencias

- [`aws sts decode-authorization-message`](https://docs.aws.amazon.com/STS/latest/APIReference/API_DecodeAuthorizationMessage.html) — a veces expande errores IAM.
- Interno: `devlog.md` entrada 2026-04-23.
