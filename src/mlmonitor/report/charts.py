"""Renderizado matplotlib de gráficas para el PDF (PNG inline base64).

Convenciones:
- Backend Agg (headless), seteado antes de importar pyplot.
- Funciones devuelven base64 SIN prefijo `data:image/png;base64,` — el template
  añade el prefijo. Esto facilita tests y mantiene el dato puro.
- Paleta consistente con styles.css.
"""

import base64
from datetime import date
from io import BytesIO

import matplotlib

matplotlib.use("Agg")  # headless — antes de pyplot

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

COLOR_BAR = "#cbd5e1"
COLOR_LINES = ["#1a1a3e", "#06b6d4", "#8b5cf6", "#ef4444"]
DPI = 150


def _fig_to_base64(fig) -> str:
    buf = BytesIO()
    fig.savefig(buf, format="png", dpi=DPI, bbox_inches="tight")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def render_consolidated_decile_chart(
    decile_table: pd.DataFrame,
    rates_by_target: dict[str, list],
    cohort_week: date,
    primary_target: str,
    segment_id: str,
) -> str:
    """Barras de población (10% por decil) + N líneas de tasa de impago.

    rates_by_target: {target_name: [rate_d1, rate_d2, ..., rate_dn]}.
    """
    n = len(decile_table)
    x = np.arange(1, n + 1)
    fig, ax_left = plt.subplots(figsize=(7.5, 4.0))

    pop_pct = 100.0 / n if n else 0.0
    ax_left.bar(
        x,
        [pop_pct] * n,
        color=COLOR_BAR,
        alpha=0.6,
        edgecolor="white",
        label="% población",
    )
    ax_left.set_xlabel("Decil de score (1 = score más bajo, mayor riesgo)")
    ax_left.set_ylabel("% de población")
    ax_left.set_xticks(x)
    ax_left.set_ylim(0, max(15.0, pop_pct + 5.0))

    ax_right = ax_left.twinx()
    ax_right.set_ylabel("Tasa de impago (%)")
    for i, (tname, rates) in enumerate(rates_by_target.items()):
        rates_pct = [
            r * 100 if r is not None and not pd.isna(r) else np.nan
            for r in rates
        ]
        ax_right.plot(
            x,
            rates_pct,
            marker="o",
            linewidth=2,
            color=COLOR_LINES[i % len(COLOR_LINES)],
            label=tname,
        )
    ax_right.set_ylim(bottom=0)

    fig.suptitle(
        f"Segmento {segment_id} — Tasa por decil "
        f"(cohorte {cohort_week.isoformat()}, primary={primary_target})",
        fontsize=11,
        color="#1a1a3e",
    )

    h1, l1 = ax_left.get_legend_handles_labels()
    h2, l2 = ax_right.get_legend_handles_labels()
    ax_left.legend(h1 + h2, l1 + l2, loc="upper right", fontsize=8, framealpha=0.9)

    return _fig_to_base64(fig)


def render_per_target_decile_chart(
    per_target: dict[str, dict],
    segment_id: str,
) -> str:
    """Subplots horizontales: uno por target activo, con su cohorte propia."""
    targets = list(per_target.items())
    n_panels = len(targets)
    fig, axes = plt.subplots(
        1, n_panels, figsize=(5.0 * n_panels, 3.8), squeeze=False,
    )
    axes = axes[0]

    for i, (tname, payload) in enumerate(targets):
        ax_left = axes[i]
        if not payload["available"]:
            ax_left.text(
                0.5,
                0.5,
                f"{tname}\nCohorte no disponible\n({payload.get('reason', '')})",
                ha="center",
                va="center",
                fontsize=9,
                color="#6b7280",
                transform=ax_left.transAxes,
            )
            ax_left.set_xticks([])
            ax_left.set_yticks([])
            ax_left.set_title(tname, fontsize=10)
            continue

        table = payload["decile_table"]
        n = len(table)
        x = np.arange(1, n + 1)
        pop_pct = 100.0 / n if n else 0.0
        ax_left.bar(x, [pop_pct] * n, color=COLOR_BAR, alpha=0.6, edgecolor="white")
        ax_left.set_xlabel("Decil")
        ax_left.set_ylabel("% población")
        ax_left.set_xticks(x)
        ax_left.set_ylim(0, max(15.0, pop_pct + 5.0))

        ax_right = ax_left.twinx()
        rates = (table["event_rate"] * 100).tolist()
        ax_right.plot(
            x,
            rates,
            marker="o",
            linewidth=2,
            color=COLOR_LINES[i % len(COLOR_LINES)],
            label=tname,
        )
        ax_right.set_ylabel("Tasa de impago (%)")
        ax_right.set_ylim(bottom=0)
        ax_left.set_title(
            f"{tname} — cohorte {payload['cohort_week'].isoformat()}",
            fontsize=10,
            color="#1a1a3e",
        )

    fig.suptitle(
        f"Segmento {segment_id} — deciles por target (cohorte propia)",
        fontsize=11,
        color="#1a1a3e",
    )
    fig.tight_layout(rect=[0, 0, 1, 0.94])
    return _fig_to_base64(fig)
