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
        self._coletas = {}
        print(f"[S3] Conectado ao bucket: {self.bucket}", flush=True)

    def coleta_atual(self, portal: str, nova: bool = False) -> str:
        """Identificador (data de início) da coleta em andamento do portal.

        Fica em progress/{portal}/_coleta.json para que todas as execuções de uma
        mesma coleta (que leva vários dias) gravem na mesma pasta coleta=AAAA-MM-DD.
        nova=True inicia uma coleta nova (usado com --reset), exceto se já houver uma
        iniciada há menos de 24h: os vários --reset de um mesmo workflow (jobs paralelos,
        estados em sequência que passam da meia-noite) caem todos na mesma coleta."""
        if not nova and portal in self._coletas:
            return self._coletas[portal]
        key = f"progress/{portal}/_coleta.json"
        coleta = None
        try:
            atual = json.loads(self.s3.get_object(Bucket=self.bucket, Key=key)["Body"].read())
            idade = datetime.utcnow() - datetime.fromisoformat(atual.get("iniciada_em", "2000-01-01"))
            if not nova or idade.total_seconds() < 24 * 3600:
                coleta = atual["coleta"]
        except Exception:
            pass
        if not coleta:
            coleta = datetime.utcnow().strftime("%Y-%m-%d")
            self.s3.put_object(Bucket=self.bucket, Key=key, ContentType="application/json",
                               Body=json.dumps({"coleta": coleta, "iniciada_em": datetime.utcnow().isoformat()}))
            print(f"[S3] Nova coleta {portal}: coleta={coleta}", flush=True)
        self._coletas[portal] = coleta
        return coleta

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
                         "latitude", "longitude", "preco_por_m2", "aluguel_total", "baixou_preco_pct",
                         "qualidade_anuncio"]
            int_cols = ["quartos", "suites", "banheiros", "vagas", "image_count", "andar", "total_andares",
                        "idade_imovel", "unidades_por_andar", "elevadores", "salas"]
            bool_cols = ["lancamento", "localizacao_exata", "tem_tour_virtual", "tem_video", "tem_planta",
                         "imovel_disponivel", "aceita_financiamento", "anunciante_verificado", "tem_closet"]

            for col in float_cols:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce")

            for col in int_cols:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")

            for col in bool_cols:
                if col in df.columns:
                    df[col] = df[col].astype("boolean")

            # Demais colunas: sempre string. Evita (1) coluna toda nula gravada com tipo
            # "null" e (2) valores mistos (ex.: código 439851 numérico num anúncio e
            # "AC3901" em outro), que fariam o pyarrow recusar o arquivo inteiro
            for col in df.columns:
                if df[col].dtype == object:
                    df[col] = df[col].map(lambda v: None if v is None or v is pd.NA or v != v else str(v)).astype("string")

            # Adiciona metadata
            df["data_coleta"] = datetime.utcnow().isoformat()
            df["portal"] = portal

            # Normaliza nome da cidade para usar como path
            cidade_slug = self._slugify(cidade)

            # Path no S3: imoveis/portal=vivareal/coleta=2026-10-01/estado=SP/sao-paulo.parquet
            # (uma pasta por coleta: recoletar não sobrescreve a anterior)
            prefixo = f"imoveis/portal={portal}/coleta={self.coleta_atual(portal)}/estado={estado}"
            if part > 0:
                s3_key = f"{prefixo}/{cidade_slug}_part{part}.parquet"
            else:
                s3_key = f"{prefixo}/{cidade_slug}.parquet"

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

    # Os dados e o progresso da coleta antiga do VivaReal (vivareal/estado=... e
    # progress/{UF}.json) não são lidos nem gravados por este código: tudo o que é novo
    # vai para imoveis/... e progress/{portal}/...

    def save_portal_progress(self, portal: str, estado: str, data: dict):
        """Salva progresso em progress/{portal}/{estado}.json."""
        try:
            data = dict(data, estado=estado, updated_at=datetime.utcnow().isoformat())
            self.s3.put_object(
                Bucket=self.bucket,
                Key=f"progress/{portal}/{estado}.json",
                Body=json.dumps(data, ensure_ascii=False),
                ContentType="application/json",
            )
        except Exception as e:
            print(f"  [S3] Erro ao salvar progresso: {e}", flush=True)

    def get_portal_progress(self, portal: str, estado: str) -> dict:
        """Retorna progresso de outros portais (dict vazio se não existir)."""
        try:
            response = self.s3.get_object(Bucket=self.bucket, Key=f"progress/{portal}/{estado}.json")
            return json.loads(response["Body"].read())
        except Exception:
            return {}

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
