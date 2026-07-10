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
FEED_MARKERS = ("loaderData", '"items"')


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
        self._last_impersonate = None

    def _build_client(self, impersonate: str | None = None) -> requests.Session:
        _impersonate = impersonate or random.choice(["tor", "edge", "firefox", "safari"])
        self._last_impersonate = _impersonate
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

    @staticmethod
    def _looks_like_feed(response) -> bool:
        if response.status_code != 200:
            return False
        body = response.text or ""
        return any(marker in body for marker in FEED_MARKERS)

    def _probe(self, method: str, url: str, probe_kwargs: dict, impersonate: str):
        with self._build_client(impersonate) as client:
            return client.request(
                method,
                url,
                timeout=self.timeout,
                allow_redirects=True,
                **probe_kwargs,
            )

    def _cookie_is_guilty(self, method: str, url: str, kwargs: dict) -> bool:
        impersonate = self._last_impersonate
        without_cookie = {key: value for key, value in kwargs.items() if key != "cookies"}
        with_cookie = dict(kwargs)

        try:
            free = self._probe(method, url, without_cookie, impersonate)
        except Exception as err:
            logger.warning(f"Контрольный запрос без куки не удался ({err}) — куку не виним")
            return False

        if free.status_code in BLOCK_CODES:
            logger.warning(
                f"Тот же запрос без куки тоже заблокирован ({free.status_code}) — "
                f"блокирует IP, кука ни при чём"
            )
            return False

        if not self._looks_like_feed(free):
            logger.warning(f"Без куки: HTTP {free.status_code} без выдачи — доказательств против куки нет")
            return False

        try:
            recheck = self._probe(method, url, with_cookie, impersonate)
        except Exception as err:
            logger.warning(f"Перепроверка куки не удалась ({err}) — куку не виним")
            return False

        if recheck.status_code in BLOCK_CODES or not self._looks_like_feed(recheck):
            logger.warning("Без куки выдача есть, с кукой блок (перепроверено) — кука мертва, меняем")
            return True

        logger.warning("С кукой выдача тоже пришла — был шум, кука жива")
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

                        if self.cookies and self._cookie_is_guilty(method, url, kwargs):
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