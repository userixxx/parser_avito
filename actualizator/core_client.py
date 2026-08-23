import requests
from loguru import logger


class CoreClient:
    def __init__(self, api_url: str, token: str, timeout: float = 30.0, purchase_timeout: float = 150.0):
        self.api_url = api_url.rstrip("/")
        self.timeout = timeout
        self.purchase_timeout = purchase_timeout
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

    def change_equipment(self, source: str, city: str, reason: str) -> bool:
        try:
            res = requests.post(
                f"{self.api_url}/api/internal/proxy/change-equipment",
                headers=self.headers,
                json={"source": source, "city": city, "reason": reason},
                timeout=120.0,
            )
        except requests.RequestException as err:
            logger.warning(f"смена оборудования не удалась: {err}")
            return False

        if not res.ok:
            logger.warning(f"смена оборудования вернула {res.status_code} {res.text[:200]}")
            return False

        try:
            payload = res.json()
        except ValueError:
            return False

        if payload.get("ok"):
            logger.warning(f"оборудование прокси сменено, новый IP {payload.get('ip', '?')}")
            return True

        logger.warning(f"core отказал в смене оборудования: {payload.get('error', '?')}")
        return False

    def lease_cookie(self, city: str, exclude_id: int | None = None) -> dict | None:
        params = {"city": city}

        if exclude_id:
            params["exclude"] = exclude_id

        try:
            res = requests.get(
                f"{self.api_url}/api/internal/avito/cookies/lease",
                headers=self.headers,
                params=params,
                timeout=self.purchase_timeout,
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

    def replace_cookie(self, slot: str, dead_ids: list[int], reason: str) -> dict | None:
        try:
            res = requests.post(
                f"{self.api_url}/api/internal/avito/cookies/replace",
                headers=self.headers,
                json={"slot": slot, "cookie_ids": dead_ids, "reason": reason},
                timeout=self.purchase_timeout,
            )
        except requests.RequestException as err:
            logger.warning(f"замена куки не удалась: {err}")
            return None

        if res.status_code == 429:
            try:
                payload = res.json()
            except ValueError:
                payload = {}
            logger.warning(
                f"core отказал в замене куки: {payload.get('error', '?')} "
                f"retry_after={payload.get('retry_after', '?')}с"
            )
            return None

        if not res.ok:
            logger.warning(f"замена куки вернула {res.status_code} {res.text[:200]}")
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
