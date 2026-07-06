import random
import itertools
from config.settings import settings


class ProxyManager:
    """Gerencia rotação de proxies para evitar bloqueios."""

    def __init__(self):
        self.proxies = settings.PROXY_LIST
        self._cycle = itertools.cycle(self.proxies) if self.proxies else None
        self._failed_proxies = set()

    def get_proxy(self) -> dict | None:
        """Retorna o próximo proxy disponível no formato requests."""
        if not self._cycle:
            return None

        for _ in range(len(self.proxies)):
            proxy = next(self._cycle)
            if proxy not in self._failed_proxies:
                return {"http": proxy, "https": proxy}

        # Reset se todos falharam
        self._failed_proxies.clear()
        proxy = next(self._cycle)
        return {"http": proxy, "https": proxy}

    def mark_failed(self, proxy_url: str):
        """Marca um proxy como falho temporariamente."""
        self._failed_proxies.add(proxy_url)

    def get_random_proxy(self) -> dict | None:
        """Retorna um proxy aleatório."""
        if not self.proxies:
            return None
        available = [p for p in self.proxies if p not in self._failed_proxies]
        if not available:
            self._failed_proxies.clear()
            available = self.proxies
        proxy = random.choice(available)
        return {"http": proxy, "https": proxy}

    @property
    def has_proxies(self) -> bool:
        return bool(self.proxies)
