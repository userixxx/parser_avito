"""
Клиент для запросов парсера (curl_cffi)
"""
import hashlib
import random
import time
from pathlib import Path
from curl_cffi import requests
from loguru import logger

from parser.cookies.base import CookiesProvider
from parser.proxies.proxy import Proxy

BLOCK_CODES = (401, 403, 429)
FEED_MARKERS = ("loaderData", '"items"')
UNSAFE_HEADERS = ("host", "content-length", "connection", "accept-encoding", "cookie")
BLOCK_EVENTS_TTL = 1800


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
        self._last_user_agent = None

    def _block_events_path(self) -> Path | None:
        change_ip_url = getattr(self.proxy, "change_ip_url", None)

        if not change_ip_url:
            return None

        digest = hashlib.sha1(change_ip_url.encode()).hexdigest()[:12]

        return Path("storage") / f"block_events_{digest}"

    def _read_block_events(self) -> int:
        path = self._block_events_path()

        if path is None:
            return self._block_limit_events

        try:
            count, stamp = path.read_text().split()
            if time.time() - float(stamp) > BLOCK_EVENTS_TTL:
                return 0
            return int(count)
        except (OSError, ValueError):
            return 0

    def _store_block_events(self, value: int) -> None:
        self._block_limit_events = value
        path = self._block_events_path()

        if path is None:
            return

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"{value} {time.time()}")
        except OSError as err:
            logger.warning(f"Не удалось сохранить счётчик блокировок: {err}")

    def _build_client(
        self,
        impersonate: str | None = None,
        user_agent: str | None = None,
    ) -> requests.Session:
        _impersonate = impersonate or random.choice(["tor", "edge", "firefox", "safari"])
        self._last_impersonate = _impersonate
        session = requests.Session(
            impersonate=_impersonate,
        )

        if user_agent:
            _user_agent = user_agent
        else:
            _chrome_version = str(random.randint(140, 147))
            _user_agent = (
                f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                f"AppleWebKit/537.36 (KHTML, like Gecko) "
                f"Chrome/{_chrome_version}.0.0.0 Safari/537.36"
            )

        self._last_user_agent = _user_agent
        session.headers.update({"user-agent": _user_agent})

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

    def _probe(
        self,
        method: str,
        url: str,
        probe_kwargs: dict,
        impersonate: str,
        user_agent: str,
    ):
        with self._build_client(impersonate, user_agent) as client:
            return client.request(
                method,
                url,
                timeout=self.timeout,
                allow_redirects=True,
                **probe_kwargs,
            )

    def _cookie_is_guilty(self, method: str, url: str, kwargs: dict) -> bool:
        if not kwargs.get("cookies"):
            logger.warning("Боевой запрос шёл без куки — винить нечего")
            return False

        impersonate = self._last_impersonate
        user_agent = self._last_user_agent

        cookie_first = random.choice((True, False))

        free_probe = ("free", {key: value for key, value in kwargs.items() if key != "cookies"})
        cookie_probe = ("cookie", dict(kwargs))
        probes = [cookie_probe, free_probe] if cookie_first else [free_probe, cookie_probe]

        for position, (name, probe_kwargs) in enumerate(probes):
            if position:
                time.sleep(self.retry_delay)

            try:
                response = self._probe(method, url, probe_kwargs, impersonate, user_agent)
            except Exception as err:
                logger.warning(f"Контрольная проба ({name}) не удалась ({err}) — куку не виним")
                return False

            if name == "cookie" and self._looks_like_feed(response):
                logger.warning("С кукой выдача тоже пришла — был шум, кука жива")
                return False

            if name == "free":
                if response.status_code in BLOCK_CODES:
                    logger.warning(
                        f"Тот же запрос без куки тоже заблокирован ({response.status_code}) — "
                        f"блокирует IP, кука ни при чём"
                    )
                    return False

                if not self._looks_like_feed(response):
                    logger.warning(
                        f"Без куки: HTTP {response.status_code} без выдачи — "
                        f"доказательств против куки нет"
                    )
                    return False

        if not cookie_first:
            logger.warning(
                "Без куки выдача, с кукой блок — но проба с кукой шла второй, "
                "блок мог дать порядок запросов. Вердикт отложен до следующей проверки"
            )
            return False

        logger.warning(
            "Проба с кукой шла первой и дала блок, проба без куки шла второй и дала выдачу — "
            "кука мертва, меняем"
        )
        return True

    @staticmethod
    def _known_impersonate(value: str | None) -> str | None:
        if not value:
            return None

        try:
            from curl_cffi.requests import BrowserType
            supported = {profile.value for profile in BrowserType}
        except Exception:
            return None

        if value in supported:
            return value

        logger.warning(f"Отпечаток {value} не поддерживается curl_cffi — берём случайный")
        return None

    def _provider_fingerprint(self) -> tuple[str | None, str | None, dict]:
        getter = getattr(self.cookies, "get_fingerprint", None)

        if getter is None:
            return None, None, {}

        fingerprint = getter() or {}

        return (
            self._known_impersonate(fingerprint.get("impersonate")),
            fingerprint.get("user_agent"),
            fingerprint.get("headers") or {},
        )

    def request(self, method: str, url: str, **kwargs):
        last_exc = None

        for attempt in range(1, self.max_retries + 1):
            try:
                impersonate, user_agent, extra_headers = self._provider_fingerprint()

                with self._build_client(impersonate, user_agent) as client:
                    if extra_headers:
                        client.headers.update(
                            {name: value for name, value in extra_headers.items()
                             if name.lower() not in UNSAFE_HEADERS}
                        )

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

                        events = self._read_block_events() + 1
                        self._store_block_events(events)

                        if self.on_subnet_block and events >= self.equip_after:
                            logger.warning("Смена IP не помогает (subnet-блок), запрашиваю смену оборудования")
                            try:
                                self.on_subnet_block()
                            except Exception as e:
                                logger.warning(f"on_subnet_block error: {e}")
                            self._store_block_events(0)

                    time.sleep(self.retry_delay)
                    continue

                # === успех ===
                response.raise_for_status()
                self._block_attempts = 0
                self._store_block_events(0)
                return response

            except requests.RequestsError as e:
                last_exc = e
                logger.warning(f"Request error (attempt {attempt}): {e}")
                time.sleep(self.retry_delay)

        raise RuntimeError("HTTP request failed after retries") from last_exc