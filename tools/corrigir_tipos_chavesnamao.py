"""
Converte para texto as colunas listing_id e anunciante_id dos Parquet do Chaves na Mão
gravados antes da correção (o portal manda os IDs como número; a tabela do Athena os
define como texto, e um arquivo com int64 nessas colunas faz o Athena recusar qualquer
consulta que o leia). Só reescreve os arquivos que precisam; os dados não mudam.
Pode rodar mais de uma vez (os já corrigidos são pulados).

Uso (dentro de scraper-imoveis, com o .env):
    python tools/corrigir_tipos_chavesnamao.py
"""
import io
import sys
from concurrent.futures import ThreadPoolExecutor

import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, ".")
from storage.s3_storage import S3Storage, _id_texto  # noqa: E402

COLUNAS = ("listing_id", "anunciante_id")
storage = S3Storage()
s3, bucket = storage.s3, storage.bucket


def corrigir(key: str) -> bool:
    tabela = pq.read_table(io.BytesIO(s3.get_object(Bucket=bucket, Key=key)["Body"].read()))
    mudou = False
    for col in COLUNAS:
        if col in tabela.column_names and not pa.types.is_string(tabela.schema.field(col).type):
            valores = pa.array([_id_texto(v) for v in tabela.column(col).to_pylist()], type=pa.string())
            tabela = tabela.set_column(tabela.column_names.index(col), col, valores)
            mudou = True
    if mudou:
        buf = io.BytesIO()
        pq.write_table(tabela, buf)
        s3.put_object(Bucket=bucket, Key=key, Body=buf.getvalue())
    return mudou


chaves = []
for pagina in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix="imoveis/portal=chavesnamao/"):
    chaves += [o["Key"] for o in pagina.get("Contents", []) if o["Key"].endswith(".parquet")]
with ThreadPoolExecutor(16) as pool:
    corrigidos = sum(pool.map(corrigir, chaves))
print(f"{corrigidos} de {len(chaves)} arquivos corrigidos")
