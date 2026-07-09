import time
from abc import ABC, abstractmethod
from urllib.parse import urlsplit, urlunsplit

import requests
from loguru import logger


class Proxy(ABC):
    @abstractmethod
    def get_httpx_proxy(self) -> dict | None:
        pass

    @abstractmethod
    def handle_block(self):
        pass


class NoProxy(Proxy):
    def get_httpx_proxy(self):
        return None

    def handle_block(self):
        pass


class ServerProxy(Proxy):
    def __init__(self, proxy):
        self.proxy = proxy

    def get_httpx_proxy(self):
        return f"http://{self.proxy}"

    def handle_block(self):
        pass


class MobileProxy(Proxy):
    MIRROR_HOSTS = ("aproxy.site", "changeip.mobileproxy.space")
    HOST_SCHEMES = {"aproxy.site": "http", "changeip.mobileproxy.space": "https"}
    BUSY_MARKERS = ("already change ip",)

    _preferred_channel: dict[str, int] = {}
    _last_rotation_at: dict[str, float] = {}

    def __init__(self, url, change_ip_url, api_proxy=None, on_rotation_failed=None, timeout: int = 30,
                 probe_timeout: int = 5, cooldown: int = 120):
        self.url = url
        self.change_ip_url = change_ip_url
        self.api_proxy = api_proxy
        self.on_rotation_failed = on_rotation_failed
        self.timeout = timeout
        self.probe_timeout = probe_timeout
        self.cooldown = cooldown
        self.channels = self._build_channels()

    def get_httpx_proxy(self):
        return f"http://{self.url}"

    def handle_block(self) -> bool:
        if not self.channels:
            return False

        waited = self._seconds_since_rotation()
        if waited < self.cooldown:
            logger.info(f"Смена IP пропущена: кулдаун, прошло {int(waited)}с из {self.cooldown}с")
            return True

        preferred = self._preferred_channel.get(self.change_ip_url)
        order = self._order(preferred)
        failures = []

        for index in order:
            url, proxies = self.channels[index]
            label = self._label(url, proxies)

            if index != preferred and not self._probe(url, proxies):
                failures.append(f"{label}: канал недоступен")
                continue

            outcome, detail = self._rotate(url, proxies)

            if outcome == "ok":
                self._remember_rotation(index)
                logger.success(f"Смена IP через {label}: {detail}")
                return True

            if outcome == "pending":
                self._remember_rotation(index)
                logger.info(f"Смена IP через {label}: запущена, ответ не дождались")
                return True

            failures.append(f"{label}: {detail}")

        self._preferred_channel.pop(self.change_ip_url, None)
        reason = "смена IP не удалась ни по одному каналу — " + "; ".join(failures)
        logger.error(f"Ротация IP недоступна: {reason}")
        self._notify_failure(reason)
        return False

    def _seconds_since_rotation(self) -> float:
        last = self._last_rotation_at.get(self.change_ip_url)
        return self.cooldown if last is None else time.monotonic() - last

    def _remember_rotation(self, index: int) -> None:
        self._preferred_channel[self.change_ip_url] = index
        self._last_rotation_at[self.change_ip_url] = time.monotonic()

    def _order(self, preferred: int | None) -> list[int]:
        indexes = list(range(len(self.channels)))
        if preferred is None or preferred >= len(self.channels):
            return indexes
        return [preferred] + [i for i in indexes if i != preferred]

    def _probe(self, url: str, proxies: dict | None) -> bool:
        try:
            requests.get(self._without_key(url), params={"format": "json"},
                         proxies=proxies, timeout=self.probe_timeout)
            return True
        except requests.RequestException:
            return False

    def _rotate(self, url: str, proxies: dict | None) -> tuple[str, str]:
        try:
            response = requests.get(url, params={"format": "json"}, proxies=proxies, timeout=self.timeout)
        except requests.ReadTimeout:
            return "pending", "ReadTimeout"
        except requests.RequestException as err:
            return "error", type(err).__name__

        succeeded, detail = self._interpret(response)
        return ("ok" if succeeded else "error"), detail

    @staticmethod
    def _without_key(url: str) -> str:
        parts = urlsplit(url)
        query = "&".join(p for p in parts.query.split("&") if not p.startswith("proxy_key="))
        return urlunsplit((parts.scheme, parts.netloc, parts.path or "/", query, ""))

    def _build_channels(self) -> list[tuple[str, dict | None]]:
        current = urlsplit(self.change_ip_url).netloc
        hosts = [current] + [host for host in self.MIRROR_HOSTS if host != current]
        proxies = {"http": self.api_proxy, "https": self.api_proxy} if self.api_proxy else None

        channels = []
        for host in hosts:
            url = self._rehost(self.change_ip_url, host)
            channels.append((url, None))
            if proxies:
                channels.append((url, proxies))

        return channels

    @classmethod
    def _rehost(cls, url: str, host: str) -> str:
        parts = urlsplit(url)
        scheme = cls.HOST_SCHEMES.get(host, parts.scheme or "https")
        return urlunsplit((scheme, host, parts.path or "/", parts.query, ""))

    def _interpret(self, response) -> tuple[bool, str]:
        if response.status_code != 200:
            return False, f"HTTP {response.status_code}"

        try:
            payload = response.json()
        except ValueError:
            return False, "ответ не JSON"

        if not isinstance(payload, dict):
            return False, "неожиданный формат ответа"

        new_ip = payload.get("new_ip") or payload.get("ip")
        if new_ip:
            return True, f"новый IP {new_ip}"

        message = str(payload.get("message", "")).strip()
        if any(marker in message.lower() for marker in self.BUSY_MARKERS):
            return True, "смена IP уже выполняется"

        if str(payload.get("status", "")).lower() == "ok":
            return True, message or "status=ok"

        return False, message or "status=err"

    def _notify_failure(self, reason: str) -> None:
        if self.on_rotation_failed is None:
            return
        try:
            self.on_rotation_failed(reason)
        except Exception as err:
            logger.warning(f"on_rotation_failed error: {err}")

    @staticmethod
    def _label(url: str, proxies: dict | None) -> str:
        host = urlsplit(url).netloc
        return f"{host} через прокси" if proxies else host
