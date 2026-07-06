import re
import json
from datetime import datetime
from bs4 import BeautifulSoup


class DataExtractor:
    """Utilitários para extração de dados de HTML e JSON-LD."""

    @staticmethod
    def extract_json_ld(html: str) -> dict | None:
        """Extrai dados estruturados JSON-LD da página."""
        soup = BeautifulSoup(html, "html.parser")
        scripts = soup.find_all("script", {"type": "application/ld+json"})

        for script in scripts:
            try:
                data = json.loads(script.string)
                if isinstance(data, list):
                    for item in data:
                        if item.get("@type") in ["Product", "RealEstateListing", "Residence"]:
                            return item
                elif data.get("@type") in ["Product", "RealEstateListing", "Residence"]:
                    return data
            except (json.JSONDecodeError, TypeError):
                continue
        return None

    @staticmethod
    def extract_next_data(html: str) -> dict | None:
        """Extrai dados do __NEXT_DATA__ (React/Next.js)."""
        match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                return None
        return None

    @staticmethod
    def extract_initial_state(html: str) -> dict | None:
        """Extrai window.__INITIAL_STATE__ ou similar."""
        patterns = [
            r'window\.__INITIAL_STATE__\s*=\s*({.*?});',
            r'window\.__DATA__\s*=\s*({.*?});',
            r'window\.initialData\s*=\s*({.*?});',
        ]
        for pattern in patterns:
            match = re.search(pattern, html, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(1))
                except json.JSONDecodeError:
                    continue
        return None

    @staticmethod
    def clean_price(text: str) -> float | None:
        """Limpa e converte texto de preço para float."""
        if not text:
            return None
        cleaned = re.sub(r'[R$\s.]', '', str(text))
        cleaned = cleaned.replace(',', '.')
        try:
            value = float(cleaned)
            return value if value > 0 else None
        except ValueError:
            return None

    @staticmethod
    def clean_area(text: str) -> float | None:
        """Extrai área em m² do texto."""
        if not text:
            return None
        match = re.search(r'([\d.,]+)\s*m', str(text))
        if match:
            area_str = match.group(1).replace('.', '').replace(',', '.')
            try:
                return float(area_str)
            except ValueError:
                return None
        return None

    @staticmethod
    def clean_int(text: str) -> int | None:
        """Extrai inteiro de texto."""
        if not text:
            return None
        match = re.search(r'(\d+)', str(text))
        if match:
            return int(match.group(1))
        return None

    @staticmethod
    def extract_coordinates(html: str) -> tuple[float | None, float | None]:
        """Tenta extrair latitude/longitude do HTML."""
        lat_match = re.search(r'latitude["\s:=]+(-?\d+\.?\d*)', html)
        lng_match = re.search(r'longitude["\s:=]+(-?\d+\.?\d*)', html)
        lat = float(lat_match.group(1)) if lat_match else None
        lng = float(lng_match.group(1)) if lng_match else None
        return lat, lng

    @staticmethod
    def extract_photos(html: str) -> str | None:
        """Extrai URLs de fotos do anúncio. Retorna separadas por |."""
        soup = BeautifulSoup(html, "html.parser")
        photos = set()

        # Meta og:image
        og_images = soup.find_all("meta", {"property": "og:image"})
        for tag in og_images:
            if tag.get("content"):
                photos.add(tag["content"])

        # Imagens em galerias/carousels
        gallery_imgs = soup.find_all("img", src=re.compile(r"https?://.*\.(jpg|jpeg|png|webp)", re.I))
        for img in gallery_imgs:
            src = img.get("src") or img.get("data-src")
            if src and ("listing" in src or "imovel" in src or "photo" in src or "image" in src):
                photos.add(src)

        # data-src (lazy loading)
        lazy_imgs = soup.find_all(attrs={"data-src": re.compile(r"https?://.*\.(jpg|jpeg|png|webp)", re.I)})
        for img in lazy_imgs:
            photos.add(img["data-src"])

        return "|".join(photos) if photos else None

    @staticmethod
    def parse_date(text: str) -> datetime | None:
        """Tenta parsear data de publicação/atualização."""
        if not text:
            return None

        # Formatos comuns nos portais brasileiros
        formats = [
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%dT%H:%M:%S.%fZ",
            "%Y-%m-%d",
            "%d/%m/%Y",
            "%d/%m/%Y %H:%M",
        ]
        for fmt in formats:
            try:
                return datetime.strptime(text.strip(), fmt)
            except ValueError:
                continue

        # Tenta ISO format genérico
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00").replace("+00:00", ""))
        except (ValueError, TypeError):
            return None

    @staticmethod
    def extract_dates_from_html(html: str) -> dict:
        """Extrai datas de publicação e atualização do HTML."""
        dates = {"data_publicacao": None, "data_ultima_atualizacao": None}

        # Meta tags de data
        soup = BeautifulSoup(html, "html.parser")

        pub_meta = soup.find("meta", {"property": "article:published_time"})
        if pub_meta and pub_meta.get("content"):
            dates["data_publicacao"] = DataExtractor.parse_date(pub_meta["content"])

        mod_meta = soup.find("meta", {"property": "article:modified_time"})
        if mod_meta and mod_meta.get("content"):
            dates["data_ultima_atualizacao"] = DataExtractor.parse_date(mod_meta["content"])

        # Busca em texto
        if not dates["data_publicacao"]:
            pub_match = re.search(r'(?:publicado|criado|anunciado)\s*(?:em|:)?\s*(\d{2}/\d{2}/\d{4})', html, re.I)
            if pub_match:
                dates["data_publicacao"] = DataExtractor.parse_date(pub_match.group(1))

        if not dates["data_ultima_atualizacao"]:
            upd_match = re.search(r'(?:atualizado|modificado)\s*(?:em|:)?\s*(\d{2}/\d{2}/\d{4})', html, re.I)
            if upd_match:
                dates["data_ultima_atualizacao"] = DataExtractor.parse_date(upd_match.group(1))

        return dates
