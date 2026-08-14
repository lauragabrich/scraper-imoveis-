"""
Scraper de Anúncios Imobiliários - VivaReal
Coleta dados via API interna, todas as cidades e bairros do Brasil.
Salva em Parquet no Amazon S3.

Uso:
    python main.py --estado SP
    python main.py --all-estados
    python main.py --all-estados --reset
"""

import argparse
from scrapers.vivareal import VivaRealScraper


def main():
    parser = argparse.ArgumentParser(description="Scraper VivaReal - Imóveis Brasil")
    parser.add_argument("--estado", type=str, help="Estado (ex: SP, RJ, MG)")
    parser.add_argument("--cidade", type=str, default="", help="Cidade específica")
    parser.add_argument("--all-estados", action="store_true", help="Todos os estados")
    parser.add_argument("--limit", type=int, help="Limite de anúncios")
    parser.add_argument("--reset", action="store_true", help="Resetar progresso")

    args = parser.parse_args()

    if not args.estado and not args.all_estados:
        parser.print_help()
        return

    scraper = VivaRealScraper()
    storage = scraper.storage

    if args.reset:
        for estado in scraper.ESTADOS.keys():
            storage.save_progress(estado, 1, cidade_nome="", bairro_idx=0)
        print("[*] Progresso resetado", flush=True)

    if args.all_estados:
        estados = list(scraper.ESTADOS.keys())
    else:
        estados = [args.estado.upper()]

    total_saved = 0

    for estado in estados:
        progress = storage.get_progress(estado)
        start_page = progress.get("last_page", 1)

        if start_page == -1:
            print(f"[{estado}] Já concluído, pulando...", flush=True)
            continue

        if start_page > 1:
            print(f"[{estado}] Continuando da página {start_page}...", flush=True)

        saved, last_page = scraper.run(
            estado=estado,
            cidade=args.cidade,
            limit=args.limit,
            start_page=start_page,
            start_bairro_idx=progress.get("bairro_idx", 0),
        )
        total_saved += saved

        # Salva progresso
        storage.save_progress(estado, last_page)
        print(f"[{estado}] Progresso salvo: {last_page}", flush=True)

    print(f"\n{'='*60}", flush=True)
    print(f"TOTAL GERAL: {total_saved} anúncios salvos no S3", flush=True)
    print(f"{'='*60}", flush=True)


if __name__ == "__main__":
    main()
