import asyncio
import random
import time
from config.settings import settings


class RateLimiter:
    """Controla a taxa de requisições para evitar bloqueios."""

    def __init__(self, min_delay: float = None, max_delay: float = None):
        self.min_delay = min_delay or settings.REQUEST_DELAY_MIN
        self.max_delay = max_delay or settings.REQUEST_DELAY_MAX
        self._last_request_time = 0.0

    def wait(self):
        """Espera síncrono entre requisições."""
        elapsed = time.time() - self._last_request_time
        delay = random.uniform(self.min_delay, self.max_delay)
        if elapsed < delay:
            time.sleep(delay - elapsed)
        self._last_request_time = time.time()

    async def async_wait(self):
        """Espera assíncrono entre requisições."""
        elapsed = time.time() - self._last_request_time
        delay = random.uniform(self.min_delay, self.max_delay)
        if elapsed < delay:
            await asyncio.sleep(delay - elapsed)
        self._last_request_time = time.time()

    def get_delay(self) -> float:
        """Retorna um delay aleatório."""
        return random.uniform(self.min_delay, self.max_delay)
