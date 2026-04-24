# Módulo 05 — S3: buckets y prefijos

## Objetivo

Entender por qué MLMonitor usa un solo bucket con dos prefijos (`inputs/` y `mlmonitor/reports/`), operar subida/descarga manual, y conocer las deudas pendientes (lifecycle, versioning).

## Conceptos

**Bucket.** Contenedor plano de objetos. Los "directorios" son ilusión — los prefijos son parte del key.

**Prefijo.** Convención. `s3://bucket/inputs/raw_tables/x.csv` es un solo objeto con key `inputs/raw_tables/x.csv`.

**¿Uno o muchos buckets?** Decisión de diseño. Opté por uno solo porque:
- Las políticas IAM pueden restringir por prefijo (ver policy task role: `inputs/*` Read vs `mlmonitor/reports/*` Write).
- Menos recursos que administrar.
- Los CSVs de input y los PDFs de output tienen el mismo owner lógico (este proyecto).

**Desventajas:** si mañana quieres lifecycle distinto por tipo (PDFs a Glacier, CSVs retener 90 días), hay que configurar reglas separadas por prefijo en vez de por bucket.

## Inventario real

```
s3://ml-monitoring-reports-credito/
├── inputs/
│   └── raw_tables/
│       ├── base_train_test_bb.csv          (baseline, se re-sube solo al re-entrenar)
│       ├── variables_serc_<YYYYWW>.csv     (semanal)
│       └── muestra_weekly_<YYYYWW>.csv     (semanal)
└── mlmonitor/
    └── reports/
        └── mlmonitor_<YYYY-MM-DD>.pdf      (uno por corrida)
```

## Track A — Inspección

```bash
# Existencia del bucket
aws s3api head-bucket --bucket ml-monitoring-reports-credito

# Versioning
aws s3api get-bucket-versioning --bucket ml-monitoring-reports-credito
# Vacío = deshabilitado. Deuda técnica.

# Lifecycle
aws s3api get-bucket-lifecycle-configuration --bucket ml-monitoring-reports-credito
# Error NoSuchLifecycleConfiguration = deuda técnica.

# Inventario de prefijos
aws s3 ls s3://ml-monitoring-reports-credito/inputs/raw_tables/
aws s3 ls s3://ml-monitoring-reports-credito/mlmonitor/reports/
```

## Subir un CSV semanal (operación normal)

```bash
# Supón semana 21 (2026-01-12 en ISO)
aws s3 cp variables_serc_202621.csv s3://ml-monitoring-reports-credito/inputs/raw_tables/
aws s3 cp muestra_weekly_202621.csv  s3://ml-monitoring-reports-credito/inputs/raw_tables/
```

## Descargar el último PDF generado

```bash
# Listar por modificación desc
aws s3api list-objects-v2 --bucket ml-monitoring-reports-credito \
  --prefix mlmonitor/reports/ \
  --query 'sort_by(Contents, &LastModified)[-1].{key:Key,size:Size,date:LastModified}'

# Descargar uno específico
aws s3 cp s3://ml-monitoring-reports-credito/mlmonitor/reports/mlmonitor_2026-01-05.pdf /tmp/
```

## Deudas técnicas

1. **Sin lifecycle.** PDFs viejos se acumulan para siempre. Plan: mover a Glacier >180 días.
2. **Sin versioning.** Si alguien sobrescribe un PDF (mismo nombre), se pierde el anterior. Plan: habilitar versioning en `mlmonitor/reports/*`.
3. **Sin automatización de upload de CSVs.** El usuario los sube a mano. D5 en `dudas_documentacion.md`.

## Ejercicios

1. Sube un archivo dummy a `s3://ml-monitoring-reports-credito/inputs/raw_tables/test_$(whoami).txt` y luego bórralo. (Confirma que puedes.)
2. Lista todos los PDFs ordenados por tamaño descendente.
3. Intenta subir un archivo al prefijo `mlmonitor/reports/` desde un perfil sin permisos (p. ej. crea un IAM user sin policy S3). Confirma que falla.

## Checklist de dominio

- [ ] Sé por qué se usa un bucket con prefijos vs dos buckets.
- [ ] Puedo subir y bajar archivos al bucket correcto.
- [ ] Sé qué permisos IAM necesita cada prefijo.
- [ ] Sé las deudas técnicas pendientes (lifecycle, versioning).

## Referencias

- [S3 bucket policies vs IAM](https://docs.aws.amazon.com/AmazonS3/latest/userguide/access-policy-language-overview.html)
- Interno: [`docs/infrastructure/aws_deployment.md §3.2`](../infrastructure/aws_deployment.md)
