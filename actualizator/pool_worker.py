import random
import time

from loguru import logger


class PoolCycle:
    def __init__(self, settings, core, fetcher, proxy):
        self.settings = settings
        self.core = core
        self.fetcher = fetcher
        self.proxy = proxy

    def tick(self) -> str:
        if not self.proxy.acquire():
            return "busy"

        proxy_id = self.proxy.proxy_id

        if not self.proxy.proxy_url:
            self.proxy.release()
            logger.warning("пул отдал канал без адреса — запрос без прокси не делаем")
            return "busy"

        try:
            self.fetcher.apply_config(self.proxy.proxy_url, None)
            tasks = self.core.lease(1, source="avito")

            if not tasks:
                return "empty"

            task = tasks[0]
            result, status = self.fetcher.check(task["source_url"], "avito")
        finally:
            self.proxy.release()

        if self.fetcher.cookie_starved:
            self.fetcher.cookie_starved = False
            logger.warning("в слоте нет активной куки — ждём, задачу не отчитываем")
            return "starved"

        logger.info(f"[{task['listing_id']}] avito/{task['city']} proxy=#{proxy_id} http={status} {result}")

        self.core.report([{
            "listing_id": task["listing_id"],
            "source": "avito",
            "result": result,
            "http_code": status,
        }])

        return "done"


def run_pool(settings, core, fetcher, proxy, heartbeat) -> int:
    logger.info(f"актуализатор-пул запущен | город выбирает пул | слот={settings.cookie_slot}")

    while True:
        try:
            outcome = PoolCycle(settings, core, fetcher, proxy).tick()
        except Exception as err:
            logger.warning(f"цикл пулового воркера упал: {str(err)[:200]}")
            heartbeat.fail(str(err)[:200])
            time.sleep(settings.disabled_sleep)
            continue

        if outcome == "done":
            heartbeat.ok({"checked": 1})
            time.sleep(random.uniform(settings.pause_min, settings.pause_max))
        elif outcome == "busy":
            time.sleep(settings.busy_sleep)
        elif outcome == "starved":
            heartbeat.fail("нет активной куки в слоте")
            time.sleep(settings.idle_sleep)
        else:
            time.sleep(settings.idle_sleep)
