import random
import time

import requests
from curl_cffi import requests as cffi
from loguru import logger

from actualizator.classify import classify

DEFAULT_IMPERSONATE = "edge99"
ROTATION_CHANNELS = (
    "https://changeip.mobileproxy.space/",
    "http://aproxy.site/",
)

SOURCE_PROFILES = {
    "avito": {"cookie": True, "read_limit": 1_200_000},
    "cian": {"cookie": False, "read_limit": 900_000},
    "yandex": {"cookie": False, "read_limit": 900_000},
}

DEFAULT_PROFILE = {"cookie": False, "read_limit": 1_200_000}


class Fetcher:
    def __init__(self, settings, core, cookie_slot: str):
        self.settings = settings
        self.core = core
        self.cookie_slot = cookie_slot
        self.proxy_string: str | None = None
        self.change_ip_url: str | None = None
        self.cookie: dict | None = None
        self.consecutive_blocks = 0
        self.last_rotate_at = 0.0
        self.last_status: int | None = None

    def apply_config(self, proxy_string: str | None, change_ip_url: str | None) -> None:
        self.proxy_string = proxy_string
        self.change_ip_url = change_ip_url

    @staticmethod
    def profile(source: str) -> dict:
        return SOURCE_PROFILES.get(source, DEFAULT_PROFILE)

    def _proxies(self) -> dict | None:
        if not self.proxy_string:
            return None
        url = self.proxy_string
        if not url.startswith("http"):
            url = f"http://{url}"
        return {"http": url, "https": url}

    def _ensure_cookie(self) -> dict | None:
        if self.cookie is not None:
            return self.cookie

        leased = self.core.lease_cookie(self.cookie_slot)
        if leased is None:
            return None

        self.cookie = leased
        logger.info(
            f"кука слота {self.cookie_slot}: id={leased.get('cookie_id')} "
            f"type={leased.get('type')} imp={leased.get('impersonate')}"
        )
        return self.cookie

    def _request_kwargs(self, source: str, with_cookie: bool = True) -> dict:
        kwargs = {
            "proxies": self._proxies(),
            "timeout": self.settings.request_timeout,
            "impersonate": DEFAULT_IMPERSONATE,
            "stream": True,
        }

        if with_cookie and self.profile(source)["cookie"] and self.cookie:
            kwargs["cookies"] = self.cookie.get("cookies") or {}

        return kwargs

    def ready(self) -> bool:
        if not self.proxy_string:
            logger.error("прокси не задан в parser_configs — работать нечем")
            return False

        return True

    def _read(self, response, limit: int) -> str:
        buffer = bytearray()

        for chunk in response.iter_content(chunk_size=65536):
            buffer.extend(chunk)
            if len(buffer) >= limit:
                break

        return bytes(buffer).decode("utf-8", errors="ignore")

    def _get(self, url: str, source: str, with_cookie: bool = True) -> tuple[int, str]:
        response = cffi.get(url, **self._request_kwargs(source, with_cookie))
        try:
            body = self._read(response, self.profile(source)["read_limit"])
            return response.status_code, body
        finally:
            response.close()

    def fetch(self, url: str, source: str) -> tuple[int, str]:
        if self.profile(source)["cookie"] and self._ensure_cookie() is None:
            logger.warning(f"нет куки для слота {self.cookie_slot} — задача {source} отложена")
            return 0, ""

        for attempt in range(1, self.settings.net_retries + 1):
            try:
                status, body = self._get(url, source)
                self.last_status = status
                return status, body
            except Exception as err:
                logger.warning(f"сетевая ошибка {attempt}/{self.settings.net_retries}: {str(err)[:120]}")
                time.sleep(4)

        return 0, ""

    def check(self, url: str, source: str) -> tuple[str, int]:
        status, body = self.fetch(url, source)
        result = classify(source, status, body) if status else "error"

        if result == "blocked":
            self.consecutive_blocks += 1
            self._on_block(url, source)
        else:
            self.consecutive_blocks = 0
            if self.cookie and self.profile(source)["cookie"] and result in ("alive", "not_found"):
                self.core.report_cookie(self.cookie["cookie_id"], True, status)

        return result, status

    def _on_block(self, url: str, source: str) -> None:
        if self.consecutive_blocks < self.settings.block_limit:
            return

        self.consecutive_blocks = 0

        if self.profile(source)["cookie"] and self._cookie_is_guilty(url, source):
            logger.warning("дискриминатор: виновата кука — возвращаем её в пул как заблокированную")
            if self.cookie:
                self.core.report_cookie(self.cookie["cookie_id"], False, self.last_status)
            self.cookie = None
            return

        logger.warning("дискриминатор: виноват IP — меняем адрес, куку не трогаем")
        self.rotate_ip()

    def _cookie_is_guilty(self, url: str, source: str) -> bool:
        try:
            status, body = self._get(url, source, with_cookie=False)
        except Exception:
            return False

        if status != 200:
            return False

        return classify(source, status, body) != "blocked"

    def rotate_ip(self) -> bool:
        if not self.change_ip_url:
            return False

        elapsed = time.time() - self.last_rotate_at
        if elapsed < self.settings.rotate_cooldown:
            wait = self.settings.rotate_cooldown - elapsed
            logger.info(f"кулдаун ротации: ждём {wait:.0f}с")
            time.sleep(wait)

        for channel in self._rotation_urls():
            try:
                response = requests.get(channel, timeout=30)
            except requests.RequestException as err:
                logger.warning(f"канал ротации недоступен ({str(err)[:80]})")
                continue

            if response.status_code != 200:
                continue

            if '"status":"err"' in response.text.replace(" ", ""):
                logger.warning(f"канал ротации отказал: {response.text[:120]}")
                continue

            self.last_rotate_at = time.time()
            logger.info("IP сменён")
            time.sleep(6)
            return True

        logger.error("сменить IP не удалось ни по одному каналу")
        return False

    def _rotation_urls(self) -> list[str]:
        if not self.change_ip_url:
            return []

        urls = [self.change_ip_url]

        if "proxy_key=" in self.change_ip_url:
            key = self.change_ip_url.split("proxy_key=")[1].split("&")[0]
            for base in ROTATION_CHANNELS:
                candidate = f"{base}?proxy_key={key}&format=json"
                if candidate not in urls:
                    urls.append(candidate)

        return urls

    def pause(self) -> None:
        time.sleep(random.uniform(self.settings.pause_min, self.settings.pause_max))
