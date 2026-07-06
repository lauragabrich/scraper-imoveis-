import requests
from scrapers.base import BaseScraper
from parsers.extractor import DataExtractor
from config.settings import settings


class VivaRealScraper(BaseScraper):
    """Scraper para VivaReal via API interna (JSON)."""

    PORTAL_NAME = "vivareal"
    API_URL = "https://glue-api.vivareal.com/v2/listings"

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

    def _get_api_headers(self):
        return {
            "User-Agent": settings.USER_AGENTS[0],
            "x-domain": "www.vivareal.com.br",
            "Accept": "application/json",
        }

    def _make_request(self, estado: str, cidade: str, page: int, size: int = 24):
        """Faz request na API do VivaReal."""
        estado_nome = self.ESTADOS.get(estado.upper(), estado)
        cidade_nome = cidade.replace("-", " ").title() if cidade else estado_nome
        params = {
            "addressState": estado_nome,
            "addressCity": cidade_nome,
            "addressNeighborhood": "",
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
        if response.status_code != 200:
            print(f"\n[vivareal] Status {response.status_code} | URL: {response.url}")
        response.raise_for_status()
        return response.json()

    def get_total_pages(self, estado: str, cidade: str) -> int:
        """Retorna total de páginas via API."""
        try:
            data = self._make_request(estado, cidade, page=1, size=24)
            total = data.get("search", {}).get("totalCount", 0)
            print(f"[vivareal] Total de anúncios disponíveis: {total}")
            return (total // 24) + 1
        except Exception as e:
            print(f"[vivareal] Erro ao obter total: {e}")
            return 100

    def collect_listings_page(self, estado: str, cidade: str, page: int) -> list[dict]:
        """Coleta anúncios de uma página via API."""
        data = self._make_request(estado, cidade, page, size=24)
        listings = data.get("search", {}).get("result", {}).get("listings", [])
        results = []

        for item in listings:
            parsed = self._parse_listing(item)
            if parsed:
                results.append(parsed)

        return results

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
        except (KeyError, IndexError, TypeError, ValueError) as e:
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
