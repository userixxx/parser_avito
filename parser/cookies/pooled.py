import json
import time
from pathlib import Path

import requests
from loguru import logger

from parser.cookies.base import CookiesProvider
from parser.cookies.external_api import ExternalApiCookiesProvider


class PooledCookiesProvider(CookiesProvider):
    def __init__(
        self,
        api_url: str,
        token: str,
        city: str,
        fallback: ExternalApiCookiesProvider,
        storage_path: str | Path = "storage/cookies_pool.json",
        lease_timeout: int = 40,
        report_timeout: int = 10,
        report_ok_interval: int = 60,
    ):
        self.api_url = api_url.rstrip("/")
        self.token = token
        self.city = city
        self.fallback = fallback
        self.storage_path = Path(storage_path)
        self.lease_timeout = lease_timeout
        self.report_timeout = report_timeout
        self.report_ok_interval = report_ok_interval

        self.cookie_id: int | None = None
        self.current_cookies: dict | None = None
        self.last_status_code: int | None = None
        self.last_report_ok_at: float = 0.0
        self.headers = {"X-Api-Key": self.token, "Content-Type": "application/json"}

    def get(self) -> dict:
        if self.current_cookies:
            return self.current_cookies

        leased = self._lease_from_core()
        if leased is not None:
            return leased

        disk = self._cookies_from_disk()
        if disk is not None:
            logger.warning("Пул кук недоступен — используем последнюю куку с диска")
            self.current_cookies = disk
            return disk

        logger.warning("Пул и диск недоступны — фолбэк на прямой spfa")
        return self.fallback.get()

    def update(self, response) -> None:
        if not response:
            return

        code = getattr(response, "status_code", None)
        if code is None:
            return

        self.last_status_code = code

    def mark_success(self) -> None:
        if self.cookie_id is None:
            return

        now = time.time()
        if now - self.last_report_ok_at < self.report_ok_interval:
            return

        self.last_report_ok_at = now
        self._report(ok=True, status_code=self.last_status_code)

    def handle_block(self) -> None:
        had_pool_cookie = self.cookie_id is not None

        reported = False
        if had_pool_cookie:
            reported = self._report(ok=False, status_code=self.last_status_code)

        self.cookie_id = None
        self.current_cookies = None

        if reported:
            return

        if not had_pool_cookie:
            self._discard_disk()

        logger.warning("Пул недоступен при блокировке — фолбэк handle_block на spfa")
        self.fallback.handle_block()

    def _lease_from_core(self) -> dict | None:
        try:
            res = requests.get(
                f"{self.api_url}/api/internal/avito/cookies/lease",
                headers=self.headers,
                params={"city": self.city} if self.city else None,
                timeout=self.lease_timeout,
            )
        except requests.RequestException as e:
            logger.warning(f"Не удалось получить куку из пула: {e}")
            return None

        if res.status_code == 503:
            retry_after = res.json().get("retry_after") if res.content else None
            logger.info(f"Пул пуст, core просит подождать ~{retry_after}с")
            return None

        if not res.ok:
            logger.warning(f"Пул вернул статус {res.status_code}")
            return None

        try:
            data = res.json()
        except ValueError:
            logger.warning("Пул вернул некорректный JSON")
            return None

        self.cookie_id = data.get("cookie_id")
        self.current_cookies = data.get("cookies")

        if not self.cookie_id or not self.current_cookies:
            logger.warning(f"Пул вернул неполные данные: {data}")
            self.cookie_id = None
            self.current_cookies = None
            return None

        logger.info(f"Получена кука из пула | cookie_id={self.cookie_id}")
        self._save_to_disk()
        return self.current_cookies

    def _report(self, ok: bool, status_code: int | None) -> bool:
        try:
            res = requests.post(
                f"{self.api_url}/api/internal/avito/cookies/report",
                headers=self.headers,
                json={"cookie_id": self.cookie_id, "ok": ok, "status_code": status_code},
                timeout=self.report_timeout,
            )
            return res.ok
        except requests.RequestException as e:
            logger.warning(f"Не удалось отчитаться о куке в пул: {e}")
            return False

    def _save_to_disk(self) -> None:
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "cookie_id": self.cookie_id,
                "cookies": self.current_cookies,
                "saved_at": time.time(),
            }
            self.storage_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as err:
            logger.warning(f"Не удалось сохранить куку пула на диск: {err}")

    def _discard_disk(self) -> None:
        try:
            self.storage_path.unlink(missing_ok=True)
        except Exception as err:
            logger.warning(f"Не удалось удалить устаревшую куку пула с диска: {err}")

    def _cookies_from_disk(self) -> dict | None:
        if not self.storage_path.exists():
            return None
        try:
            data = json.loads(self.storage_path.read_text(encoding="utf-8"))
            cookies = data.get("cookies")
            return cookies or None
        except Exception as err:
            logger.warning(f"Не удалось загрузить куку пула с диска: {err}")
            return None
