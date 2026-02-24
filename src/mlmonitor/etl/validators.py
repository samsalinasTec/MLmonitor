"""
DataQualityValidator — Framework de validación de calidad de datos.

NOTA: Esqueleto con métodos definidos pero sin lógica de validación compleja.
Las implementaciones concretas se completarán según las fuentes de datos de producción.
"""

from dataclasses import dataclass, field
from typing import Any

import pandas as pd


@dataclass
class ValidationResult:
    """Resultado de una validación de calidad de datos."""
    check_name: str
    passed: bool
    details: dict[str, Any] = field(default_factory=dict)
    message: str = ""


class DataQualityValidator:
    """
    Validador de calidad de datos para el pipeline ETL.

    Provee un framework extensible para validar DataFrames antes de cargarlos
    en las tablas FACT de monitoreo.
    """

    def __init__(self, df: pd.DataFrame):
        self.df = df
        self.results: list[ValidationResult] = []

    def check_nulls(
        self,
        columns: list[str] | None = None,
        max_null_rate: float = 0.05,
    ) -> ValidationResult:
        """
        Verifica que la tasa de nulos por columna no exceda el umbral.

        Args:
            columns: columnas a verificar (None = todas)
            max_null_rate: tasa máxima de nulos permitida (0.0-1.0)
        """
        cols = columns or list(self.df.columns)
        null_rates = {}
        violations = []

        for col in cols:
            if col not in self.df.columns:
                continue
            rate = self.df[col].isna().mean()
            null_rates[col] = round(float(rate), 4)
            if rate > max_null_rate:
                violations.append(col)

        passed = len(violations) == 0
        result = ValidationResult(
            check_name="check_nulls",
            passed=passed,
            details={
                "null_rates": null_rates,
                "violations": violations,
                "max_null_rate": max_null_rate,
            },
            message=(
                f"OK: todas las columnas dentro del umbral ({max_null_rate:.0%})"
                if passed
                else f"FAIL: columnas con exceso de nulos: {violations}"
            ),
        )
        self.results.append(result)
        return result

    def check_ranges(
        self,
        column_ranges: dict[str, tuple[float, float]],
    ) -> ValidationResult:
        """
        Verifica que los valores de columnas numéricas estén dentro de rangos.

        Args:
            column_ranges: {column_name: (min_value, max_value)}
        """
        violations = []
        details = {}

        for col, (min_val, max_val) in column_ranges.items():
            if col not in self.df.columns:
                details[col] = {"status": "column_not_found"}
                continue

            series = self.df[col].dropna()
            out_of_range = ((series < min_val) | (series > max_val)).sum()
            pct = float(out_of_range / len(self.df)) if len(self.df) > 0 else 0.0

            details[col] = {
                "min": float(series.min()) if len(series) > 0 else None,
                "max": float(series.max()) if len(series) > 0 else None,
                "expected_range": (min_val, max_val),
                "out_of_range_count": int(out_of_range),
                "out_of_range_pct": round(pct, 4),
            }
            if out_of_range > 0:
                violations.append(col)

        passed = len(violations) == 0
        result = ValidationResult(
            check_name="check_ranges",
            passed=passed,
            details={"column_details": details, "violations": violations},
            message=(
                "OK: todos los valores en rango"
                if passed
                else f"FAIL: valores fuera de rango en: {violations}"
            ),
        )
        self.results.append(result)
        return result

    def check_duplicates(
        self,
        key_columns: list[str],
    ) -> ValidationResult:
        """
        Verifica que no existan registros duplicados según las columnas clave.

        Args:
            key_columns: columnas que forman la clave única
        """
        existing_cols = [c for c in key_columns if c in self.df.columns]
        if not existing_cols:
            result = ValidationResult(
                check_name="check_duplicates",
                passed=True,
                details={"message": "No se encontraron columnas clave en el DataFrame"},
            )
            self.results.append(result)
            return result

        duplicates = self.df.duplicated(subset=existing_cols, keep=False)
        dup_count = int(duplicates.sum())

        passed = dup_count == 0
        result = ValidationResult(
            check_name="check_duplicates",
            passed=passed,
            details={
                "key_columns": existing_cols,
                "duplicate_count": dup_count,
                "total_rows": len(self.df),
            },
            message=(
                "OK: sin duplicados"
                if passed
                else f"FAIL: {dup_count} filas duplicadas según columnas: {existing_cols}"
            ),
        )
        self.results.append(result)
        return result

    def check_schema(
        self,
        expected_schema: dict[str, type],
    ) -> ValidationResult:
        """
        Verifica que el DataFrame tenga las columnas esperadas con los tipos correctos.

        Args:
            expected_schema: {column_name: expected_dtype_class}
                             e.g., {"score": float, "segment_id": str}
        """
        missing_columns = []
        type_mismatches = []
        details = {}

        for col, expected_type in expected_schema.items():
            if col not in self.df.columns:
                missing_columns.append(col)
                details[col] = {"status": "missing"}
                continue

            actual_dtype = self.df[col].dtype
            # Verificación flexible de tipos
            type_ok = self._check_dtype_compatible(actual_dtype, expected_type)
            details[col] = {
                "expected": str(expected_type.__name__),
                "actual": str(actual_dtype),
                "compatible": type_ok,
            }
            if not type_ok:
                type_mismatches.append(col)

        passed = not missing_columns and not type_mismatches
        result = ValidationResult(
            check_name="check_schema",
            passed=passed,
            details={
                "schema_details": details,
                "missing_columns": missing_columns,
                "type_mismatches": type_mismatches,
            },
            message=(
                "OK: esquema válido"
                if passed
                else f"FAIL: columnas faltantes: {missing_columns}, "
                     f"tipos incorrectos: {type_mismatches}"
            ),
        )
        self.results.append(result)
        return result

    def _check_dtype_compatible(self, actual_dtype, expected_type: type) -> bool:
        """Verifica compatibilidad flexible entre dtypes de pandas y tipos Python."""
        import numpy as np

        if expected_type in (int, float):
            return pd.api.types.is_numeric_dtype(actual_dtype)
        elif expected_type == str:
            return pd.api.types.is_string_dtype(actual_dtype) or \
                   actual_dtype == object
        elif expected_type == bool:
            return pd.api.types.is_bool_dtype(actual_dtype)
        return True  # fallback: aceptar

    def get_summary(self) -> dict:
        """Retorna un resumen de todas las validaciones ejecutadas."""
        return {
            "total_checks": len(self.results),
            "passed": sum(1 for r in self.results if r.passed),
            "failed": sum(1 for r in self.results if not r.passed),
            "checks": [
                {
                    "name": r.check_name,
                    "passed": r.passed,
                    "message": r.message,
                }
                for r in self.results
            ],
        }

    def all_passed(self) -> bool:
        """Retorna True si todas las validaciones pasaron."""
        return all(r.passed for r in self.results)
