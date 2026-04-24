# dudas_documentacion.md

Archivo vivo para registrar preguntas que el agente no puede responder sólo leyendo el repositorio. El usuario responde aquí (o vía conversación) y el agente actualiza la documentación correspondiente.

Formato por duda:
- **Contexto:** dónde surgió (archivo/sección).
- **Pregunta:** la duda concreta.
- **Por qué bloquea documentar:** qué no puedo afirmar sin esta información.
- **Respuesta:** (el usuario la llena).
- **Estado:** `abierta` | `respondida` | `aplicada` (ya incorporada al doc).

---

## Dudas abiertas (2026-04-20, sesión inicial de documentación)

### D1 — Nombre oficial del secreto de SES

- **Contexto:** `config/secrets_loader.py` usa `ml-monitoring/SES` (mayúsculas) para cargar `sender_email` y `recipient_email`.
- **Pregunta:** ¿El nombre real del secreto en AWS Secrets Manager es `ml-monitoring/SES` (con "SES" en mayúsculas) o se normalizará a minúsculas (`ml-monitoring/ses`) en algún momento? ¿Se añadirán otros campos al secreto (p. ej. un `reply_to`)?
- **Por qué bloquea documentar:** el inventario en `docs/infrastructure/aws_secrets_manager.md` debe reflejar el identificador exacto que el runtime consume.
- **Respuesta (2026-04-23):** confirmado `ml-monitoring/SES` con mayúsculas. No se añaden campos nuevos.
- **Estado:** aplicada

### D2 — Destinatarios reales de los PDFs

- **Contexto:** `config/secrets_loader.py` mapea `recipient_email` a `email_recipients` (settings) y `SESEmailSender` lo parsea como lista separada por comas.
- **Pregunta:** ¿Qué destinatarios reales (roles o equipos, no correos personales) deberían recibir el reporte semanal en producción? ¿Se maneja lista única o por segmento?
- **Por qué bloquea documentar:** `docs/architecture/architecture.md` describe el paso final del pipeline, y sería útil que el doc enumere los consumidores del reporte.
- **Respuesta (2026-04-23):** por ahora una sola dirección verificada en SES sandbox (`samsalriu@gmail.com`). Lista única, no por segmento. Se revisará cuando se salga del sandbox.
- **Estado:** aplicada

### D3 — Bucket S3 definitivo y política de retención

- **Contexto:** `config/settings.py` define `s3_bucket=""` (deshabilitado) y `s3_prefix="mlmonitor/reports"`.
- **Pregunta:** ¿Cuál será el bucket productivo? ¿Hay política de lifecycle (Glacier / expiración) para los PDFs?
- **Por qué bloquea documentar:** `aws_secrets_manager.md` o un doc de infra de S3 debería listar el bucket autoritativo cuando exista.
- **Respuesta (2026-04-23):** `ml-monitoring-reports-credito` (creado 2026-03-02). Dos prefijos: `inputs/raw_tables/` (CSVs) y `mlmonitor/reports/` (PDFs). Lifecycle aún sin configurar — deuda técnica en `docs/infrastructure/aws_deployment.md §5`.
- **Estado:** aplicada

### D4 — Plataforma de ejecución en AWS

- **Contexto:** `CLAUDE.md §6` menciona "ECS / Step Functions — a definir" como próximo hito.
- **Pregunta:** ¿Ya hay decisión sobre la plataforma de ejecución (ECS Fargate, Step Functions + Lambda, Batch, EventBridge Scheduler)? ¿Cuál es la frecuencia esperada (lunes a las X hora)?
- **Por qué bloquea documentar:** el diagrama de `architecture.md` queda incompleto o impreciso sin saber quién dispara el pipeline semanalmente.
- **Respuesta (2026-04-23):** ECS Fargate + EventBridge Scheduler, una sola task secuencial. Disparada lunes 08:00 CDMX = 14:00 UTC. Ver ADR §8.2.20 y `docs/infrastructure/aws_deployment.md`.
- **Estado:** aplicada

### D5 — Origen de los CSVs raw en producción

- **Contexto:** Hoy `data/inputs/raw_tables/*.csv` son archivos locales. DECISIONS.md §8.2.18 menciona que en el futuro "el bootstrap acepta un DataFrame" para permitir lectura desde BD.
- **Pregunta:** Cuando todo corra en AWS, ¿los CSVs se descargarán desde S3, se consultarán desde una BD origen (RDS, Redshift, otro), o habrá un pipeline externo que los deposite en algún lugar?
- **Por qué bloquea documentar:** `architecture.md` necesita describir la fuente real de los datos raw.
- **Respuesta (2026-04-23):** para el MVP, el usuario sube manualmente a `s3://ml-monitoring-reports-credito/inputs/raw_tables/` y el contenedor hace `aws s3 sync` al arrancar. Automatizar el upstream (que otro pipeline deposite los CSVs ahí) queda fuera del MVP.
- **Estado:** aplicada

### D6 — Política de versionado del baseline de entrenamiento

- **Contexto:** `base_train_test_bb.csv` es la referencia congelada de PSI (DECISIONS.md §8.2.18). `META_BASELINE_DISTRIBUTIONS` se puebla una sola vez.
- **Pregunta:** ¿Cuándo y cómo se espera refrescar el baseline? ¿Cada re-entrenamiento del modelo? ¿Se invalida toda la tabla y se reejecuta bootstrap, o se versiona por `model_id`?
- **Por qué bloquea documentar:** `data_model.md` debería describir el ciclo de vida de esa tabla y no asumir inmutabilidad eterna.
- **Respuesta:**
- **Estado:** abierta

### D7 — Múltiples modelos en paralelo

- **Contexto:** `MetaModelRegistry` está diseñada para soportar varios `model_id`, pero hoy sólo existe `BAZBOOST_V1`.
- **Pregunta:** ¿Se planea monitorear otros modelos en paralelo (p. ej. segunda versión del scorecard, otros productos)? ¿Cuál es el horizonte?
- **Por qué bloquea documentar:** ayuda a priorizar qué convenciones generalizar en `data_model.md` y cuáles dejar explícitamente como específicas de BAZBOOST_V1.
- **Respuesta:**
- **Estado:** abierta

### D8 — SLA y ventana operativa de envío

- **Contexto:** No hay `cron` ni `schedule` en el código; el envío semanal se dispara manualmente (`run_pipeline.py`).
- **Pregunta:** ¿Existe un SLA definido (p. ej. "reporte listo el martes 9 a.m. CDMX")? ¿Quién es el responsable de disparar la ejecución mientras no esté automatizada?
- **Por qué bloquea documentar:** `architecture.md` debería mencionar la cadencia operativa real.
- **Respuesta (2026-04-23):** lunes 08:00 CDMX (cron UTC `0 14 ? * MON *`). Disparo automático por EventBridge Scheduler `mlmonitor-weekly`. Disparo manual siempre disponible con `aws ecs run-task`; el responsable de la supervisión semanal es el usuario (`sam.salinas`).
- **Estado:** aplicada

---

## Dudas respondidas (archivo histórico)

_Vacío por ahora._
