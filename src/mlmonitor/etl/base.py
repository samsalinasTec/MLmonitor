"""
ETL Skeleton — ABCs para el pipeline de extracción, transformación y carga.

NOTA: Este es un esqueleto. La lógica de extracción real desde fuentes
de producción (Oracle, S3, APIs, etc.) se implementará en subclases.
"""

from abc import ABC, abstractmethod

import pandas as pd


class BaseExtractor(ABC):
    """
    Extractor base — lee datos desde una fuente externa.

    Implementar para: Oracle DB, S3, APIs REST, archivos planos, etc.
    """

    @abstractmethod
    def extract(self, **kwargs) -> pd.DataFrame:
        """
        Extrae datos desde la fuente y retorna un DataFrame.

        Args:
            **kwargs: Parámetros específicos de la fuente
                      (fecha, segmento, tabla, query, etc.)

        Returns:
            pd.DataFrame con los datos extraídos.
        """
        ...

    def validate_schema(self, df: pd.DataFrame, required_columns: list[str]) -> None:
        """Valida que el DataFrame tiene las columnas requeridas."""
        missing = set(required_columns) - set(df.columns)
        if missing:
            raise ValueError(
                f"Columnas faltantes en DataFrame: {missing}"
            )


class BaseTransformer(ABC):
    """
    Transformer base — transforma y limpia datos extraídos.

    Implementar para: normalización de scores, mapeo de variables,
    cálculo de bins, imputación, etc.
    """

    @abstractmethod
    def transform(self, df: pd.DataFrame, **kwargs) -> pd.DataFrame:
        """
        Transforma el DataFrame aplicando reglas de negocio.

        Args:
            df: DataFrame de entrada
            **kwargs: Parámetros adicionales de transformación

        Returns:
            pd.DataFrame transformado.
        """
        ...


class BaseLoader(ABC):
    """
    Loader base — carga datos transformados en la base de datos de monitoreo.

    Implementar para: carga incremental a FACT_DISTRIBUTIONS,
    FACT_PERFORMANCE_OUTCOMES, etc.
    """

    @abstractmethod
    def load(self, df: pd.DataFrame, target_table: str, **kwargs) -> int:
        """
        Carga el DataFrame en la tabla destino.

        Args:
            df: DataFrame a cargar
            target_table: Nombre de la tabla destino
            **kwargs: Parámetros adicionales (batch_size, upsert_keys, etc.)

        Returns:
            Número de filas insertadas.
        """
        ...
