import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    # Database - Turso (libSQL remoto)
    TURSO_DATABASE_URL = os.getenv("TURSO_DATABASE_URL", "")
    TURSO_AUTH_TOKEN = os.getenv("TURSO_AUTH_TOKEN", "")

    # Scraping
    MAX_CONCURRENT_REQUESTS = int(os.getenv("MAX_CONCURRENT_REQUESTS", "5"))
    REQUEST_DELAY_MIN = float(os.getenv("REQUEST_DELAY_MIN", "1.0"))
    REQUEST_DELAY_MAX = float(os.getenv("REQUEST_DELAY_MAX", "3.0"))
    MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))

    # Proxies
    PROXY_LIST = [p.strip() for p in os.getenv("PROXY_LIST", "").split(",") if p.strip()]

    # Cache
    SITEMAP_CACHE_DIR = os.getenv("SITEMAP_CACHE_DIR", "./cache/sitemaps")

    # Portais - Sitemaps
    SITEMAPS = {
        "vivareal": "https://www.vivareal.com.br/sitemap.xml",
        "zapimoveis": "https://www.zapimoveis.com.br/sitemap.xml",
        "imovelweb": "https://www.imovelweb.com.br/sitemap.xml",
        "lugarcerto": "https://www.lugarcerto.com.br/sitemap.xml",
        "olx": "https://www.olx.com.br/sitemap.xml",
    }

    # User agents rotativos
    USER_AGENTS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    ]


settings = Settings()
