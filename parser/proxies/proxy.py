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

    def __init__(self, url, change_ip_url, api_proxy=None, on_rotation_failed=None, timeout: int = 10):
        self.url = url
        self.change_ip_url = change_ip_url
        self.api_proxy = api_proxy
        self.on_rotation_failed = on_rotation_failed
        self.timeout = timeout
        self.channels = self._build_channels()

    def get_httpx_proxy(self):
        return f"http://{self.url}"

    def handle_block(self) -> bool:
        if not self.channels:
            return False

        preferred = self._preferred_channel.get(self.change_ip_url, 0) % len(self.channels)
        rotated = self.channels[preferred:] + self.channels[:preferred]
        failures = []

        for offset, (url, proxies) in enumerate(rotated):
            label = self._label(url, proxies)
            try:
                response = requests.get(url, params={"format": "json"}, proxies=proxies, timeout=self.timeout)
            except requests.RequestException as err:
                failures.append(f"{label}: {type(err).__name__}")
                continue

            succeeded, detail = self._interpret(response)
            if succeeded:
                self._preferred_channel[self.change_ip_url] = (preferred + offset) % len(self.channels)
                logger.success(f"Смена IP через {label}: {detail}")
                return True

            failures.append(f"{label}: {detail}")

        reason = "смена IP не удалась ни по одному каналу — " + "; ".join(failures)
        logger.error(f"Ротация IP недоступна: {reason}")
        self._notify_failure(reason)
        return False

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
