"""
Corrige arquivos da coleta nova já gravados no S3 (só em imoveis/, nunca na coleta
antiga em vivareal/):
  - tipo do VivaReal: valores em inglês que não eram padronizados (ex.: "office",
    "allotment_land") viram as categorias comuns, a partir de property_sub_type;
  - latitude/longitude invertidas pelo anunciante (ver storage.corrigir_coordenadas).

Por padrão só simula e mostra o que mudaria. Para gravar:
    python tools/corrigir_coleta.py --aplicar
"""
import argparse
import io
import os
import sys

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from storage.s3_storage import S3Storage, corrigir_coordenadas  # noqa: E402
from scrapers.vivareal import VivaRealScraper  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--aplicar", action="store_true", help="grava as correções (sem isso, só simula)")
    args = parser.parse_args()

    storage = S3Storage()
    s3, bucket = storage.s3, storage.bucket
    tipos_vr = VivaRealScraper.TIPOS

    chaves, token = [], None
    while True:
        kw = {"Bucket": bucket, "Prefix": "imoveis/"}
        if token:
            kw["ContinuationToken"] = token
        r = s3.list_objects_v2(**kw)
        chaves += [o["Key"] for o in r.get("Contents", []) if o["Key"].endswith(".parquet")]
        if not r.get("IsTruncated"):
            break
        token = r["NextContinuationToken"]

    arquivos = linhas_tipo = linhas_coord = 0
    for chave in chaves:
        assert chave.startswith("imoveis/")  # nunca toca a coleta antiga
        corpo = s3.get_object(Bucket=bucket, Key=chave)["Body"].read()
        tabela = pq.read_table(io.BytesIO(corpo))
        # Só as colunas corrigidas passam pelo pandas; as demais ficam intactas no Arrow
        # (regravar tudo via pandas mudaria, p. ex., int com nulos para float)
        cols = [c for c in ("tipo", "property_sub_type", "latitude", "longitude") if c in tabela.column_names]
        df = tabela.select(cols).to_pandas()
        mudou_tipo = 0
        if "portal=vivareal/" in chave and {"tipo", "property_sub_type"} <= set(cols):
            novo = df["property_sub_type"].map(lambda t: tipos_vr.get(str(t).upper()) if pd.notna(t) else None)
            troca = novo.notna() & (novo != df["tipo"])
            mudou_tipo = int(troca.sum())
            df.loc[troca, "tipo"] = novo[troca]
        mudou_coord = corrigir_coordenadas(df)
        if not (mudou_tipo or mudou_coord):
            continue
        arquivos += 1
        linhas_tipo += mudou_tipo
        linhas_coord += mudou_coord
        if args.aplicar:
            for col in ("tipo", "latitude", "longitude"):
                if col in cols:
                    i = tabela.column_names.index(col)
                    tipo_arrow = tabela.schema.field(col).type
                    tabela = tabela.set_column(i, col, pa.array(df[col].tolist(), type=tipo_arrow))
            buffer = io.BytesIO()
            pq.write_table(tabela, buffer)
            s3.put_object(Bucket=bucket, Key=chave, Body=buffer.getvalue())

    acao = "corrigidos" if args.aplicar else "seriam corrigidos (simulação; use --aplicar para gravar)"
    print(f"{len(chaves)} arquivos lidos; {arquivos} {acao}: "
          f"{linhas_tipo} linhas de tipo, {linhas_coord} coordenadas desinvertidas")


if __name__ == "__main__":
    main()
