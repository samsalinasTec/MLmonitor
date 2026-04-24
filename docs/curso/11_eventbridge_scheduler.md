# Módulo 11 — EventBridge Scheduler

## Objetivo

Entender la diferencia entre EventBridge Scheduler y el "EventBridge Rules" antiguo, leer el schedule actual, pausarlo/reanudarlo, y crear un schedule de prueba en sandbox.

## Conceptos

**EventBridge Scheduler** (2022) es el reemplazo moderno de "EventBridge Rules con schedule". Ventajas: cron en cualquier timezone (no solo UTC), más targets, mejor límite de concurrencia, one-time schedules. **Usa este, no el viejo.**

**Cron expression** de Scheduler: `cron(minutes hours day-of-month month day-of-week year)`. Nota: incluye `year` (el cron de Unix no). Para MLMonitor: `cron(0 14 ? * MON *)` = "a las 14:00 UTC, cualquier día del mes, en lunes, cualquier año".

**Target.** Qué invoca el schedule. En nuestro caso: ECS RunTask sobre `mlmonitor-cluster` + family `mlmonitor`. El target se describe en [`deploy/scheduler-target.json`](../../deploy/scheduler-target.json).

**Role del schedule.** `mlmonitor-scheduler-invoke` (módulo 08). Sin él, Scheduler no puede llamar `ecs:RunTask`.

## El schedule actual

```
Nombre:    mlmonitor-weekly
Cron:      cron(0 14 ? * MON *)
Timezone:  UTC
Equivale a: lunes 08:00 América/Ciudad_de_México
Estado:    ENABLED
Target:    arn:aws:ecs:us-east-1:930067561911:cluster/mlmonitor-cluster
           + task-definition mlmonitor (family, sin revisión → usa la más reciente)
Role:      mlmonitor-scheduler-invoke
```

### ¿Por qué UTC y no `America/Mexico_City`?

Scheduler **sí** soporta timezones. Usamos UTC por simplicidad operativa: el horario de verano en México cambia (o no — CDMX no observa DST desde 2023), pero si algún día vuelve, un schedule en UTC nunca se descoloca. Cálculo manual:
- Horario estándar CDMX (CST, UTC-6) → 08:00 local = 14:00 UTC ✅
- Si vuelve DST (CDT, UTC-5) → 08:00 local = 13:00 UTC (habría que re-ajustar).

Por hoy `cron(0 14 ...) UTC` es correcto.

## Track A — Inspección

```bash
# Schedule
aws scheduler get-schedule --name mlmonitor-weekly

# Historial: últimas invocaciones (buscar con describe-tasks en STOPPED)
aws ecs list-tasks --cluster mlmonitor-cluster --desired-status STOPPED --max-items 5

# Role del scheduler
aws iam get-role --role-name mlmonitor-scheduler-invoke \
  --query 'Role.{trust:AssumeRolePolicyDocument.Statement[0].Principal,created:CreateDate}'
```

## Pausar / reanudar

```bash
# Pausar (no borra, solo suspende)
aws scheduler update-schedule --name mlmonitor-weekly --state DISABLED \
  --schedule-expression "cron(0 14 ? * MON *)" \
  --target file://deploy/scheduler-target.json \
  --flexible-time-window "Mode=OFF"

# Reanudar
aws scheduler update-schedule --name mlmonitor-weekly --state ENABLED \
  --schedule-expression "cron(0 14 ? * MON *)" \
  --target file://deploy/scheduler-target.json \
  --flexible-time-window "Mode=OFF"
```

> `update-schedule` exige reenviar **todo** (expresión, target, flexible window). Es idempotente pero verboso.

## Track B — Tu propio schedule de prueba

```bash
# One-off dentro de 5 min
AT=$(date -u -v+5M +"%Y-%m-%dT%H:%M:%S")
aws scheduler create-schedule \
  --name mlmonitor-test-curso-${CURSO_ALIAS} \
  --schedule-expression "at(${AT})" \
  --flexible-time-window "Mode=OFF" \
  --target file://deploy/scheduler-target.json \
  --state ENABLED

# Espera y verifica que se disparó
sleep 360
aws ecs list-tasks --cluster mlmonitor-cluster --started-by "scheduler"

# Teardown
aws scheduler delete-schedule --name mlmonitor-test-curso-${CURSO_ALIAS}
```

## Ejercicios

1. Convierte `cron(30 7 ? * WED *) UTC` a hora CDMX (horario estándar).
2. Pausa `mlmonitor-weekly` y confirma con `get-schedule` que está `DISABLED`. Luego reanúdalo.
3. Crea un one-off schedule en sandbox con tu alias y bórralo después.

## Problemas que encontré

- **`update-schedule` falla si no reenvías `--target`.** No es un "patch"; es un PUT. Solución: siempre pasar target + expresión + flexible-window aunque no cambien.

## Checklist de dominio

- [ ] Sé la diferencia entre Scheduler y EventBridge Rules.
- [ ] Puedo leer una expresión cron de Scheduler.
- [ ] Sé por qué el target apunta a family sin revisión.
- [ ] Puedo pausar/reanudar/borrar schedules.

## Referencias

- [EventBridge Scheduler user guide](https://docs.aws.amazon.com/scheduler/latest/UserGuide/what-is-scheduler.html)
- [Cron-based schedules](https://docs.aws.amazon.com/scheduler/latest/UserGuide/schedule-types.html#cron-based)
