"""Armazenamento no Amazon S3 via Parquet."""
import boto3
import pandas as pd
import io
import json
from datetime import datetime
from config.settings import settings


class S3Storage:
    """Gerencia upload de dados para o S3 em formato Parquet."""

    def __init__(self):
        self.s3 = boto3.client(
            "s3",
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
            region_name=settings.AWS_REGION,
        )
        self.bucket = settings.AWS_S3_BUCKET
        print(f"[S3] Conectado ao bucket: {self.bucket}", flush=True)

    def save_anuncios(self, anuncios: list[dict], estado: str, cidade: str, portal: str = "vivareal", part: int = 0) -> bool:
        """Salva lista de anúncios como Parquet no S3."""
        if not anuncios:
            return False

        try:
            # Converte para DataFrame
            df = pd.DataFrame(anuncios)

            # Remove raw_json para economizar espaço
            if "raw_json" in df.columns:
                df = df.drop(columns=["raw_json"])

            # Força tipos consistentes para evitar erros no Athena
            float_cols = ["preco", "preco_condominio", "iptu", "area_construida", "area_terreno",
                         "latitude", "longitude", "preco_por_m2", "aluguel_total"]
            int_cols = ["quartos", "suites", "banheiros", "vagas", "image_count", "andar", "total_andares"]

            for col in float_cols:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce")

            for col in int_cols:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")

            # Adiciona metadata
            df["data_coleta"] = datetime.utcnow().isoformat()
            df["portal"] = portal

            # Normaliza nome da cidade para usar como path
            cidade_slug = self._slugify(cidade)

            # Path no S3: vivareal/estado=SP/sao-paulo.parquet ou sao-paulo_part1.parquet
            if part > 0:
                s3_key = f"{portal}/estado={estado}/{cidade_slug}_part{part}.parquet"
            else:
                s3_key = f"{portal}/estado={estado}/{cidade_slug}.parquet"

            # Converte para Parquet em memória
            buffer = io.BytesIO()
            df.to_parquet(buffer, index=False, engine="pyarrow")
            buffer.seek(0)

            # Upload para S3
            self.s3.put_object(
                Bucket=self.bucket,
                Key=s3_key,
                Body=buffer.getvalue(),
            )

            print(f"  [S3] {len(anuncios)} anúncios salvos em {s3_key}", flush=True)
            return True

        except Exception as e:
            print(f"  [S3] Erro ao salvar: {e}", flush=True)
            return False

    def save_progress(self, estado: str, last_page: int):
        """Salva progresso como JSON no S3."""
        try:
            progress = {
                "estado": estado,
                "last_page": last_page,
                "updated_at": datetime.utcnow().isoformat(),
            }
            s3_key = f"progress/{estado}.json"
            self.s3.put_object(
                Bucket=self.bucket,
                Key=s3_key,
                Body=json.dumps(progress),
                ContentType="application/json",
            )
        except Exception as e:
            print(f"  [S3] Erro ao salvar progresso: {e}", flush=True)

    def get_progress(self, estado: str) -> int:
        """Retorna última página processada."""
        try:
            s3_key = f"progress/{estado}.json"
            response = self.s3.get_object(Bucket=self.bucket, Key=s3_key)
            data = json.loads(response["Body"].read())
            return data.get("last_page", 1)
        except Exception:
            return 1

    def _slugify(self, text: str) -> str:
        """Converte texto para slug (sem acentos, lowercase, hífens)."""
        import unicodedata
        import re
        text = unicodedata.normalize("NFKD", text)
        text = text.encode("ascii", "ignore").decode("ascii")
        text = text.lower().strip()
        text = re.sub(r"[^\w\s-]", "", text)
        text = re.sub(r"[-\s]+", "-", text)
        return text
