"""
Tasa de evento por decil real (qcut sobre fnpuntaje continuo).

Distinto de business_metrics.py: aquí los grupos son percentiles dinámicos
calculados sobre la cohorte (no los bines fijos del scorecard que viven en
FACT_PERFORMANCE_BINNED).

Convenciones:
- Decil 1 = scores más bajos = mayor riesgo (la línea de tasa debe decrecer
  con decil creciente). NO se invierte el score: el eje X habla por sí solo.
- Si pd.qcut colapsa por duplicados (duplicates="drop"), el resultado tendrá
  <10 grupos y la gráfica se ajusta automáticamente.
"""

from datetime import date, timedelta

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from mlmonitor.db.models import FactDecilesHistory, FactPerformanceIndividual, MetaVariables

N_DECILES = 10
DECILE_MIN_OBS = 100
DECILE_WINDOW_WEEKS = 4  # análogo a PSI_WINDOW_WEEKS; ver psi.py


def _window_weeks(cohort_end: date, window: int = DECILE_WINDOW_WEEKS) -> list[date]:
    """Lista de lunes ISO en la ventana, desde cohort_end hacia atrás.

    Para deciles, cohort_end = calculation_week - target_lag (semana donde
    los créditos cumplen exactamente la madurez del target). Las semanas
    anteriores tienen créditos MÁS maduros, por lo que sus outcomes también
    son confiables. Replica la lógica de psi.py::_window_weeks.
    """
    return [cohort_end - timedelta(weeks=i) for i in range(window)]


def compute_decile_table(
    scores: pd.Series,
    target_flags: pd.Series,
    n_deciles: int = N_DECILES,
) -> pd.DataFrame:
    """Agrupa scores en deciles ascendentes y calcula tasa observada por decil.

    Returns:
        DataFrame con columnas: decile (1..n), score_min, score_max, score_mean,
        n_total, n_event, event_rate, pct_population. DataFrame vacío si scores
        está vacío o todo es NaN.
    """
    if len(scores) == 0:
        return pd.DataFrame(columns=[
            "decile", "score_min", "score_max", "score_mean",
            "n_total", "n_event", "event_rate", "pct_population",
        ])

    df = pd.DataFrame({
        "score": pd.to_numeric(scores, errors="coerce"),
        "flag": pd.to_numeric(target_flags, errors="coerce").fillna(0).astype(int),
    }).dropna(subset=["score"]).reset_index(drop=True)

    if df.empty:
        return pd.DataFrame(columns=[
            "decile", "score_min", "score_max", "score_mean",
            "n_total", "n_event", "event_rate", "pct_population",
        ])

    df["bucket"] = pd.qcut(df["score"], q=n_deciles, labels=False, duplicates="drop")
    grouped = (
        df.groupby("bucket", observed=True)
          .agg(
              score_min=("score", "min"),
              score_max=("score", "max"),
              score_mean=("score", "mean"),
              n_total=("flag", "size"),
              n_event=("flag", "sum"),
          )
          .reset_index(drop=True)
    )
    grouped["decile"] = np.arange(1, len(grouped) + 1)
    grouped["event_rate"] = grouped["n_event"] / grouped["n_total"].clip(lower=1)
    total = grouped["n_total"].sum()
    grouped["pct_population"] = grouped["n_total"] / total if total else 0.0
    return grouped[[
        "decile", "score_min", "score_max", "score_mean",
        "n_total", "n_event", "event_rate", "pct_population",
    ]]


def get_decile_data_for_segment(
    session: Session,
    model_registry_id: int,
    calculation_week: date,
    primary_target_lag: int,
    all_targets: list[MetaVariables],
    min_obs: int = DECILE_MIN_OBS,
) -> dict:
    """Carga FACT_PERFORMANCE_INDIVIDUAL y arma datos para las dos gráficas.

    Cada segmento es un model_registry_id distinto, así que filtrar por
    model_registry_id ya filtra implícitamente por segmento.

    Returns:
        {
          "consolidated": {
              "cohort_week": date,                # = calculation_week - primary_lag
              "decile_table": pd.DataFrame | None,
              "rates_by_target": dict[str, list[float | None]],
              "missing_targets": list[str],       # targets con lag > primary_lag
              "available": bool,
          },
          "per_target": {
              tname: {
                  "cohort_week": date,
                  "decile_table": pd.DataFrame | None,
                  "available": bool,
                  "reason": str | None,
              }
              ...
          }
        }
    """
    primary_cohort = calculation_week - timedelta(weeks=primary_target_lag)
    primary_window = _window_weeks(primary_cohort)

    eligible_targets = [
        t for t in all_targets
        if (t.lag_semanas or 0) <= primary_target_lag
    ]
    missing_targets = [
        t.variable_name for t in all_targets
        if (t.lag_semanas or 0) > primary_target_lag
    ]

    consolidated: dict = {
        "cohort_week": primary_cohort,
        "cohort_window_start": primary_window[-1],
        "cohort_window_end": primary_cohort,
        "decile_table": None,
        "rates_by_target": {},
        "missing_targets": missing_targets,
        "available": False,
    }

    flags_by_target: dict[str, pd.DataFrame] = {}
    for t in eligible_targets:
        rows = (
            session.query(FactPerformanceIndividual)
            .filter(
                FactPerformanceIndividual.model_registry_id == model_registry_id,
                FactPerformanceIndividual.origination_week.in_(primary_window),
                FactPerformanceIndividual.ventana == t.variable_name,
            )
            .all()
        )
        if rows:
            flags_by_target[t.variable_name] = pd.DataFrame([
                {"credito_id": r.credito_id, "fnpuntaje": r.fnpuntaje, "flag": r.flag}
                for r in rows
            ])

    if flags_by_target:
        ref_target = next(iter(flags_by_target))
        ref_df = flags_by_target[ref_target].dropna(subset=["fnpuntaje"]).reset_index(drop=True)
        if len(ref_df) >= min_obs:
            ref_df["bucket"] = pd.qcut(
                ref_df["fnpuntaje"], q=N_DECILES, labels=False, duplicates="drop",
            )
            base = (
                ref_df.groupby("bucket", observed=True)
                      .agg(
                          score_min=("fnpuntaje", "min"),
                          score_max=("fnpuntaje", "max"),
                          score_mean=("fnpuntaje", "mean"),
                          n_total=("flag", "size"),
                      )
                      .reset_index(drop=False)
            )
            base["decile"] = np.arange(1, len(base) + 1)
            total = base["n_total"].sum()
            base["pct_population"] = base["n_total"] / total if total else 0.0

            rates_by_target: dict[str, list] = {}
            for tname, tdf in flags_by_target.items():
                merged = ref_df[["credito_id", "bucket"]].merge(
                    tdf[["credito_id", "flag"]],
                    on="credito_id",
                    how="left",
                )
                rates = (
                    merged.groupby("bucket", observed=True)["flag"]
                          .mean()
                          .reindex(base["bucket"])
                )
                rates_by_target[tname] = rates.tolist()

            consolidated["decile_table"] = base.drop(columns=["bucket"])
            consolidated["rates_by_target"] = rates_by_target
            consolidated["available"] = True
        else:
            consolidated["available"] = False

    per_target: dict[str, dict] = {}
    for t in all_targets:
        cohort = calculation_week - timedelta(weeks=t.lag_semanas or 0)
        window = _window_weeks(cohort)
        rows = (
            session.query(FactPerformanceIndividual)
            .filter(
                FactPerformanceIndividual.model_registry_id == model_registry_id,
                FactPerformanceIndividual.origination_week.in_(window),
                FactPerformanceIndividual.ventana == t.variable_name,
            )
            .all()
        )
        if not rows:
            per_target[t.variable_name] = {
                "cohort_week": cohort,
                "cohort_window_start": window[-1],
                "cohort_window_end": cohort,
                "decile_table": None,
                "available": False,
                "reason": "Cohorte sin datos",
            }
            continue

        df = pd.DataFrame([{"score": r.fnpuntaje, "flag": r.flag} for r in rows])
        if len(df.dropna(subset=["score"])) < min_obs:
            per_target[t.variable_name] = {
                "cohort_week": cohort,
                "cohort_window_start": window[-1],
                "cohort_window_end": cohort,
                "decile_table": None,
                "available": False,
                "reason": f"n={len(df)} < {min_obs}",
            }
            continue

        table = compute_decile_table(df["score"], df["flag"])
        per_target[t.variable_name] = {
            "cohort_week": cohort,
            "cohort_window_start": window[-1],
            "cohort_window_end": cohort,
            "decile_table": table,
            "available": not table.empty,
            "reason": None if not table.empty else "qcut vacío",
        }

    return {"consolidated": consolidated, "per_target": per_target}


def persist_deciles_history(
    session: Session,
    model_registry_id: int,
    calculation_week: date,
    decile_data: dict,
) -> int:
    """Persiste deciles per-target en FACT_DECILES_HISTORY (idempotente).

    Usa el bloque per_target del output de get_decile_data_for_segment.
    El bloque consolidated NO se persiste como tal (es derivado: cada target
    tiene su propia tabla individual + rates_by_target se reconstruye desde
    los individuales si se necesita).

    Idempotencia: la unique constraint
    (model_registry_id, calculation_week, target_variable, decile)
    rechaza inserts duplicados; pre-borramos antes de insertar para permitir
    re-runs con datos actualizados.
    """
    pt_data = decile_data.get("per_target", {})
    if not pt_data:
        return 0

    targets_with_data = [
        tname for tname, p in pt_data.items()
        if p.get("available") and p.get("decile_table") is not None
        and not p["decile_table"].empty
    ]
    if not targets_with_data:
        return 0

    # Idempotencia: borrar filas previas de esta combinación antes de insertar.
    (
        session.query(FactDecilesHistory)
        .filter(
            FactDecilesHistory.model_registry_id == model_registry_id,
            FactDecilesHistory.calculation_week == calculation_week,
            FactDecilesHistory.target_variable.in_(targets_with_data),
        )
        .delete(synchronize_session=False)
    )

    rows: list[FactDecilesHistory] = []
    for tname in targets_with_data:
        p = pt_data[tname]
        table = p["decile_table"]
        win_start = p["cohort_window_start"]
        win_end = p["cohort_window_end"]
        for _, r in table.iterrows():
            rows.append(FactDecilesHistory(
                model_registry_id=model_registry_id,
                calculation_week=calculation_week,
                target_variable=tname,
                cohort_window_start=win_start,
                cohort_window_end=win_end,
                decile=int(r["decile"]),
                score_min=float(r["score_min"]),
                score_max=float(r["score_max"]),
                score_mean=float(r["score_mean"]),
                n_obs=int(r["n_total"]),
                n_events=int(r["n_event"]),
                event_rate=float(r["event_rate"]),
                pct_population=float(r["pct_population"]) if pd.notna(r["pct_population"]) else None,
            ))

    if rows:
        session.add_all(rows)
        session.flush()
    return len(rows)


def check_decile_ordering_violations(
    decile_table: pd.DataFrame,
    ascending: bool,
    tol: float = 0.001,
) -> dict:
    """Cuenta violaciones de monotonía sobre event_rate ordenado por decil.

    Convención de deciles (ver get_decile_data_for_segment): decile 1 = scores
    más bajos = mayor riesgo. Para targets de incumplimiento (ascending=False)
    el event_rate debe DECRECER conforme decile sube. Para targets crecientes
    (ascending=True) debe CRECER.

    Tolerancia: diferencias menores a `tol` en valor absoluto no se cuentan
    (ruido). Default 0.001 (0.1pp): los `event_rate` típicos viven en 0.03-0.20,
    así que esta tolerancia detecta inversiones reales visibles en la gráfica
    sin disparar por ruido de muestreo. La versión legacy sobre bins fijos
    usaba 0.005 porque comparaba sobre 21 buckets más estrechos; en deciles
    cada bucket es 10% de la población y los efectos de muestreo son menores.

    Args:
        decile_table: DataFrame con columnas `decile` y `event_rate` (al menos).
        ascending: True si event_rate debe crecer con decile, False si debe decrecer.
        tol: tolerancia para considerar una diferencia como ruido.

    Returns:
        {"violations": int, "violation_pairs": [
            {decile_low, decile_high, value_low, value_high}, ...
        ]}
    """
    if decile_table is None or decile_table.empty:
        return {"violations": 0, "violation_pairs": []}

    df = decile_table.sort_values("decile").reset_index(drop=True)
    deciles = df["decile"].tolist()
    values = df["event_rate"].tolist()

    violations = 0
    violation_pairs: list[dict] = []
    for i in range(len(values) - 1):
        v_i = values[i]
        v_j = values[i + 1]
        if v_i is None or v_j is None or pd.isna(v_i) or pd.isna(v_j):
            continue

        is_violation = (
            v_j < v_i - tol if ascending else v_j > v_i + tol
        )
        if is_violation:
            violations += 1
            violation_pairs.append({
                "decile_low": int(deciles[i]),
                "decile_high": int(deciles[i + 1]),
                "value_low": round(float(v_i), 4),
                "value_high": round(float(v_j), 4),
            })

    return {"violations": violations, "violation_pairs": violation_pairs}


def load_per_target_deciles(
    session: Session,
    model_registry_id: int,
    calculation_week: date,
) -> dict[str, dict]:
    """Reconstruye el bloque per_target del dict de get_decile_data_for_segment
    leyendo de FACT_DECILES_HISTORY (sin tocar FACT_PERFORMANCE_INDIVIDUAL).

    Para cada target persistido, devuelve un sub-dict con la misma forma que
    el original: cohort_window_start/end, decile_table como DataFrame, y
    available=True. Los targets sin filas no aparecen en el dict (igual que
    el comportamiento original cuando no hay datos).

    Returns:
        {tname: {cohort_week, cohort_window_start, cohort_window_end,
                decile_table: pd.DataFrame, available: True, reason: None}}
    """
    rows = (
        session.query(FactDecilesHistory)
        .filter(
            FactDecilesHistory.model_registry_id == model_registry_id,
            FactDecilesHistory.calculation_week == calculation_week,
        )
        .all()
    )
    if not rows:
        return {}

    by_target: dict[str, list] = {}
    for r in rows:
        by_target.setdefault(r.target_variable, []).append(r)

    out: dict[str, dict] = {}
    for tname, trows in by_target.items():
        trows_sorted = sorted(trows, key=lambda r: r.decile)
        df = pd.DataFrame([
            {
                "decile": r.decile,
                "score_min": r.score_min,
                "score_max": r.score_max,
                "score_mean": r.score_mean,
                "n_total": r.n_obs,
                "n_event": r.n_events,
                "event_rate": r.event_rate,
                "pct_population": r.pct_population,
            }
            for r in trows_sorted
        ])
        first = trows_sorted[0]
        out[tname] = {
            "cohort_week": first.cohort_window_end,
            "cohort_window_start": first.cohort_window_start,
            "cohort_window_end": first.cohort_window_end,
            "decile_table": df,
            "available": True,
            "reason": None,
        }
    return out
