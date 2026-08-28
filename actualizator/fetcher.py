import json
import random
import time
from pathlib import Path

import requests
from curl_cffi import requests as cffi
from curl_cffi.requests import BrowserType
from loguru import logger

from actualizator.classify import BLOCK_CODES, classify

IMPERSONATE_POOL = ("tor", "edge", "firefox", "safari")
CHROME_VERSION_RANGE = (140, 147)
ROTATION_CHANNELS = (
    "https://changeip.mobileproxy.space/",
    "http://aproxy.site/",
)
MOBILEPROXY_API = "https://mobileproxy.space/api.html"

SOURCE_PROFILES = {
    "avito": {"cookie": True, "read_limit": 1_200_000},
    "cian": {"cookie": False, "read_limit": 900_000},
    "yandex": {"cookie": False, "read_limit": 900_000},
}

DEFAULT_PROFILE = {"cookie": False, "read_limit": 1_200_000}

UNSAFE_COOKIE_HEADERS = ("host", "content-length", "connection", "accept-encoding")

ACTION_SWAP_COOKIE = "cookie"
ACTION_ROTATE_IP = "rotate"
ACTION_CHANGE_EQUIPMENT = "equipment"
COOKIE_BUDGET_FILE = "cookie_budget.json"
DAY_SECONDS = 86400.0


def known_impersonate(value: str | None) -> str | None:
    if not value:
        return None

    try:
        supported = {profile.value for profile in BrowserType}
    except Exception:
        return None

    if value in supported:
        return value

    logger.warning(f"отпечаток {value} не поддерживается curl_cffi — берём случайный из пула")
    return None


def random_user_agent() -> str:
    version = random.randint(*CHROME_VERSION_RANGE)

    return (
        f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        f"AppleWebKit/537.36 (KHTML, like Gecko) "
        f"Chrome/{version}.0.0.0 Safari/537.36"
    )


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
        self.last_success_at = time.time()
        self.avito_success_at = time.time()
        self.last_repair_at = 0.0
        self.last_cookie_buy_at = time.time()
        self.last_equipment_at = time.time()
        self.consecutive_net_errors = 0
        self.net_error_sources: set[str] = set()
        self.cookie_budget_path = Path(settings.storage_dir) / COOKIE_BUDGET_FILE

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

    def _cookie_budget(self, now: float) -> list[list]:
        try:
            saved = json.loads(self.cookie_budget_path.read_text())
        except (OSError, ValueError):
            return []

        if not isinstance(saved, list):
            return []

        window = [row for row in saved if isinstance(row, list) and len(row) == 2 and now - row[0] < DAY_SECONDS]

        return sorted(window, key=lambda row: row[0])

    def _cookie_budget_wait(self) -> float:
        now = time.time()
        window = self._cookie_budget(now)
        interval_left = 0.0

        if window:
            interval_left = max(0.0, self.settings.cookie_min_interval - (now - window[-1][0]))

        if len(window) < self.settings.cookie_daily_cap:
            return interval_left

        return max(interval_left, window[0][0] + DAY_SECONDS - now)

    def _cookie_budget_allows(self) -> bool:
        wait = self._cookie_budget_wait()

        if wait <= 0:
            return True

        window = self._cookie_budget(time.time())
        logger.info(
            f"бюджет кук {len(window)}/{self.settings.cookie_daily_cap} за сутки — "
            f"следующая покупка через {wait / 60:.0f} мин"
        )

        return False

    def _register_cookie(self, cookie_id) -> None:
        if cookie_id is None:
            return

        now = time.time()
        window = self._cookie_budget(now)

        if any(row[1] == cookie_id for row in window):
            return

        window.append([now, cookie_id])

        try:
            self.cookie_budget_path.parent.mkdir(parents=True, exist_ok=True)
            self.cookie_budget_path.write_text(json.dumps(window))
        except OSError as err:
            logger.warning(f"бюджет кук не сохранён: {str(err)[:120]}")

        logger.info(f"бюджет кук: израсходовано {len(window)}/{self.settings.cookie_daily_cap} за сутки")

    def _lease_cookie(self, exclude_id: int | None = None, allow_purchase: bool = True) -> dict | None:
        leased = self.core.lease_cookie(self.cookie_slot, exclude_id, allow_purchase)

        if leased is None:
            return None

        if allow_purchase:
            self._register_cookie(leased.get("cookie_id"))

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
            self.cookie = self._lease_cookie(allow_purchase=self._cookie_budget_allows())

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
            "impersonate": random.choice(IMPERSONATE_POOL),
            "stream": True,
        }

        if self.profile(source)["cookie"] and cookie:
            kwargs.update(self._cookie_passport(cookie))

        headers = dict(kwargs.get("headers") or {})
        headers.setdefault("User-Agent", random_user_agent())
        kwargs["headers"] = headers

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
        status, body = 0, ""

        for attempt in range(1, self.settings.block_retries + 1):
            status, body = self._fetch_once(target, source)
            self.last_status = status

            if status not in BLOCK_CODES:
                return status, body

            if attempt < self.settings.block_retries:
                logger.info(
                    f"блок {status}, попытка {attempt}/{self.settings.block_retries} "
                    f"— повторяем с новым отпечатком"
                )
                time.sleep(self.settings.block_retry_pause)

        return status, body

    def _fetch_once(self, target: str, source: str) -> tuple[int, str]:
        for attempt in range(1, self.settings.net_retries + 1):
            try:
                return self._get(target, source, self.cookie)
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

        if status:
            self.consecutive_net_errors = 0
            self.net_error_sources.clear()
        elif not self.cookie_starved:
            self.consecutive_net_errors += 1
            self.net_error_sources.add(source)

        if result == "blocked":
            self.consecutive_blocks += 1
            self.last_blocked = (url, source)
            self._on_block()
        elif result in ("alive", "not_found"):
            self.consecutive_blocks = 0
            self.escalation_step = 0
            self.last_success_at = time.time()
            if source == "avito":
                self.avito_success_at = time.time()
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

        logger.warning(
            f"{self.settings.block_limit} блокировок подряд — пачка прервана, "
            f"ступень {self.escalation_step} ({self._escalation_action()}) выполнится после отчёта"
        )

    def _escalation_plan(self) -> list[str]:
        if self.settings.avito_mobile:
            return [ACTION_ROTATE_IP, ACTION_SWAP_COOKIE, ACTION_ROTATE_IP]

        return [ACTION_SWAP_COOKIE, ACTION_ROTATE_IP, ACTION_ROTATE_IP, ACTION_CHANGE_EQUIPMENT]

    def _escalation_action(self) -> str:
        plan = self._escalation_plan()
        index = min(max(self.escalation_step, 1), len(plan)) - 1

        return plan[index]

    def cooldown(self) -> None:
        if self.cookie_starved:
            self.cookie_starved = False
            self._wait_for_cookie_budget()
            return

        if not self.escalation_pending:
            return

        self.escalation_pending = False
        action = self._escalation_action()

        if action == ACTION_SWAP_COOKIE and self._swap_blocked_cookie():
            self.escalation_step = 0
            return

        if action == ACTION_ROTATE_IP:
            self.rotate_ip()

        if action == ACTION_CHANGE_EQUIPMENT:
            changed = self.core.change_equipment(
                "actualizer",
                self.settings.equipment_city,
                f"актуализатор: блокировка держится {self.escalation_step} ступеней подряд",
            )
            logger.warning("оборудование сменено" if changed else "сменить оборудование не вышло")

        if self.escalation_step >= len(self._escalation_plan()):
            self.escalation_step = 0

    def _wait_for_cookie_budget(self) -> None:
        wait = self._cookie_budget_wait()

        if wait <= 0:
            return

        logger.warning(f"куки нет, бюджет исчерпан — ждём окна покупки {wait / 60:.0f} мин")
        time.sleep(wait)

    def _swap_blocked_cookie(self) -> bool:
        if self.last_blocked is None:
            return False

        url, source = self.last_blocked

        if not self.profile(source)["cookie"] or self.cookie is None:
            return False

        current_id = self.cookie["cookie_id"]
        spare = self._lease_cookie(current_id, allow_purchase=False)

        if spare is None:
            logger.info("второй куки в пуле нет — обвинить куку нечем")
            return self._replace_worn_cookie([current_id])

        try:
            status, body = self._get(url, source, spare)
        except Exception as err:
            logger.warning(f"проба второй кукой не удалась: {str(err)[:120]}")
            return self._replace_worn_cookie([current_id])

        verdict = classify(source, status, body) if status else "error"

        if verdict not in ("alive", "not_found"):
            logger.warning(f"дискриминатор: вторая кука тоже {verdict} ({status}) — виновата не кука")
            worn = [current_id, spare["cookie_id"]] if verdict == "blocked" else [current_id]
            return self._replace_worn_cookie(worn)

        logger.warning(
            f"дискриминатор: вторая кука {spare.get('cookie_id')} отдала {verdict} — "
            f"кука {current_id} признана заблокированной"
        )
        self.core.report_cookie(current_id, False, self.last_status)
        self.core.report_cookie(spare["cookie_id"], True, status)
        self.cookie = spare

        return True

    def _replace_worn_cookie(self, worn_ids: list[int]) -> bool:
        idle = time.time() - self.last_success_at

        if idle < self.settings.cookie_stale_seconds:
            logger.info(
                f"простой {idle / 60:.0f} мин меньше порога "
                f"{self.settings.cookie_stale_seconds / 60:.0f} мин — куку не меняем"
            )
            return False

        if not self._cookie_budget_allows():
            return False

        fresh = self.core.replace_cookie(
            self.cookie_slot,
            worn_ids,
            f"нет успешных проверок {idle / 60:.0f} мин, куки {worn_ids} изношены",
        )

        if fresh is None:
            return False

        self._register_cookie(fresh.get("cookie_id"))

        logger.warning(
            f"куплена свежая кука {fresh.get('cookie_id')} взамен {worn_ids} — "
            f"простой был {idle / 60:.0f} мин"
        )
        self.cookie = fresh
        self.last_success_at = time.time()
        self.last_cookie_buy_at = time.time()

        return True

    def channel_looks_dead(self) -> bool:
        limit = self.settings.dead_channel_errors

        return limit > 0 and self.consecutive_net_errors >= limit

    def periodic_repair(self) -> None:
        if self.settings.repair_idle_seconds <= 0:
            return

        now = time.time()

        if self.channel_looks_dead() and now - self.last_equipment_at >= self.settings.dead_equipment_seconds:
            sources = ", ".join(sorted(self.net_error_sources)) or "?"
            self._repair_equipment(
                now,
                True,
                f"канал не отвечает: {self.consecutive_net_errors} сетевых ошибок подряд ({sources})",
            )
            return

        if now - max(self.avito_success_at, self.last_repair_at) < self.settings.repair_idle_seconds:
            return

        idle = (now - self.avito_success_at) / 60

        if now - self.last_equipment_at >= self.settings.repair_equipment_seconds:
            self._repair_equipment(now, False, f"нет вердиктов Авито {idle:.0f} мин")
            return

        if now - self.last_cookie_buy_at >= self.settings.repair_cookie_seconds:
            if self._repair_cookie(now, idle):
                self.rotate_ip()
                self.last_repair_at = time.time()
                return
            self.last_cookie_buy_at = now - self.settings.repair_cookie_seconds + 300

        if now - self.last_rotate_at >= self.settings.repair_rotate_seconds:
            logger.warning(f"ремонт: нет вердиктов Авито {idle:.0f} мин — меняем IP")
            self.rotate_ip()
            self.last_repair_at = time.time()

    def _repair_cookie(self, now: float, idle: float) -> bool:
        if not self._cookie_budget_allows():
            return False

        worn = [self.cookie["cookie_id"]] if self.cookie else []
        fresh = self.core.replace_cookie(
            self.cookie_slot,
            worn,
            f"ремонт актуализатора: нет вердиктов Авито {idle:.0f} мин",
        )

        if fresh is None:
            return False

        self._register_cookie(fresh.get("cookie_id"))

        logger.warning(f"ремонт: куплена свежая кука {fresh.get('cookie_id')} взамен {worn} — меняем и IP")
        self.cookie = fresh
        self.last_cookie_buy_at = now
        self.last_success_at = time.time()

        return True

    def _repair_equipment(self, now: float, blacklist: bool, reason: str) -> None:
        logger.warning(f"ремонт: {reason} — меняем оборудование")
        self.last_equipment_at = now
        self.last_repair_at = now
        self.consecutive_net_errors = 0
        self.net_error_sources.clear()

        changed = self._change_equipment_direct(blacklist) or self.core.change_equipment(
            "actualizer", self.settings.equipment_city, f"актуализатор: {reason}"
        )

        logger.warning("ремонт: оборудование сменено" if changed else "ремонт: сменить оборудование не вышло")
        self.last_repair_at = time.time()

    def _proxy_identity(self) -> tuple[int, int] | None:
        if not self.settings.mobileproxy_token or not self.proxy_string:
            return None

        login = self.proxy_string.split("//")[-1].split(":")[0]

        try:
            response = requests.get(
                MOBILEPROXY_API,
                params={"command": "get_my_proxy"},
                headers={"Authorization": f"Bearer {self.settings.mobileproxy_token}"},
                timeout=40,
            )
            rows = response.json()
        except Exception as err:
            logger.warning(f"список прокси mobileproxy недоступен: {str(err)[:120]}")
            return None

        if isinstance(rows, dict):
            rows = rows.get("list") or []

        for row in rows:
            if row.get("proxy_login") == login:
                return int(row["proxy_id"]), int(row["id_city"])

        logger.warning(f"прокси с логином {login} в аккаунте mobileproxy не найден")
        return None

    def _change_equipment_direct(self, blacklist: bool) -> bool:
        identity = self._proxy_identity()

        if identity is None:
            return False

        proxy_id, id_city = identity
        params = {
            "command": "change_equipment",
            "proxy_id": proxy_id,
            "id_city": id_city,
            "check_after_change": "true",
        }

        if blacklist:
            params["add_to_black_list"] = 1

        try:
            response = requests.get(
                MOBILEPROXY_API,
                params=params,
                headers={"Authorization": f"Bearer {self.settings.mobileproxy_token}"},
                timeout=180,
            )
            payload = response.json()
        except Exception as err:
            logger.warning(f"смена оборудования не удалась: {str(err)[:120]}")
            return False

        if payload.get("status") != "ok":
            logger.warning(f"mobileproxy отказал в смене оборудования: {str(payload)[:160]}")
            return False

        checked = payload.get("checked") or {}
        logger.warning(
            f"оборудование прокси {proxy_id} сменено, новый IP {checked.get(str(proxy_id), '?')}, "
            f"город {id_city}, блэклист {'да' if blacklist else 'нет'}"
        )

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
