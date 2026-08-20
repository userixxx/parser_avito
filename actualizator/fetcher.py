import random
import time

import requests
from curl_cffi import requests as cffi
from curl_cffi.requests import BrowserType
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

UNSAFE_COOKIE_HEADERS = ("host", "content-length", "connection", "accept-encoding")

ACTION_BACKOFF = "backoff"
ACTION_SWAP_COOKIE = "cookie"
ACTION_ROTATE_IP = "rotate"
ACTION_CHANGE_EQUIPMENT = "equipment"
ACTION_HALT = "halt"


def known_impersonate(value: str | None) -> str | None:
    if not value:
        return None

    try:
        supported = {profile.value for profile in BrowserType}
    except Exception:
        return None

    if value in supported:
        return value

    logger.warning(f"отпечаток {value} не поддерживается curl_cffi — остаёмся на {DEFAULT_IMPERSONATE}")
    return None


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
        self.escalation_step = 0
        self.escalation_pending = False
        self.batch_interrupted = False
        self.cookie_starved = False
        self.last_blocked: tuple[str, str] | None = None

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

    def _lease_cookie(self, exclude_id: int | None = None) -> dict | None:
        leased = self.core.lease_cookie(self.cookie_slot, exclude_id)

        if leased is None:
            return None

        logger.info(
            f"кука слота {self.cookie_slot}: id={leased.get('cookie_id')} "
            f"type={leased.get('type')} imp={leased.get('impersonate')}"
        )

        if self.settings.avito_mobile and leased.get("type") != "mobile":
            logger.warning(
                f"мобильный режим включён, а пул отдал куку типа {leased.get('type')} — "
                f"карточки {self.settings.avito_mobile_host} её не примут, "
                f"нужен AVITO_COOKIE_TYPE_OVERRIDES={self.cookie_slot}:mobile"
            )

        return leased

    def _ensure_cookie(self) -> dict | None:
        if self.cookie is None:
            self.cookie = self._lease_cookie()

        return self.cookie

    def _cookie_passport(self, cookie: dict) -> dict:
        passport: dict = {"cookies": cookie.get("cookies") or {}}

        impersonate = known_impersonate(cookie.get("impersonate"))
        if impersonate:
            passport["impersonate"] = impersonate

        headers = {
            name: value
            for name, value in (cookie.get("headers") or {}).items()
            if name.lower() not in UNSAFE_COOKIE_HEADERS
        }

        user_agent = cookie.get("user_agent")
        if user_agent:
            headers.setdefault("User-Agent", user_agent)

        if headers:
            passport["headers"] = headers

        return passport

    def _request_kwargs(self, source: str, cookie: dict | None) -> dict:
        kwargs = {
            "proxies": self._proxies(),
            "timeout": self.settings.request_timeout,
            "impersonate": DEFAULT_IMPERSONATE,
            "stream": True,
        }

        if self.profile(source)["cookie"] and cookie:
            kwargs.update(self._cookie_passport(cookie))

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

    def _get(self, url: str, source: str, cookie: dict | None) -> tuple[int, str]:
        response = cffi.get(url, **self._request_kwargs(source, cookie))
        try:
            body = self._read(response, self.profile(source)["read_limit"])
            return response.status_code, body
        finally:
            response.close()

    def _target_url(self, url: str, source: str) -> str:
        if source != "avito" or not self.settings.avito_mobile:
            return url

        return url.replace("://www.avito.ru", f"://{self.settings.avito_mobile_host}", 1)

    def fetch(self, url: str, source: str) -> tuple[int, str]:
        if self.profile(source)["cookie"] and self._ensure_cookie() is None:
            logger.warning(
                f"нет куки для слота {self.cookie_slot} — пачка прервана, "
                f"иначе попытки задач сгорят впустую"
            )
            self.cookie_starved = True
            self.batch_interrupted = True
            return 0, ""

        target = self._target_url(url, source)

        for attempt in range(1, self.settings.net_retries + 1):
            try:
                status, body = self._get(target, source, self.cookie)
                self.last_status = status
                return status, body
            except Exception as err:
                logger.warning(f"сетевая ошибка {attempt}/{self.settings.net_retries}: {str(err)[:120]}")
                time.sleep(4)

        return 0, ""

    def start_batch(self) -> None:
        self.batch_interrupted = False
        self.cookie_starved = False

    def check(self, url: str, source: str) -> tuple[str, int]:
        status, body = self.fetch(url, source)
        mobile = source == "avito" and self.settings.avito_mobile
        result = classify(source, status, body, mobile=mobile) if status else "error"

        if result == "blocked":
            self.consecutive_blocks += 1
            self.last_blocked = (url, source)
            self._on_block()
        elif result in ("alive", "not_found"):
            self.consecutive_blocks = 0
            self.escalation_step = 0
            if self.cookie and self.profile(source)["cookie"]:
                self.core.report_cookie(self.cookie["cookie_id"], True, status)
        else:
            self.consecutive_blocks = 0

        return result, status

    def _on_block(self) -> None:
        if self.consecutive_blocks < self.settings.block_limit:
            return

        self.consecutive_blocks = 0
        self.escalation_step += 1
        self.escalation_pending = True
        self.batch_interrupted = True

        action, _ = self._escalation_action()

        logger.warning(
            f"{self.settings.block_limit} блокировок подряд — пачка прервана, "
            f"ступень {self.escalation_step} ({action}) выполнится после отчёта"
        )

    def _escalation_plan(self) -> list[tuple[str, float]]:
        if self.settings.avito_mobile:
            return [
                (ACTION_ROTATE_IP, self.settings.mobile_rotate_pause),
                (ACTION_ROTATE_IP, self.settings.mobile_rotate_pause),
                (ACTION_SWAP_COOKIE, 0.0),
                (ACTION_ROTATE_IP, self.settings.mobile_rotate_pause),
                (ACTION_BACKOFF, self.settings.backoff_ladder[0]),
                (ACTION_CHANGE_EQUIPMENT, self.settings.equipment_pause),
                (ACTION_HALT, self.settings.halt_sleep),
            ]

        plan = [(ACTION_BACKOFF, pause) for pause in self.settings.backoff_ladder]
        plan.append((ACTION_SWAP_COOKIE, 0.0))
        plan.append((ACTION_ROTATE_IP, self.settings.rotate_pause))
        plan.append((ACTION_ROTATE_IP, self.settings.rotate_pause))
        plan.append((ACTION_CHANGE_EQUIPMENT, self.settings.equipment_pause))
        plan.append((ACTION_HALT, self.settings.halt_sleep))

        return plan

    def _escalation_action(self) -> tuple[str, float]:
        plan = self._escalation_plan()
        index = min(max(self.escalation_step, 1), len(plan)) - 1

        return plan[index]

    def cooldown(self) -> None:
        if self.cookie_starved:
            self.cookie_starved = False
            self._sleep_off(self.settings.no_cookie_sleep, "пул кук пуст — ждём пополнения")
            return

        if not self.escalation_pending:
            return

        self.escalation_pending = False
        action, pause = self._escalation_action()

        if action == ACTION_BACKOFF:
            self._sleep_off(pause, "бэкофф: даём лимиту Авито остыть")
            return

        if action == ACTION_SWAP_COOKIE:
            if self._swap_blocked_cookie():
                self.escalation_step = 0
                return
            self._sleep_off(self.settings.backoff_ladder[-1], "сменить куку не вышло — ждём")
            return

        if action == ACTION_ROTATE_IP:
            self.rotate_ip()
            self._sleep_off(pause, "после смены IP выжидаем")
            return

        if action == ACTION_CHANGE_EQUIPMENT:
            changed = self.core.change_equipment(
                "actualizer",
                self.settings.equipment_city,
                f"актуализатор: блокировка держится {self.escalation_step} ступеней подряд",
            )
            self._sleep_off(pause, "оборудование сменено — выжидаем" if changed else "сменить оборудование не вышло — ждём")
            return

        self._sleep_off(pause, "лестница пройдена целиком, блок не снят — уходим в долгий сон")
        self.escalation_step = 0

    def _sleep_off(self, seconds: float, reason: str) -> None:
        logger.warning(f"{reason}: спим {seconds:.0f}с")
        time.sleep(seconds)

    def _swap_blocked_cookie(self) -> bool:
        if self.last_blocked is None:
            return False

        url, source = self.last_blocked

        if not self.profile(source)["cookie"] or self.cookie is None:
            return False

        current_id = self.cookie["cookie_id"]
        spare = self._lease_cookie(current_id)

        if spare is None:
            logger.info("второй куки в пуле нет — обвинить куку нечем, идём дальше по лестнице")
            return False

        try:
            status, body = self._get(url, source, spare)
        except Exception as err:
            logger.warning(f"проба второй кукой не удалась: {str(err)[:120]}")
            return False

        verdict = classify(source, status, body) if status else "error"

        if verdict not in ("alive", "not_found"):
            logger.warning(f"дискриминатор: вторая кука тоже {verdict} ({status}) — виновата не кука")
            return False

        logger.warning(
            f"дискриминатор: вторая кука {spare.get('cookie_id')} отдала {verdict} — "
            f"кука {current_id} признана заблокированной"
        )
        self.core.report_cookie(current_id, False, self.last_status)
        self.core.report_cookie(spare["cookie_id"], True, status)
        self.cookie = spare

        return True

    def rotate_ip(self) -> bool:
        if not self.change_ip_url:
            return False

        elapsed = time.time() - self.last_rotate_at
        cooldown = (
            self.settings.mobile_rotate_cooldown
            if self.settings.avito_mobile
            else self.settings.rotate_cooldown
        )

        if elapsed < cooldown:
            wait = cooldown - elapsed
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
