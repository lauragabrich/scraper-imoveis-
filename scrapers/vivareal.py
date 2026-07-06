import requests
import json
import os
import time
import random
from scrapers.base import BaseScraper
from parsers.extractor import DataExtractor
from config.settings import settings


class VivaRealScraper(BaseScraper):
    """Scraper para VivaReal via API interna - busca por bairro."""

    PORTAL_NAME = "vivareal"
    API_URL = "https://glue-api.vivareal.com/v2/listings"
    LOCATIONS_URL = "https://glue-api.vivareal.com/v2/locations"
    BAIRROS_CACHE = "bairros_cache.json"

    ESTADOS = {
        "SP": "São Paulo", "RJ": "Rio de Janeiro", "MG": "Minas Gerais",
        "PR": "Paraná", "RS": "Rio Grande do Sul", "SC": "Santa Catarina",
        "BA": "Bahia", "PE": "Pernambuco", "CE": "Ceará", "DF": "Distrito Federal",
        "GO": "Goiás", "PA": "Pará", "AM": "Amazonas", "MA": "Maranhão",
        "ES": "Espírito Santo", "MT": "Mato Grosso", "MS": "Mato Grosso do Sul",
        "PB": "Paraíba", "RN": "Rio Grande do Norte", "AL": "Alagoas",
        "PI": "Piauí", "SE": "Sergipe", "TO": "Tocantins", "RO": "Rondônia",
        "AC": "Acre", "AP": "Amapá", "RR": "Roraima",
    }

    # Capitais de cada estado (busca inicial)
    CAPITAIS = {
        "SP": "São Paulo", "RJ": "Rio de Janeiro", "MG": "Belo Horizonte",
        "PR": "Curitiba", "RS": "Porto Alegre", "SC": "Florianópolis",
        "BA": "Salvador", "PE": "Recife", "CE": "Fortaleza", "DF": "Brasília",
        "GO": "Goiânia", "PA": "Belém", "AM": "Manaus", "MA": "São Luís",
        "ES": "Vitória", "MT": "Cuiabá", "MS": "Campo Grande",
        "PB": "João Pessoa", "RN": "Natal", "AL": "Maceió",
        "PI": "Teresina", "SE": "Aracaju", "TO": "Palmas", "RO": "Porto Velho",
        "AC": "Rio Branco", "AP": "Macapá", "RR": "Boa Vista",
    }

    def _get_api_headers(self):
        return {
            "User-Agent": random.choice(settings.USER_AGENTS),
            "x-domain": "www.vivareal.com.br",
            "Accept": "application/json",
        }

    def discover_bairros(self, estado: str, cidade: str) -> list[str]:
        """Descobre bairros de uma cidade via API de locations."""
        # Verifica cache
        cache = self._load_bairros_cache()
        key = f"{estado}_{cidade}"
        if key in cache:
            return cache[key]

        print(f"[vivareal] Descobrindo bairros de {cidade}/{estado}...")

        bairros = set()
        estado_nome = self.ESTADOS.get(estado.upper(), estado)

        # Busca letras A-Z + silabas comuns para pegar mais bairros
        queries = list("ABCDEFGHIJKLMNOPQRSTUVWXYZ") + [
            "Vila", "Jardim", "Parque", "Centro", "Santa", "São",
            "Bela", "Nova", "Alto", "Barra", "Campo", "Cidade",
        ]

        for query in queries:
            self.rate_limiter.wait()
            try:
                params = {
                    "q": f"{query} {cidade}",
                    "addressState": estado_nome,
                    "size": "50",
                }
                r = requests.get(
                    self.LOCATIONS_URL,
                    params=params,
                    headers=self._get_api_headers(),
                    timeout=15,
                )
                if r.status_code == 200:
                    data = r.json()
                    neighborhoods = data.get("neighborhood", {}).get("result", {}).get("locations", [])
                    for n in neighborhoods:
                        addr = n.get("address", {})
                        name = addr.get("neighborhood")
                        n_city = addr.get("city", "")
                        if name and n_city.lower() == cidade.lower():
                            bairros.add(name)
            except Exception:
                continue

        bairros_list = sorted(list(bairros))
        print(f"[vivareal] {len(bairros_list)} bairros encontrados em {cidade}/{estado}")

        # Salva cache
        cache[key] = bairros_list
        self._save_bairros_cache(cache)

        return bairros_list

    def _load_bairros_cache(self) -> dict:
        if os.path.exists(self.BAIRROS_CACHE):
            with open(self.BAIRROS_CACHE, "r", encoding="utf-8") as f:
                return json.load(f)
        return {}

    def _save_bairros_cache(self, cache: dict):
        with open(self.BAIRROS_CACHE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)

    def _make_request(self, estado: str, cidade: str, bairro: str, page: int, size: int = 24):
        """Faz request na API do VivaReal com bairro."""
        estado_nome = self.ESTADOS.get(estado.upper(), estado)
        params = {
            "addressState": estado_nome,
            "addressCity": cidade,
            "addressNeighborhood": bairro,
            "businessType": "SALE",
            "listingType": "USED",
            "size": str(size),
            "from": str((page - 1) * size),
            "categoryPage": "RESULT",
        }
        self.rate_limiter.wait()
        response = requests.get(
            self.API_URL,
            params=params,
            headers=self._get_api_headers(),
            timeout=15,
        )
        response.raise_for_status()
        return response.json()

    def get_total_pages(self, estado: str, cidade: str) -> int:
        """Não usado na abordagem por bairro."""
        return 0

    def collect_listings_page(self, estado: str, cidade: str, page: int) -> list[dict]:
        """Não usado na abordagem por bairro."""
        return []

    def scrape_bairro(self, estado: str, cidade: str, bairro: str, limit_pages: int = 420) -> int:
        """Scrape todos os anúncios de um bairro. Retorna quantidade salva."""
        saved = 0

        for page in range(1, limit_pages + 1):
            try:
                data = self._make_request(estado, cidade, bairro, page)
                listings = data.get("search", {}).get("result", {}).get("listings", [])

                if not listings:
                    break  # Sem mais resultados

                for item in listings:
                    parsed = self._parse_listing(item)
                    if parsed:
                        parsed["portal"] = self.PORTAL_NAME
                        if self.db.save_anuncio(parsed):
                            saved += 1

            except Exception as e:
                if "400" in str(e) or "429" in str(e):
                    break
                continue

        return saved

    def run(self, estado: str = "SP", cidade: str = "", limit: int = None, start_page: int = 1):
        """Executa scraping por bairro. Retorna (saved, last_index)."""
        from tqdm import tqdm

        cidade = cidade or self.CAPITAIS.get(estado.upper(), "")

        print(f"\n{'='*60}")
        print(f"Scraping VivaReal: {cidade}/{estado} (por bairro)")
        print(f"{'='*60}\n")

        # Descobre bairros
        bairros = self.discover_bairros(estado, cidade)

        if not bairros:
            print(f"[vivareal] Nenhum bairro encontrado para {cidade}/{estado}")
            return 0, -1

        # start_page aqui é o índice do bairro para continuar
        start_idx = start_page - 1 if start_page > 0 else 0
        bairros_restantes = bairros[start_idx:]

        print(f"[vivareal] {len(bairros_restantes)} bairros para processar (de {len(bairros)} total)")

        total_saved = 0
        last_idx = start_idx

        for i, bairro in enumerate(tqdm(bairros_restantes, desc=f"[{estado}] Bairros")):
            saved = self.scrape_bairro(estado, cidade, bairro)
            total_saved += saved
            last_idx = start_idx + i + 1

            if limit and total_saved >= limit:
                break

        if last_idx >= len(bairros):
            last_idx = -1  # concluído

        print(f"\n[vivareal] {cidade}/{estado}: {total_saved} anúncios salvos")
        return total_saved, last_idx + 1 if last_idx != -1 else -1

    def _parse_listing(self, item: dict) -> dict | None:
        """Parse de um anúncio da API."""
        try:
            listing = item.get("listing", {})
            address = listing.get("address", {})
            pricing = listing.get("pricingInfos", [{}])
            price_info = pricing[0] if pricing else {}

            # Fotos
            images = item.get("medias", [])
            fotos = "|".join([m.get("url", "") for m in images if m.get("url")]) or None

            # Preço
            preco = price_info.get("price")
            if not preco:
                preco = price_info.get("rentalTotalPrice")

            # Áreas
            usable = listing.get("usableAreas", [])
            total = listing.get("totalAreas", [])

            # Datas
            created = listing.get("createdAt")
            updated = listing.get("updatedAt")

            # URL
            link = listing.get("link", {})
            url = f"https://www.vivareal.com.br{link.get('href', '')}" if link.get("href") else None
            if not url:
                listing_id = listing.get("id", "")
                url = f"https://www.vivareal.com.br/imovel/{listing_id}"

            # Coordenadas
            point = address.get("point", {})

            return {
                "url": url,
                "titulo": listing.get("title"),
                "descricao": listing.get("description"),
                "tipo": self._map_tipo(listing.get("unitTypes", [None])[0]) if listing.get("unitTypes") else None,
                "finalidade": price_info.get("businessType", "SALE").replace("SALE", "venda").replace("RENTAL", "aluguel"),
                "preco": float(preco) if preco else None,
                "preco_condominio": float(price_info.get("monthlyCondoFee")) if price_info.get("monthlyCondoFee") else None,
                "iptu": float(price_info.get("yearlyIptu")) if price_info.get("yearlyIptu") else None,
                "area_construida": float(usable[0]) if usable and usable[0] else None,
                "area_terreno": float(total[0]) if total and total[0] else None,
                "quartos": int(listing.get("bedrooms", [0])[0]) if listing.get("bedrooms") else None,
                "suites": int(listing.get("suites", [0])[0]) if listing.get("suites") else None,
                "banheiros": int(listing.get("bathrooms", [0])[0]) if listing.get("bathrooms") else None,
                "vagas": int(listing.get("parkingSpaces", [0])[0]) if listing.get("parkingSpaces") else None,
                "rua": address.get("street"),
                "bairro": address.get("neighborhood"),
                "cidade": address.get("city"),
                "estado": address.get("stateAcronym"),
                "cep": address.get("zipCode"),
                "latitude": point.get("lat"),
                "longitude": point.get("lon"),
                "fotos_urls": fotos,
                "data_publicacao": created,
                "data_ultima_atualizacao": updated,
            }
        except (KeyError, IndexError, TypeError, ValueError):
            return None

    def _map_tipo(self, unit_type: str) -> str | None:
        if not unit_type:
            return None
        mapping = {
            "APARTMENT": "apartamento", "HOME": "casa",
            "CONDOMINIUM": "casa", "LAND": "terreno",
            "PENTHOUSE": "cobertura", "FLAT": "flat",
            "COMMERCIAL": "comercial", "FARM": "rural",
        }
        return mapping.get(unit_type.upper(), unit_type.lower())
