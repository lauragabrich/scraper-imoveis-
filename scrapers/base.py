import random
import requests
from abc import ABC, abstractmethod
from tenacity import retry, stop_after_attempt, wait_exponential
from config.settings import settings
from utils.rate_limiter import RateLimiter
from utils.proxy_manager import ProxyManager
from storage.database import Database


class BaseScraper(ABC):
    """Classe base para scrapers via paginação de listagem."""

    PORTAL_NAME: str = ""

    def __init__(self):
        self.rate_limiter = RateLimiter()
        self.proxy_manager = ProxyManager()
        self.db = Database()
        self.session = requests.Session()
        self._setup_session()

    def _setup_session(self):
        """Configura headers padrão da sessão."""
        self.session.headers.update({
            "User-Agent": random.choice(settings.USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
        })

    def _rotate_user_agent(self):
        """Rotaciona o User-Agent."""
        self.session.headers["User-Agent"] = random.choice(settings.USER_AGENTS)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def fetch_page(self, url: str) -> str | None:
        """Faz request com retry, proxies e rate limiting."""
        self.rate_limiter.wait()
        self._rotate_user_agent()
        proxies = self.proxy_manager.get_proxy()

        try:
            response = self.session.get(url, proxies=proxies, timeout=15)
            response.raise_for_status()
            return response.text
        except requests.exceptions.RequestException as e:
            if proxies:
                proxy_url = list(proxies.values())[0]
                self.proxy_manager.mark_failed(proxy_url)
            raise e

    @abstractmethod
    def collect_listings_page(self, estado: str, cidade: str, page: int) -> list[dict]:
        """
        Coleta anúncios de uma página de listagem.
        Retorna lista de dicts com dados dos anúncios.
        """
        pass

    @abstractmethod
    def get_total_pages(self, estado: str, cidade: str) -> int:
        """Retorna o número total de páginas disponíveis."""
        pass

    def run(self, estado: str = "SP", cidade: str = "", limit: int = None, start_page: int = 1):
        """Executa o scraping paginado. Retorna (saved, last_page)."""
        from tqdm import tqdm

        print(f"\n{'='*60}")
        print(f"Iniciando scraping: {self.PORTAL_NAME}")
        print(f"Estado: {estado} | Cidade: {cidade or 'todas'}")
        print(f"{'='*60}\n")

        total_pages = self.get_total_pages(estado, cidade)
        if limit:
            max_pages = min(total_pages, start_page + (limit // 20) + 1)
        else:
            max_pages = total_pages

        print(f"[{self.PORTAL_NAME}] Páginas: {start_page} a {max_pages} (de {total_pages} total)")

        saved = 0
        errors = 0
        consecutive_errors = 0
        last_page = start_page

        for page in tqdm(range(start_page, max_pages + 1), desc=f"[{self.PORTAL_NAME}] Páginas"):
            try:
                listings = self.collect_listings_page(estado, cidade, page)

                if not listings:
                    consecutive_errors += 1
                    if consecutive_errors >= 5:
                        print(f"\n[{self.PORTAL_NAME}] 5 páginas vazias seguidas, parando.")
                        break
                    continue

                consecutive_errors = 0

                for data in listings:
                    if limit and saved >= limit:
                        break
                    data["portal"] = self.PORTAL_NAME
                    if self.db.save_anuncio(data):
                        saved += 1
                    else:
                        errors += 1

                last_page = page + 1

                if limit and saved >= limit:
                    break

            except Exception as e:
                print(f"\n[{self.PORTAL_NAME}] Erro na página {page}: {e}")
                errors += 1
                consecutive_errors += 1
                if consecutive_errors >= 5:
                    print(f"\n[{self.PORTAL_NAME}] Muitos erros seguidos, parando.")
                    break
                continue

        if last_page > max_pages:
            last_page = -1  # concluído

        print(f"\n[{self.PORTAL_NAME}] Concluído: {saved} salvos, {errors} erros")
        return saved, last_page
