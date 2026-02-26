"""
S3Uploader — Sube el reporte PDF a un bucket S3.
"""

from pathlib import Path


class S3Uploader:
    """Sube archivos a S3. Requiere boto3 y credenciales AWS configuradas."""

    def __init__(self, bucket: str, prefix: str = "mlmonitor/reports", region: str = "us-east-1"):
        self.bucket = bucket
        self.prefix = prefix.rstrip("/")
        self.region = region
        self._client = None

    def _get_client(self):
        if self._client is None:
            import boto3
            self._client = boto3.client("s3", region_name=self.region)
        return self._client

    def upload(self, local_path: Path) -> str:
        """
        Sube un archivo a S3.

        Args:
            local_path: Ruta local del archivo a subir.

        Returns:
            URI S3 del archivo subido (s3://bucket/prefix/filename).
        """
        if not local_path.exists():
            raise FileNotFoundError(f"Archivo no encontrado: {local_path}")

        key = f"{self.prefix}/{local_path.name}"
        self._get_client().upload_file(
            Filename=str(local_path),
            Bucket=self.bucket,
            Key=key,
            ExtraArgs={"ContentType": "application/pdf"},
        )
        s3_uri = f"s3://{self.bucket}/{key}"
        print(f"[S3Uploader] Subido: {s3_uri}")
        return s3_uri

    @classmethod
    def from_settings(cls) -> "S3Uploader":
        from config.settings import settings
        return cls(
            bucket=settings.s3_bucket,
            prefix=settings.s3_prefix,
            region=settings.aws_region,
        )
