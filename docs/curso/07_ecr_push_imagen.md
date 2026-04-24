# Módulo 07 — ECR: push de imagen

## Objetivo

Pushear una imagen a ECR, aprender la estrategia de tagging (`v0.1.0` + `latest`), y hacer rollback retaggeando sin rebuild.

## Conceptos

**ECR (Elastic Container Registry)** es el Docker registry privado de AWS. Acceso por IAM (no user/password). Imagen tag mutable por default.

**Tag inmutable vs mutable.** Mutable (default) = puedes sobrescribir `latest` apuntándolo a otra imagen. Inmutable = una vez tagueado, no se puede reusar el tag. Para `v0.1.0` conviene inmutable; para `latest` tiene que ser mutable.

**Estrategia de tagging:**
- `v<semver>` → identidad permanente. Nunca reuso.
- `latest` → puntero al "bueno" actual. Rollback = mover `latest` a un `v<anterior>`.
- `<git-sha>` → opcional en CI/CD, trazabilidad.

## Track A — Inspección

```bash
# Lista de imágenes
aws ecr describe-images --repository-name mlmonitor \
  --query 'imageDetails[].{tags:imageTags,pushed:imagePushedAt,size:imageSizeInBytes}'

# Config de scan
aws ecr describe-repositories --repository-names mlmonitor \
  --query 'repositories[0].{scan:imageScanningConfiguration,mutability:imageTagMutability}'

# Hallazgos de seguridad del último scan
aws ecr describe-image-scan-findings --repository-name mlmonitor --image-id imageTag=latest \
  --query 'imageScanFindings.findingSeverityCounts' 2>/dev/null
```

## Push de una imagen nueva

```bash
cd mlmonitor
VERSION=v0.1.1

# Login a ECR (token dura 12h)
aws ecr get-login-password --region us-east-1 \
  | docker login --username AWS --password-stdin 930067561911.dkr.ecr.us-east-1.amazonaws.com

# Build + push en una pasada
docker buildx build --platform linux/amd64 \
  -t 930067561911.dkr.ecr.us-east-1.amazonaws.com/mlmonitor:${VERSION} \
  -t 930067561911.dkr.ecr.us-east-1.amazonaws.com/mlmonitor:latest \
  --push .
```

**Por qué `--push` en vez de `docker push`:** buildx empuja sin cargar localmente, más rápido.

## Rollback sin rebuild

Si `v0.1.1` rompe algo y quieres volver a `v0.1.0`, no necesitas construir nada — retag:

```bash
# Bajar el manifest de v0.1.0
MANIFEST=$(aws ecr batch-get-image \
  --repository-name mlmonitor \
  --image-ids imageTag=v0.1.0 \
  --query 'images[0].imageManifest' --output text)

# Subirlo como latest
aws ecr put-image \
  --repository-name mlmonitor \
  --image-tag latest \
  --image-manifest "$MANIFEST"
```

El próximo `run-task` o ejecución del Scheduler pulleará la imagen apuntada por `latest` (ahora `v0.1.0`).

## Limpieza de imágenes antiguas

```bash
# Borrar una imagen por tag
aws ecr batch-delete-image --repository-name mlmonitor --image-ids imageTag=v0.0.1

# Ver imágenes sin tag (dangling, de builds fallidos)
aws ecr describe-images --repository-name mlmonitor \
  --filter tagStatus=UNTAGGED \
  --query 'imageDetails[].{digest:imageDigest,pushed:imagePushedAt}'
```

Deuda técnica: configurar lifecycle policy para borrar imágenes untagged > 30 días.

## Ejercicios

1. Haz un build+push con tag `v0.0.0-curso-<alias>`. Verifica que aparece en ECR.
2. Sin rebuild, retaguea `v0.0.0-curso-<alias>` como `curso-latest-<alias>`. Confirma con `describe-images`.
3. Borra ambos tags al terminar.
4. Calcula: si el `imageSizeInBytes` es 500 MB y la task tarda 30s en arrancar, ¿cuánto del cold start es pull? (Mide con `describe-tasks --include TAGS` en módulo 10.)

## Checklist de dominio

- [ ] Sé la diferencia entre tag mutable e inmutable.
- [ ] Puedo pushear y retaggear sin rebuild.
- [ ] Entiendo por qué `latest` + `v<semver>` juntos.
- [ ] Sé cómo hacer rollback en <1 min.

## Referencias

- [ECR tag mutability](https://docs.aws.amazon.com/AmazonECR/latest/userguide/image-tag-mutability.html)
- [Lifecycle policies](https://docs.aws.amazon.com/AmazonECR/latest/userguide/LifecyclePolicies.html)
