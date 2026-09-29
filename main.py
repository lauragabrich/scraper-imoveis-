"""
Scraper de Anúncios Imobiliários - VivaReal, Lugar Certo e Imovelweb
Coleta dados de todas as cidades do Brasil. Salva em Parquet no Amazon S3.

Uso:
    python main.py --estado SP                          # VivaReal (padrão)
    python main.py --estado SP --parte 2/6              # VivaReal, 2ª de 6 partes
    python main.py --all-estados --reset                # nova coleta do zero
    python main.py --portal lugarcerto --all-estados
    python main.py --portal lugarcerto --estado MG --sem-detalhes
    python main.py --portal imovelweb --estado MG --workers 12 --passadas 2
"""

import argparse
import sys

PORTAIS = ["vivareal", "lugarcerto", "imovelweb"]


def parse_parte(texto: str) -> tuple[int, int]:
    """'2/4' -> (2, 4)."""
    try:
        n, m = (int(x) for x in texto.split("/"))
    except ValueError:
        raise argparse.ArgumentTypeError("use o formato N/M, ex.: 2/4")
    if not 1 <= n <= m:
        raise argparse.ArgumentTypeError("N precisa estar entre 1 e M")
    return n, m


def criar_scraper(args):
    parte, partes = args.parte
    detalhes = not args.sem_detalhes
    if args.portal == "vivareal":
        from scrapers.vivareal import VivaRealScraper
        return VivaRealScraper(detalhes=detalhes, workers=args.workers, parte=parte, partes=partes)
    if args.portal == "lugarcerto":
        from scrapers.lugarcerto import LugarCertoScraper
        return LugarCertoScraper(detalhes=detalhes, workers=args.workers)
    from scrapers.imovelweb import ImovelwebScraper
    return ImovelwebScraper(workers=args.workers, passadas=args.passadas,
                            detalhes=detalhes, parte=parte, partes=partes)


def main():
    parser = argparse.ArgumentParser(description="Scraper de imóveis - Brasil")
    parser.add_argument("--portal", choices=PORTAIS, default="vivareal", help="Portal a coletar")
    parser.add_argument("--estado", type=str, help="Estado (ex: SP, RJ, MG). Aceita lista: SP,RJ")
    parser.add_argument("--cidade", type=str, default="", help="Cidade específica")
    parser.add_argument("--all-estados", action="store_true", help="Todos os estados")
    parser.add_argument("--limit", type=int, help="Limite de anúncios")
    parser.add_argument("--reset", action="store_true",
                        help="Nova coleta do zero: zera o progresso dos estados/parte pedidos e grava "
                             "numa pasta coleta=<data> nova")
    parser.add_argument("--sem-detalhes", action="store_true",
                        help="Não busca o detalhe de cada anúncio (mais rápido, mas sem título/descrição no "
                             "VivaReal, sem lat/lng, fotos, CEP... no Lugar Certo e sem características "
                             "completas e data de publicação no Imovelweb)")
    parser.add_argument("--workers", type=int, default=4,
                        help="Requisições em paralelo (VivaReal: cuidado, o Cloudflare bloqueia o IP "
                             "por >1h a ~10 req/s)")
    parser.add_argument("--parte", type=parse_parte, default=(1, 1), metavar="N/M",
                        help="VivaReal/Imovelweb: divide o estado em M jobs e roda a parte N (ex.: 2/4)")
    parser.add_argument("--passadas", type=int, default=1,
                        help="Imovelweb: 2 = segunda passada com outra ordenação (+~3%% de cobertura, 2x requisições)")

    args = parser.parse_args()

    if not args.estado and not args.all_estados:
        parser.print_help()
        return

    scraper = criar_scraper(args)
    estados = list(scraper.ESTADOS) if args.all_estados else \
        [e.strip().upper() for e in args.estado.split(",") if e.strip()]

    # Progresso em progress/{portal}/{UF}[_parteNdeM].json. O progress/{UF}.json antigo
    # do VivaReal (coleta por bairro) não é mais lido nem alterado.
    if args.reset:
        scraper.storage.coleta_atual(args.portal, nova=True)

    total_saved = 0
    incompletos = []
    for estado in estados:
        saved, fim = scraper.run(estado=estado, cidade=args.cidade, limit=args.limit, reset=args.reset)
        total_saved += saved
        if fim != -1:
            incompletos.append(estado)

    print(f"\n{'='*60}", flush=True)
    print(f"TOTAL GERAL ({args.portal}): {total_saved} anúncios salvos no S3", flush=True)
    if incompletos:
        print(f"Pendentes (rodar de novo para continuar): {', '.join(incompletos)}", flush=True)
    print(f"{'='*60}", flush=True)
    # Código 2 = ainda há estados pendentes (quem chama, ex. tools/rodar_imovelweb.ps1, repete)
    sys.exit(2 if incompletos else 0)


if __name__ == "__main__":
    main()
