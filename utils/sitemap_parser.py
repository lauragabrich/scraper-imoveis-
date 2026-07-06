import os
import requests
import gzip
from io import BytesIO
from bs4 import BeautifulSoup
from config.settings import settings
from tqdm import tqdm


class SitemapParser:
    """Parser de sitemaps XML para extrair URLs de anúncios."""

    def __init__(self, portal: str):
        self.portal = portal
        self.base_url = settings.SITEMAPS.get(portal)
        self.cache_dir = os.path.join(settings.SITEMAP_CACHE_DIR, portal)
        os.makedirs(self.cache_dir, exist_ok=True)

    def fetch_sitemap(self, url: str) -> str:
        """Baixa e retorna o conteúdo de um sitemap."""
        headers = {"User-Agent": settings.USER_AGENTS[0]}
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()

        # Verifica se é gzipped
        if url.endswith(".gz") or response.headers.get("Content-Encoding") == "gzip":
            content = gzip.decompress(response.content).decode("utf-8")
        else:
            content = response.text
        return content

    def parse_sitemap_index(self, content: str) -> list[str]:
        """Extrai URLs de sub-sitemaps de um sitemap index."""
        import re
        return re.findall(r'<loc>\s*(.*?)\s*</loc>', content)

    def parse_sitemap_urls(self, content: str) -> list[str]:
        """Extrai URLs de anúncios de um sitemap."""
        import re
        return re.findall(r'<loc>\s*(.*?)\s*</loc>', content)

    def get_all_listing_urls(self, filter_pattern: str = None) -> list[str]:
        """
        Coleta todas as URLs de anúncios do portal.

        Args:
            filter_pattern: regex para filtrar URLs (ex: '/sp/' para São Paulo)

        Returns:
            Lista de URLs de anúncios
        """
        import re

        print(f"[{self.portal}] Baixando sitemap index: {self.base_url}")
        content = self.fetch_sitemap(self.base_url)

        # Verifica se é um sitemap index ou sitemap direto
        if "<sitemapindex" in content:
            sub_sitemaps = self.parse_sitemap_index(content)
            print(f"[{self.portal}] Encontrados {len(sub_sitemaps)} sub-sitemaps")

            all_urls = []
            for sitemap_url in tqdm(sub_sitemaps, desc=f"[{self.portal}] Processando sitemaps"):
                try:
                    sub_content = self.fetch_sitemap(sitemap_url)
                    urls = self.parse_sitemap_urls(sub_content)
                    all_urls.extend(urls)
                except Exception as e:
                    print(f"[{self.portal}] Erro ao processar {sitemap_url}: {e}")
                    continue
        else:
            all_urls = self.parse_sitemap_urls(content)

        # Filtra URLs se padrão fornecido
        if filter_pattern:
            pattern = re.compile(filter_pattern, re.IGNORECASE)
            all_urls = [url for url in all_urls if pattern.search(url)]

        print(f"[{self.portal}] Total de URLs coletadas: {len(all_urls)}")
        return all_urls
