"""
Variable name mapping between SERC system (uppercase, no separators)
and canonical scorecard names (lowercase, underscored).

Built from cross-referencing variables_serc.csv with Variables_por_segmento.xlsx.
"""

# Explicit mapping for names that can't be resolved by simple normalization.
# SERC name -> canonical name
_SPECIAL_CASES: dict[str, str] = {
    "SEXO": "fisexo",
    "PORCFCONSTCDC12M": "porc_f_cons_cdc_12m",
    "PORCFCONSTCDC3M": "porc_f_cons_cdc_3m",
}

# Canonical variable names per segment (from Variables_por_segmento.xlsx).
# These are the scorecard input variables that should be monitored.
CANONICAL_VARIABLES: dict[int, list[str]] = {
    1: ["cp_mean_ti_8_13_rez", "cp_mean_ti_4_6_rez", "edo_mean_ti_8_13_rez", "edad", "fisexo"],
    2: ["cp_mean_ti_8_13_rez", "porc_f_cons_cdc_12m", "utilizacion", "edo_median_ti_8_13_rez", "icv", "edad", "ctas_pago_min_cerradas"],
    3: ["ko3sv", "max_mop_52s", "util_targethold", "tpf_cons_cdc_12m", "ko1_ant", "min_sem_ultcomp", "maxantcard", "ctas_pago_min_cerradas", "sum_abiertas_26s", "edad"],
    4: ["num_prom_dif_ses", "avg_mto_conveniencia", "std_num_ret_cliente_6m", "avg_num_canal_cajero_6", "fisexo", "prom_txn_compra_12m", "mto_dep_nomina_n6", "sld_prom_mensual_n6", "mto_dispo_atm_3m", "edad", "porc_oot_cons_cdc_12m"],
    5: ["maxantcard", "min_sld_tot_prod_cap_6m", "num_prom_dif_ses", "edo_median_ti_4_6_rez", "std_num_ret_cliente_6m", "ko3sv", "min_sem_ultcomp", "prom_txn_compra_12m", "sld_prom_mensual_n6", "edad", "ant_cap", "tpf_cons_cdc_12m", "cp_mean_ti_8_13_rez", "ctas_pago_min_cerradas", "avg_mto_internet", "util_targethold", "sld_tot_prod_cap_3"],
    6: ["edad", "num_prom_dif_ses", "ant_cap", "edo_mean_conteos_rez", "ind_cuenta_capta", "min_sld_tot_prod_cap_6m", "sld_vista", "num_conveniencia", "cp_mean_ti_8_13_rez", "avg_mto_internet"],
    7: ["ko3sv", "edad", "ko1_ant", "min_sem_ultcomp", "cp_mean_ti_8_13_rez", "ctas_pago_min_cerradas", "tpf_cons_cdc_12m", "mean_semvida_card", "monto_venta_pesos"],
    8: ["porc_oot_cons_cdc_12m", "maxantinstalm", "edad", "porc_baz_cons_cdc_12m", "monto_u12meses", "porc_oot_cons_cdc_3m", "porc_tc_cons_cdc_12m"],
    9: ["ant_cap", "fisexo", "cp_conteos_rez", "cp_mean_ti_8_13_rez", "edad", "edo_mean_ti_8_13_rez", "monto_eeuu_u12meses"],
    10: ["maxantcard", "ant_cap", "tpf_cons_cdc_6m", "ko3sv", "utilizacion", "edad", "ctas_pago_min_cerradas"],
    11: ["cp_mean_ti_8_13_rez", "cp_mean_malos_8_13_rez", "edo_median_ti_8_13_rez", "edad", "fisexo"],
}

# Variables in SERC that are NOT in the canonical scorecard list.
# These are intermediate/supporting variables used during scoring but not
# part of the main scorecard. Documented here for the credit team.
EXTRA_SERC_VARIABLES: list[str] = [
    "ANTDIG",
    "AVGNUMGUARDADITO6",
    "AVGNUMSEMANASACT",
    "AVGSLDGUARDADITO6",
    "COCTOTCONSCDC3M12M",
    "CPTI813REZ",
    "DIASULTDISP",
    "DISPMENSUAL",
    "EDOMEDIANCONTEOSREZ",
    "ICVPC",
    "INDSINCUENTA",
    "MAXSEMULTPAGAACTIVAS",
    "MONTOCOMPRADOLARES",
    "MONTOPAGO",
    "MTODEPINTERES12M",
    "NUFAM2",
    "NUMDEPNOMINAN3",
    "NUMNOMONETARIAS",
    "NUMPRESTAMOSPERS",
    "NUMTIENDADESC",
    "OOTCONSCDC12M",
    "OOTCONSCDC3M",
    "PCTTOTVIDA13S",
    "PROMTXNDEPINTERES12M",
    "SLDFINMESN3",
    "SLDFINMESN6",
    "SLDPROMMENSUAL",
    "SLDPROMMES",
    "TXNCOMPRA",
]

# Segment ID -> group name (from fcnombresegmento21 in SERC)
SEGMENT_GROUP_NAMES: dict[int, str] = {
    1: "NO FILES",
    2: "THIN FILES",
    3: "BIG FILES",
    4: "THIN FILES",
    5: "BIG FILES",
    6: "NO FILES",
    7: "BIG FILES",
    8: "THIN FILES",
    9: "NO FILES",
    10: "BIG FILES",
    11: "NO FILES",
}

# Feature counts per segment (from MetaModelRegistry.xlsx)
SEGMENT_FEATURE_COUNTS: dict[int, int] = {
    1: 5, 2: 7, 3: 10, 4: 11, 5: 17, 6: 10, 7: 9, 8: 7, 9: 7, 10: 7, 11: 5,
}


def _strip_normalize(name: str) -> str:
    """Uppercase, strip underscores — used for fuzzy matching only."""
    return name.upper().replace("_", "")


def _build_reverse_map() -> dict[str, str]:
    """Build a lookup: normalized-SERC-name -> canonical name."""
    reverse: dict[str, str] = {}
    for seg_vars in CANONICAL_VARIABLES.values():
        for canonical in seg_vars:
            key = _strip_normalize(canonical)
            if key not in reverse:
                reverse[key] = canonical
    return reverse


_REVERSE_MAP = _build_reverse_map()


def serc_to_canonical(serc_name: str) -> str | None:
    """
    Convert a SERC variable name to its canonical form.

    Returns None if the variable is not in the canonical scorecard list
    (e.g. INTERCEPTO or extra intermediate variables).
    """
    upper = serc_name.upper()

    if upper == "INTERCEPTO":
        return None

    if upper in _SPECIAL_CASES:
        return _SPECIAL_CASES[upper]

    key = _strip_normalize(upper)
    return _REVERSE_MAP.get(key)


def get_canonical_variables_for_segment(segment_id: int) -> list[str]:
    """Return the canonical variable list for a given segment ID (1-11)."""
    return CANONICAL_VARIABLES.get(segment_id, [])
