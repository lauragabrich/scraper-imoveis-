"""
Scraper de Anúncios Imobiliários - VivaReal
Coleta dados via API interna, todas as cidades e bairros do Brasil.

Uso:
    python main.py --estado SP
    python main.py --all-estados
    python main.py --all-estados --reset
"""

import argparse
from scrapers.vivareal import VivaRealScraper
from storage.database import Database


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
    db = scraper.db

    if args.reset:
        for estado in scraper.ESTADOS.keys():
            db.save_progress(estado, 1)
        print("[*] Progresso resetado no banco", flush=True)

    if args.all_estados:
        estados = list(scraper.ESTADOS.keys())
    else:
        estados = [args.estado.upper()]

    total_saved = 0

    for estado in estados:
        start_page = db.get_progress(estado)

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
        )
        total_saved += saved

        # Salva progresso no banco
        db.save_progress(estado, last_page)
        print(f"[{estado}] Progresso salvo: {last_page}", flush=True)

    print(f"\n{'='*60}", flush=True)
    print(f"TOTAL GERAL: {total_saved} anúncios salvos no Turso", flush=True)
    print(f"{'='*60}", flush=True)


if __name__ == "__main__":
    main()
