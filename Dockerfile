FROM python:3.11-slim AS base

# Librerías nativas: WeasyPrint (Cairo/Pango/GDK-PixBuf), drivers Postgres, curl para healthchecks.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libcairo2 \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libgdk-pixbuf-2.0-0 \
    libffi-dev \
    shared-mime-info \
    fonts-dejavu \
    libpq5 \
    curl \
    unzip \
    && rm -rf /var/lib/apt/lists/*

# AWS CLI v2 (entrypoint hace aws s3 sync). arm64 para Mac Silicon, x86_64 para Fargate default.
RUN ARCH=$(uname -m) && \
    if [ "$ARCH" = "aarch64" ]; then AWS_ARCH="aarch64"; else AWS_ARCH="x86_64"; fi && \
    curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-${AWS_ARCH}.zip" -o /tmp/awscliv2.zip && \
    unzip -q /tmp/awscliv2.zip -d /tmp && \
    /tmp/aws/install && \
    rm -rf /tmp/aws /tmp/awscliv2.zip

ENV POETRY_VERSION=1.8.3 \
    POETRY_VIRTUALENVS_CREATE=false \
    PIP_NO_CACHE_DIR=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN pip install "poetry==${POETRY_VERSION}"

WORKDIR /app

# Dependencias primero para aprovechar cache de capas.
COPY pyproject.toml poetry.lock ./
RUN poetry install --with pipeline --no-root --no-interaction --no-ansi

# Código fuente
COPY src/ ./src/
COPY scripts/ ./scripts/
COPY config/ ./config/

# Entrypoint
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENV PYTHONPATH=/app/src:/app \
    ARTIFACTS_DIR=/tmp/artifacts

ENTRYPOINT ["/entrypoint.sh"]
