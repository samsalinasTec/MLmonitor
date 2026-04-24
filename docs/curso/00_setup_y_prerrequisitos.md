# Módulo 00 — Setup y prerrequisitos

## Objetivo

Al terminar tendrás el entorno local listo para inspeccionar (Track A) y opcionalmente recrear (Track B) la infra AWS del proyecto. Serás capaz de ejecutar `aws sts get-caller-identity` y ver `930067561911` con permisos de administrador.

## Conceptos

**AWS CLI, perfil y credenciales.** El CLI lee `~/.aws/credentials` y `~/.aws/config`. Un "perfil" empaqueta `aws_access_key_id`, `aws_secret_access_key` y región/output. La variable `AWS_PROFILE` selecciona cuál usar.

**Región.** Todo el proyecto vive en `us-east-1`. Si olvidas poner región, el CLI asume `aws_default_region` del perfil o falla.

**IAM user vs IAM role.** Tu usuario `sam.salinas` es un IAM user con credenciales persistentes. Los **roles** (que verás en módulo 02) son identidades temporales asumidas por servicios AWS (p.ej. ECS asume `mlmonitor-task`).

## Herramientas requeridas

| Herramienta | Para qué | Instalación macOS |
|---|---|---|
| `aws` CLI v2 | Todos los comandos | `brew install awscli` |
| `docker` + `buildx` | Construir imagen del pipeline | Docker Desktop |
| `jq` | Parsear JSON de respuestas AWS | `brew install jq` |
| `psql` (v16) | Conectar a RDS Postgres 16 | `brew install postgresql@16` |
| `poetry` | Correr el pipeline desde laptop | `pipx install poetry` |
| Python 3.11 | Runtime del pipeline | `brew install python@3.11` |

> ⚠️ **Por qué `postgresql@16` y no v14:** RDS corre Postgres 16. `pg_dump` v14 se niega a dumpear una DB v16 (ver módulo 14, postmortem #1).

## Track A — Verificación del entorno

```bash
# 1) AWS CLI configurado
aws sts get-caller-identity
# Esperas ver:
# {
#   "UserId": "AIDAxxxx",
#   "Account": "930067561911",
#   "Arn": "arn:aws:iam::930067561911:user/sam.salinas"
# }

# 2) Región
aws configure get region  # us-east-1

# 3) Docker buildx disponible
docker buildx version

# 4) jq
jq --version

# 5) Python + Poetry
python3.11 --version
poetry --version
```

Si cualquiera falla, arregla antes de seguir.

### Smoke test de permisos

```bash
# Listar ECR repos — prueba que puedes leer servicios core
aws ecr describe-repositories --region us-east-1 --query 'repositories[].repositoryName'
# Debe incluir: "mlmonitor"

# Listar clusters ECS
aws ecs list-clusters --region us-east-1
# Debe incluir: ".../mlmonitor-cluster"
```

## Track B — Sandbox propio (opcional)

Si vas a recrear infra desde cero sin chocar con producción, define tu alias ahora:

```bash
export CURSO_ALIAS=$(whoami)
echo "Tus recursos serán sufijados con: -curso-${CURSO_ALIAS}"
```

Usaremos este sufijo en los módulos siguientes (p. ej. `mlmonitor-curso-samuelsalinas`).

## Problemas que encontré

- **`docker buildx` no usaba `linux/amd64` por defecto** en Mac con Apple Silicon. Si construyes sin `--platform linux/amd64`, ECS Fargate (que corre en x86_64) rechaza la imagen con `exec format error`. Solución: siempre usar `docker buildx build --platform linux/amd64 ...`. Lo verás en módulo 06.

## Ejercicios

1. Corre `aws sts get-caller-identity` y confirma `Account=930067561911`. ✅
2. Corre `aws ec2 describe-regions --query 'Regions[].RegionName'` y confirma que `us-east-1` está en la lista.
3. Ejecuta `bash docs/curso/scripts/check_m00_setup.sh` y confirma que todos los checks pasan.

## Checklist de dominio

- [ ] Puedo explicar la diferencia entre IAM user y IAM role.
- [ ] Sé dónde vive mi credencial y qué variable cambia el perfil.
- [ ] Tengo `aws`, `docker buildx`, `jq`, `psql@16`, `poetry`, `python3.11`.
- [ ] `aws sts get-caller-identity` me da la cuenta correcta.

## Referencias

- [AWS CLI config files](https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-files.html)
- Interno: [`docs/infrastructure/aws_deployment.md`](../infrastructure/aws_deployment.md)
