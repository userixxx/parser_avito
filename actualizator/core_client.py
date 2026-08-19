import requests
from loguru import logger


class CoreClient:
    def __init__(self, api_url: str, token: str, timeout: float = 30.0):
        self.api_url = api_url.rstrip("/")
        self.timeout = timeout
        self.headers = {"X-Api-Key": token, "Content-Type": "application/json"}

    def lease(self, limit: int) -> list[dict]:
        try:
            res = requests.get(
                f"{self.api_url}/api/internal/actualization/lease",
                headers=self.headers,
                params={"limit": limit},
                timeout=self.timeout,
            )
        except requests.RequestException as err:
            logger.warning(f"lease не удался: {err}")
            return []

        if not res.ok:
            logger.warning(f"lease вернул {res.status_code}")
            return []

        try:
            return res.json().get("tasks") or []
        except ValueError:
            logger.warning("lease вернул некорректный JSON")
            return []

    def report(self, results: list[dict]) -> bool:
        if not results:
            return True

        try:
            res = requests.post(
                f"{self.api_url}/api/internal/actualization/report",
                headers=self.headers,
                json={"results": results},
                timeout=self.timeout,
            )
        except requests.RequestException as err:
            logger.warning(f"report не удался: {err}")
            return False

        if not res.ok:
            logger.warning(f"report вернул {res.status_code} {res.text[:200]}")
            return False

        logger.info(f"report принят: {res.json().get('accepted')}")
        return True

    def lease_cookie(self, city: str) -> dict | None:
        try:
            res = requests.get(
                f"{self.api_url}/api/internal/avito/cookies/lease",
                headers=self.headers,
                params={"city": city},
                timeout=self.timeout,
            )
        except requests.RequestException as err:
            logger.warning(f"кука недоступна: {err}")
            return None

        if res.status_code == 503:
            logger.info("пул кук пуст")
            return None

        if not res.ok:
            logger.warning(f"пул кук вернул {res.status_code}")
            return None

        try:
            data = res.json()
        except ValueError:
            return None

        if not data.get("cookies"):
            return None

        return data

    def report_cookie(self, cookie_id: int, ok: bool, status_code: int | None) -> None:
        try:
            requests.post(
                f"{self.api_url}/api/internal/avito/cookies/report",
                headers=self.headers,
                json={"cookie_id": cookie_id, "ok": ok, "status_code": status_code},
                timeout=self.timeout,
            )
        except requests.RequestException as err:
            logger.warning(f"не удалось отчитаться о куке: {err}")
