# AWS Secrets Manager — Inventario de secretos

Este documento enumera los secretos que MLMonitor consume, qué campos tienen y cómo se mapean a `Settings`.

Fuente autoritativa del código:
- `config/secrets_loader.py::load_all_secrets(region)` — construye el dict de overrides.
- `config/settings.py::_build_settings()` — aplica los overrides sobre un `Settings()` base.

---

## 1. Principios

- **Lazy import de `boto3`:** el import vive dentro de `_fetch_secret()` para que el ETL (grupo de deps `main`, sin `boto3`) pueda correr sin AWS.
- **Fallback silencioso:** si Secrets Manager no está disponible (no hay credenciales, no hay internet, boto3 no instalado), `_build_settings()` captura la excepción, imprime un aviso y usa los defaults del `.env`. Esto permite desarrollo local sin AWS.
- **Región compartida:** todos los secretos viven en la misma región que Bedrock, S3 y SES (`AWS_REGION`, default `us-east-1`).
- **Los secretos solo contienen lo sensible:** config no sensible (bucket S3, prefix, región) va en `.env` y/o `Settings` defaults. No duplicar.

---

## 2. Secretos activos

### 2.1 `ml-monitoring/rds`

**Propósito:** credenciales y coordenadas de la instancia RDS PostgreSQL.

**Estructura esperada (JSON):**

```json
{
  "username": "string",
  "password": "string",
  "host": "string",
  "port": "5432",
  "dbname": "string"
}
```

**Cómo se consume:** `secrets_loader.py` arma la URL con:

```python
db_url = f"postgresql://{username}:{password}@{host}:{port}/{dbname}"
```

Y la devuelve bajo la key `db_url`, que `Settings` sobrescribe (default: `sqlite:///mlmonitor_dev.db`).

**Quién lo lee:** tanto el ETL como el Pipeline (ambos necesitan la BD).

**Observación:** `port` puede venir como string o int; el f-string lo acepta en ambos casos. Si RDS rota la contraseña, la siguiente ejecución del pipeline vuelve a cargar el secreto.

---

### 2.2 `ml-monitoring/SES`

**Propósito:** config de envío de correo (emisor y destinatarios por defecto).

**Estructura esperada (JSON):**

```json
{
  "sender_email": "string",
  "recipient_email": "string"
}
```

`recipient_email` puede ser una lista separada por comas (`a@x.com,b@x.com`); `settings.recipient_list` la parsea.

**Mapeo a `Settings`:**

| Campo del secreto | Setting |
|---|---|
| `sender_email` | `ses_from_email`, `email_from` |
| `recipient_email` | `email_recipients` |

**Quién lo lee:** solo el Pipeline (paso 4 del orchestrator, vía `SESEmailSender`). El ETL no lo necesita.

**Observación:** el nombre del secreto incluye `SES` en mayúsculas tal cual lo llama el código (`_fetch_secret("ml-monitoring/SES", region)`). Confirmación pendiente en `dudas_documentacion.md` D1.

---

## 3. Orden de precedencia de configuración

1. `Settings()` defaults en `config/settings.py` (valores hardcodeados del código).
2. `.env` (cargado por `SettingsConfigDict(env_file=".env")`).
3. Overrides de `load_all_secrets()` vía `s.model_copy(update=overrides)`.

Por tanto: **Secrets Manager gana**. Si un secreto falla al cargarse, se usan las capas 1 y 2.

---

## 4. Permisos IAM de Secrets Manager

Permisos mínimos sobre Secrets Manager para que la app pueda leer sus secretos. Para la matriz IAM cross-service (Bedrock, S3, SES) ver [`aws_iam.md`](./aws_iam.md).

- **Rol Pipeline:** `secretsmanager:GetSecretValue` sobre los ARNs de `ml-monitoring/rds` **y** `ml-monitoring/SES`.
- **Rol ETL** (si termina siendo un job separado en AWS): `secretsmanager:GetSecretValue` únicamente sobre `ml-monitoring/rds`.

Conectividad de red a RDS es requisito operativo pero no un permiso IAM.

---

## 5. Rotación de secretos

Hoy no hay automatización de rotación. El código releve el secreto en cada ejecución (no hay caché entre runs del CLI), por lo que una rotación externa se propaga en el siguiente pipeline. No rotar manualmente sin coordinar con el equipo responsable.
