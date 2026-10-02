"""
Confere cada Parquet da coleta nova (imoveis/portal=*/coleta=...) contra os tipos da
tabela imoveis.anuncios no Athena e converte as colunas com tipo diferente. Um arquivo
com tipo errado (ex.: listing_id gravado como número numa coluna de texto, ou
qualidade_anuncio inteiro numa coluna double) faz o Athena recusar qualquer consulta
que leia essa coluna. Os valores não mudam, só o tipo; só os arquivos com problema
são reescritos. Os dados antigos (vivareal/estado=...) não são tocados.
Pode rodar mais de uma vez (os já corretos são pulados).

Uso (dentro de scraper-imoveis, com o .env):
    python tools/corrigir_tipos_parquet.py                  # todos os portais
    python tools/corrigir_tipos_parquet.py chavesnamao      # só um portal
"""
import io
import struct
import sys
from concurrent.futures import ThreadPoolExecutor

import boto3
import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, ".")
from config.settings import settings  # noqa: E402
from storage.s3_storage import S3Storage, _id_texto  # noqa: E402

storage = S3Storage()
s3, bucket = storage.s3, storage.bucket
glue = boto3.client("glue", aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
                    aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY, region_name=settings.AWS_REGION)
TIPOS = {c["Name"]: c["Type"] for c in
         glue.get_table(DatabaseName="imoveis", Name="anuncios")["Table"]["StorageDescriptor"]["Columns"]}
ALVO = {"string": pa.string(), "double": pa.float64(), "bigint": pa.int64(), "boolean": pa.bool_()}


def converter(coluna: pa.ChunkedArray, tipo: pa.DataType) -> pa.Array:
    if pa.types.is_string(tipo):
        return pa.array([_id_texto(v) for v in coluna.to_pylist()], type=tipo)
    return coluna.cast(tipo)


def precisa(nome: str, tipo: pa.DataType) -> bool:
    alvo = ALVO.get(TIPOS.get(nome, ""))
    return alvo is not None and tipo != alvo and not pa.types.is_null(tipo)


def esquema(key: str, tamanho: int) -> pa.Schema:
    """Só o rodapé do Parquet (onde fica o esquema), sem baixar o arquivo inteiro."""
    fim = s3.get_object(Bucket=bucket, Key=key, Range=f"bytes={max(0, tamanho - 65536)}-")["Body"].read()
    n = struct.unpack("<I", fim[-8:-4])[0]
    if n + 8 > len(fim):
        fim = s3.get_object(Bucket=bucket, Key=key, Range=f"bytes={max(0, tamanho - n - 8)}-")["Body"].read()
    return pq.ParquetFile(io.BytesIO(b"PAR1" + fim[-(n + 8):])).schema_arrow


def corrigir(objeto: dict) -> bool:
    key = objeto["Key"]
    if not any(precisa(f.name, f.type) for f in esquema(key, objeto["Size"])):
        return False
    tabela = pq.ParquetFile(io.BytesIO(s3.get_object(Bucket=bucket, Key=key)["Body"].read())).read()
    mudou = False
    for nome in tabela.column_names:
        if not precisa(nome, tabela.schema.field(nome).type):
            continue
        alvo = ALVO[TIPOS[nome]]
        tabela = tabela.set_column(tabela.column_names.index(nome), nome, converter(tabela.column(nome), alvo))
        mudou = True
    if mudou:
        buf = io.BytesIO()
        pq.write_table(tabela, buf)
        s3.put_object(Bucket=bucket, Key=key, Body=buf.getvalue())
    return mudou


portais = sys.argv[1:] or ["vivareal", "lugarcerto", "imovelweb", "chavesnamao"]
for portal in portais:
    chaves = []
    for pagina in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=f"imoveis/portal={portal}/"):
        chaves += [o for o in pagina.get("Contents", []) if o["Key"].endswith(".parquet")]
    with ThreadPoolExecutor(32) as pool:
        corrigidos = sum(pool.map(corrigir, chaves))
    print(f"{portal}: {corrigidos} de {len(chaves)} arquivos corrigidos", flush=True)
