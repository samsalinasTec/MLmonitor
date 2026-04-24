# Módulo 06 — Dockerfile y WeasyPrint

## Objetivo

Entender línea por línea el `Dockerfile`, por qué WeasyPrint requiere libs nativas, y cómo construir la imagen localmente para `linux/amd64` desde una Mac con Apple Silicon.

## Conceptos

**WeasyPrint** es la librería Python que convierte HTML+CSS a PDF. Internamente usa **Cairo** (renderizado 2D), **Pango** (layout de texto) y **GDK-PixBuf** (imágenes) — todos binarios de C. Sin estos instalados en el contenedor, `import weasyprint` falla o genera PDFs rotos.

**Multi-arch con buildx.** `docker build` por defecto construye para la arquitectura del host. En Apple Silicon eso es `arm64`. ECS Fargate que configuramos corre en `X86_64`. Sin `--platform linux/amd64`, el contenedor arranca y ECS reporta `exec format error`.

**`.dockerignore`** excluye del build context. Crítico: sin él, `mlmonitor_dev.db` (puede ser >100 MB), `artifacts/`, `data/inputs/`, `notebooks/` y `.venv` se suben al daemon de Docker, inflando el build y filtrando datos al layer.

## Anatomía del Dockerfile

Ruta: [`mlmonitor/Dockerfile`](../../Dockerfile). Revísalo y mapea cada bloque:

```dockerfile
FROM python:3.11-slim AS base
```
Base mínima con Debian bookworm. `slim` ahorra ~700 MB vs `python:3.11` full.

```dockerfile
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libcairo2 libpango-1.0-0 libpangoft2-1.0-0 \
    libgdk-pixbuf-2.0-0 libffi-dev shared-mime-info fonts-dejavu \
    libpq5 curl awscli jq \
    && rm -rf /var/lib/apt/lists/*
```
Libs nativas. `libpq5` para psycopg2, `awscli` para el `s3 sync` del entrypoint, `fonts-dejavu` para que WeasyPrint tenga fuentes.

```dockerfile
ENV POETRY_VERSION=1.8.3 POETRY_VIRTUALENVS_CREATE=false \
    PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
RUN pip install --no-cache-dir "poetry==${POETRY_VERSION}"
```
Poetry dentro del contenedor. `VIRTUALENVS_CREATE=false` instala en site-packages del sistema (no hay usuario separado, no hay necesidad de venv).

```dockerfile
COPY pyproject.toml poetry.lock ./
RUN poetry install --with pipeline --no-root --no-interaction --no-ansi
```
**Orden importa:** copiamos solo los lockfiles antes del código. Docker cachea este layer mientras `pyproject.toml` no cambie, ahorrando minutos en rebuilds.

```dockerfile
COPY src/ ./src/
COPY scripts/ ./scripts/
COPY config/ ./config/
ENV PYTHONPATH=/app/src:/app
```
Código después. Cambios en `src/` no invalidan el layer de deps.

```dockerfile
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
```

## El entrypoint

Ruta: [`mlmonitor/docker/entrypoint.sh`](../../docker/entrypoint.sh):

```bash
#!/usr/bin/env bash
set -euo pipefail

aws s3 sync "s3://${INPUTS_BUCKET}/${INPUTS_PREFIX}/" /app/data/inputs/raw_tables/

poetry run python scripts/run_incremental_etl.py
poetry run python scripts/run_pipeline.py
```

Orden secuencial, `set -e` para que falle temprano si cualquier paso revienta.

## Track A — Inspección

```bash
# Ver el Dockerfile
cat mlmonitor/Dockerfile

# .dockerignore
cat mlmonitor/.dockerignore

# Imagen actual en ECR con sus layers
aws ecr describe-images --repository-name mlmonitor \
  --image-ids imageTag=latest \
  --query 'imageDetails[0].{size:imageSizeInBytes,digest:imageDigest,pushed:imagePushedAt}'
```

## Construir localmente (smoke test)

```bash
cd mlmonitor

# buildx builder (una sola vez)
docker buildx create --name mlmonitor-builder --use 2>/dev/null || docker buildx use mlmonitor-builder

# Build para linux/amd64 (ECS Fargate) y cargar localmente
docker buildx build --platform linux/amd64 -t mlmonitor:local --load .

# Probar WeasyPrint dentro del contenedor
docker run --rm mlmonitor:local python -c "import weasyprint; print(weasyprint.__version__)"
# Debe imprimir: 60.2
```

## Problemas que encontré

1. **`libgdk-pixbuf2.0-0` no existe en Debian bookworm.** Error: `E: Unable to locate package libgdk-pixbuf2.0-0`. Causa: Debian renombró de `libgdk-pixbuf2.0-0` (buster, sin guión) a `libgdk-pixbuf-2.0-0` (bookworm, con guión). Fix: usar el nombre con guión. Lección: leer el output de `apt-cache search gdk-pixbuf`.

2. **`exec format error` en Fargate.** Construí sin `--platform`, Docker Desktop en Mac M2 hizo `arm64`, ECS la rechazó. Fix: siempre `--platform linux/amd64` al pushear.

3. **Imagen gorda con Docker Hub 503.** `postgres:16` intermitentemente respondía 503 al pullear (problema externo de Docker Hub). Alternativa usada: `brew install postgresql@16` en laptop.

## Ejercicios

1. Construye la imagen local y mide su tamaño (`docker images mlmonitor:local`). Compáralo con el ECR (`imageSizeInBytes`). Deben coincidir ±5%.
2. Sin cambiar código, ejecuta el build dos veces. Observa que el segundo tarda segundos (layers cacheados).
3. Cambia una línea en `src/mlmonitor/pipeline/runner.py` y reconstruye. Observa que solo re-invalida los layers de `COPY src/` en adelante, no los de `poetry install`.
4. Intenta construir sin `.dockerignore` temporalmente (renómbralo). Mide `docker build` context size y entiende por qué el `.dockerignore` importa.

## Checklist de dominio

- [ ] Sé qué libs nativas necesita WeasyPrint y por qué.
- [ ] Entiendo por qué `COPY pyproject.toml` va antes que `COPY src/`.
- [ ] Sé por qué `--platform linux/amd64` es obligatorio en Apple Silicon.
- [ ] Puedo listar 3 cosas que `.dockerignore` excluye y por qué.

## Referencias

- [WeasyPrint installation](https://doc.courtbouillon.org/weasyprint/stable/first_steps.html#installation)
- [Docker layer caching](https://docs.docker.com/build/cache/)
