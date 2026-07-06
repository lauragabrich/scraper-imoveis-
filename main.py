"""
Scraper de Anúncios Imobiliários - VivaReal
Coleta dados via API interna do VivaReal (1.6M+ anúncios).

Uso:
    python main.py --estado SP
    python main.py --estado SP --cidade "São Paulo"
    python main.py --all-estados
    python main.py --all-estados --limit 100

O progresso é salvo automaticamente. Se parar no meio, roda de novo
e ele continua de onde parou.
"""

import argparse
import json
import os
from scrapers.vivareal import VivaRealScraper


PROGRESS_FILE = "progress.json"


def load_progress() -> dict:
    """Carrega progresso salvo."""
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, "r") as f:
            return json.load(f)
    return {}


def save_progress(progress: dict):
    """Salva progresso atual."""
    with open(PROGRESS_FILE, "w") as f:
        json.dump(progress, f, indent=2)


def main():
    parser = argparse.ArgumentParser(description="Scraper VivaReal - Imóveis Brasil")
    parser.add_argument("--estado", type=str, help="Estado (ex: SP, RJ, MG)")
    parser.add_argument("--cidade", type=str, default="", help="Cidade")
    parser.add_argument("--limit", type=int, help="Limitar número de anúncios")
    parser.add_argument("--all-estados", action="store_true", help="Todos os estados")
    parser.add_argument("--reset", action="store_true", help="Resetar progresso")

    args = parser.parse_args()

    if not args.estado and not args.all_estados:
        parser.print_help()
        return

    if args.reset and os.path.exists(PROGRESS_FILE):
        os.remove(PROGRESS_FILE)
        print("[*] Progresso resetado")

    scraper = VivaRealScraper()
    progress = load_progress()

    if args.all_estados:
        estados = list(scraper.ESTADOS.keys())
    else:
        estados = [args.estado.upper()]

    total_saved = 0

    for estado in estados:
        key = f"{estado}_{args.cidade or 'all'}"
        start_page = progress.get(key, 1)

        if start_page == -1:
            print(f"[{estado}] Já concluído, pulando...")
            continue

        if start_page > 1:
            print(f"[{estado}] Continuando da página {start_page}...")

        saved, last_page = scraper.run(
            estado=estado,
            cidade=args.cidade,
            limit=args.limit,
            start_page=start_page,
        )
        total_saved += saved

        # Salva progresso
        if last_page == -1:
            progress[key] = -1  # concluído
        else:
            progress[key] = last_page
        save_progress(progress)

    print(f"\n{'='*60}")
    print(f"TOTAL GERAL: {total_saved} anúncios salvos no Turso")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
