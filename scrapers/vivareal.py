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

    def discover_cidades(self, estado: str) -> list[str]:
        """Retorna todas as cidades de um estado (via IBGE - lista completa)."""
        from utils.ibge_cidades import get_cidades_estado

        cidades = get_cidades_estado(estado)
        print(f"[vivareal] {len(cidades)} cidades em {estado} (IBGE)", flush=True)
        return cidades

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

    def _make_request(self, estado: str, cidade: str, bairro: str, page: int, size: int = 24, listing_type: str = "USED"):
        """Faz request na API do VivaReal com bairro."""
        estado_nome = self.ESTADOS.get(estado.upper(), estado)
        params = {
            "addressState": estado_nome,
            "addressCity": cidade,
            "addressNeighborhood": bairro,
            "businessType": "SALE",
            "listingType": listing_type,
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
        """Scrape todos os anúncios de um bairro (usados + lançamentos). Retorna quantidade salva."""
        saved = 0

        # Busca imóveis usados
        saved += self._scrape_bairro_type(estado, cidade, bairro, "USED", limit_pages)
        # Busca lançamentos
        saved += self._scrape_bairro_type(estado, cidade, bairro, "DEVELOPMENT", limit_pages)

        return saved

    def scrape_cidade_completa(self, estado: str, cidade: str) -> int:
        """Scrape uma cidade: todos os bairros + busca sem bairro (fallback)."""
        saved = 0

        # Descobre bairros
        bairros = self.discover_bairros(estado, cidade)

        # Processa cada bairro
        if bairros:
            for bairro in bairros:
                saved += self.scrape_bairro(estado, cidade, bairro)

        # Fallback: busca sem bairro para pegar anúncios não associados a bairros
        saved += self.scrape_bairro(estado, cidade, "")

        return saved

    def _scrape_bairro_type(self, estado: str, cidade: str, bairro: str, listing_type: str, limit_pages: int = 420) -> int:
        """Scrape anúncios de um tipo específico."""
        saved = 0

        for page in range(1, limit_pages + 1):
            try:
                data = self._make_request(estado, cidade, bairro, page, listing_type=listing_type)
                listings = data.get("search", {}).get("result", {}).get("listings", [])

                if not listings:
                    break

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
        """Executa scraping por cidade e bairro. Retorna (saved, last_index)."""
        import sys

        print(f"\n{'='*60}", flush=True)
        print(f"Scraping VivaReal: {estado} (todas as cidades)", flush=True)
        print(f"{'='*60}\n", flush=True)

        # Se cidade específica foi passada, processa só ela
        if cidade:
            cidades = [cidade]
        else:
            cidades = self.discover_cidades(estado)
            if not cidades:
                # Fallback para capital
                capital = self.CAPITAIS.get(estado.upper(), "")
                cidades = [capital] if capital else []

        if not cidades:
            print(f"[vivareal] Nenhuma cidade encontrada para {estado}", flush=True)
            return 0, -1

        # start_page aqui é o índice global (cidade*1000 + bairro)
        start_cidade_idx = (start_page - 1) // 1000 if start_page > 1 else 0
        start_bairro_idx = (start_page - 1) % 1000 if start_page > 1 else 0

        print(f"[vivareal] {len(cidades)} cidades para processar", flush=True)

        total_saved = 0
        last_progress = start_page

        for cidade_idx in range(start_cidade_idx, len(cidades)):
            cidade_nome = cidades[cidade_idx]
            print(f"\n[{estado}] Cidade {cidade_idx+1}/{len(cidades)}: {cidade_nome}", flush=True)

            saved = self.scrape_cidade_completa(estado, cidade_nome)
            total_saved += saved
            last_progress = (cidade_idx + 1) * 1000 + 1

            if saved > 0:
                print(f"  → {saved} anúncios ({total_saved} total)", flush=True)

            # Salva progresso a cada cidade
            self.db.save_progress(estado, last_progress)

            if limit and total_saved >= limit:
                print(f"\n[vivareal] Limite de {limit} atingido", flush=True)
                return total_saved, last_progress

        print(f"\n[vivareal] {estado}: {total_saved} anúncios salvos ({len(cidades)} cidades)", flush=True)
        return total_saved, -1

    def _parse_listing(self, item: dict) -> dict | None:
        """Parse de um anúncio da API - extrai TODOS os campos disponíveis."""
        import json as json_mod

        try:
            listing = item.get("listing", {})
            address = listing.get("address", {})
            pricing = listing.get("pricingInfos", [{}])
            price_info = pricing[0] if pricing else {}

            # Fotos
            images = item.get("medias", [])
            fotos = "|".join([m.get("url", "") for m in images if m.get("url")]) or None
            image_count = len(images)

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
                lid = listing.get("id", "")
                url = f"https://www.vivareal.com.br/imovel/{lid}"

            # Coordenadas
            point = address.get("point", {})

            # Amenities
            amenities = listing.get("amenities", [])
            amenities_str = "|".join(amenities) if amenities else None

            # Complex amenities
            complex_raw = listing.get("complexAmenities") or listing.get("condominiumAmenities") or []
            complex_str = "|".join(complex_raw) if complex_raw else None

            # Preço por m²
            area_val = float(usable[0]) if usable and usable[0] else None
            preco_val = float(preco) if preco else None
            preco_por_m2 = round(preco_val / area_val, 2) if preco_val and area_val and area_val > 0 else None

            # Campos adicionais
            usage_types = listing.get("usageTypes", [])
            unit_types = listing.get("unitTypes", [])
            floors = listing.get("floors", [])
            unit_floor = listing.get("unitFloor", [])

            # Anunciante
            advertiser = item.get("account", {}) or item.get("advertiser", {})
            contact = listing.get("advertiserContact", {})

            # Pricing extra
            rental_info = price_info.get("rentalInfo", {})
            warranties = rental_info.get("warranties", [])
            aluguel_total = price_info.get("rentalTotalPrice") or rental_info.get("monthlyRentalTotalPrice")

            # Stamps
            stamps_raw = listing.get("stamps", [])
            stamps_str = "|".join(stamps_raw) if stamps_raw else None

            # Raw JSON completo
            raw_json = json_mod.dumps(item, ensure_ascii=False)

            return {
                "url": url,
                "titulo": listing.get("title"),
                "descricao": listing.get("description"),
                "tipo": self._map_tipo(unit_types[0]) if unit_types else None,
                "finalidade": price_info.get("businessType", "SALE").replace("SALE", "venda").replace("RENTAL", "aluguel"),
                "preco": preco_val,
                "preco_condominio": float(price_info.get("monthlyCondoFee")) if price_info.get("monthlyCondoFee") else None,
                "iptu": float(price_info.get("yearlyIptu")) if price_info.get("yearlyIptu") else None,
                "area_construida": area_val,
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
                "image_count": image_count,
                "data_publicacao": created,
                "data_ultima_atualizacao": updated,
                "amenities": amenities_str,
                "complex_amenities": complex_str,
                "preco_por_m2": preco_por_m2,
                "raw_json": raw_json,
                "usage_types": "|".join(usage_types) if usage_types else None,
                "property_sub_type": unit_types[0] if unit_types else None,
                "andar": int(floors[0]) if floors else None,
                "total_andares": int(unit_floor[0]) if unit_floor else None,
                "aceita_permuta": str(listing.get("acceptExchange")) if listing.get("acceptExchange") is not None else None,
                "status_anuncio": listing.get("status"),
                "anunciante_nome": advertiser.get("name") or contact.get("name"),
                "anunciante_telefone": str(contact.get("phones")) if contact.get("phones") else None,
                "listing_id": listing.get("id"),
                "stamps": stamps_str,
                "contract_type": price_info.get("businessType"),
                "zona": address.get("zone"),
                "periodo_iptu": price_info.get("iptuPeriod"),
                "garantias_aluguel": "|".join(warranties) if warranties else None,
                "aluguel_total": float(aluguel_total) if aluguel_total else None,
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
