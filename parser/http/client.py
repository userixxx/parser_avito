"""
Клиент для запросов парсера (curl_cffi)
"""
import random
import time
from curl_cffi import requests
from loguru import logger

from parser.cookies.base import CookiesProvider
from parser.proxies.proxy import Proxy

BLOCK_CODES = (401, 403, 429)
COOKIE_FREE_URL = "https://www.avito.ru/"


class HttpClient:
    def __init__(
        self,
        proxy: Proxy,
        cookies: CookiesProvider | None = None,
        timeout: int = 20,
        max_retries: int = 5,
        retry_delay: int = 5,
        block_threshold: int = 3,
        equip_after: int = 3,
        on_subnet_block=None,
    ):
        self.proxy = proxy
        self.cookies = cookies
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.block_threshold = block_threshold
        self.equip_after = equip_after
        self.on_subnet_block = on_subnet_block

        self._block_attempts = 0
        self._block_limit_events = 0

    def _build_client(self) -> requests.Session:
        _impersonate = random.choice(["tor", "edge", "firefox", "safari"])
        session = requests.Session(
            impersonate=_impersonate,
        )

        _chrome_version = str(random.randint(140, 147))
        headers = {
            "user-agent": f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          f"AppleWebKit/537.36 (KHTML, like Gecko) "
                          f"Chrome/{_chrome_version}.0.0.0 Safari/537.36",
        }

        session.headers.update(headers)

        proxy = self.proxy.get_httpx_proxy()
        if proxy:
            session.proxies = {
                "http": proxy,
                "https": proxy,
            }

        return session

    def _ip_is_blocked(self) -> bool:
        try:
            with self._build_client() as client:
                response = client.get(COOKIE_FREE_URL, timeout=self.timeout, allow_redirects=True)
        except Exception as err:
            logger.warning(f"Проверка IP не удалась ({err}) — считаем, что блокирует IP")
            return True

        if response.status_code in BLOCK_CODES:
            logger.warning(
                f"Главная без куки тоже заблокирована ({response.status_code}) — "
                f"блокирует IP, кука ни при чём"
            )
            return True

        logger.warning(f"Главная без куки открылась ({response.status_code}) — IP чист, виновата кука")
        return False

    def request(self, method: str, url: str, **kwargs):
        last_exc = None

        for attempt in range(1, self.max_retries + 1):
            try:
                with self._build_client() as client:

                    if self.cookies:
                        kwargs.setdefault("cookies", self.cookies.get())

                    response = client.request(
                        method,
                        url,
                        timeout=self.timeout,
                        allow_redirects=True,
                        **kwargs,
                    )

                # === обновление cookies ===
                if self.cookies:
                    self.cookies.update(response)

                # === обработка блокировок ===
                if response.status_code in BLOCK_CODES:
                    self._block_attempts += 1

                    logger.warning(
                        f"Запрос заблокирован ({response.status_code}), "
                        f"попытка {self._block_attempts}"
                    )

                    if self._block_attempts >= self.block_threshold:
                        logger.warning("Достигнут лимит блокировок, запускается обработка")

                        if self.cookies and not self._ip_is_blocked():
                            self.cookies.handle_block()

                        self.proxy.handle_block()
                        self._block_attempts = 0

                        self._block_limit_events += 1
                        if self.on_subnet_block and self._block_limit_events >= self.equip_after:
                            logger.warning("Смена IP не помогает (subnet-блок), запрашиваю смену оборудования")
                            try:
                                self.on_subnet_block()
                            except Exception as e:
                                logger.warning(f"on_subnet_block error: {e}")
                            self._block_limit_events = 0

                    time.sleep(self.retry_delay)
                    continue

                # === успех ===
                response.raise_for_status()
                self._block_attempts = 0
                self._block_limit_events = 0
                return response

            except requests.RequestsError as e:
                last_exc = e
                logger.warning(f"Request error (attempt {attempt}): {e}")
                time.sleep(self.retry_delay)

        raise RuntimeError("HTTP request failed after retries") from last_exc