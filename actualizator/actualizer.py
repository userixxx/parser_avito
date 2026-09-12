import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loguru import logger

from actualizator.core_client import CoreClient
from actualizator.fetcher import Fetcher
from actualizator.settings import Settings
from pvz_common.heartbeat import Heartbeat
from pvz_common.remote_config import RemoteConfig


def build_logger(storage_dir: str) -> None:
    logger.remove()
    logger.add(sys.stdout, level="INFO", enqueue=True)
    log_path = Path(storage_dir) / "actualizer.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger.add(str(log_path), rotation="20 MB", retention=5, level="INFO", enqueue=True)


POOL_CORE_TIMEOUT = 10.0
POOL_CORE_PURCHASE_TIMEOUT = 15.0


class SyncProxyClient:
    def __init__(self, client):
        self._client = client

    @property
    def proxy_url(self):
        return self._client.proxy_url

    @property
    def proxy_id(self):
        return self._client.proxy_id

    def acquire(self) -> bool:
        return asyncio.run(self._client.acquire())

    def release(self) -> None:
        asyncio.run(self._client.release())


def run() -> int:
    settings = Settings()
    build_logger(settings.storage_dir)

    if not settings.configured:
        logger.error("PVZ_API_TOKEN не задан — актуализатор не может работать")
        return 1

    core = CoreClient(settings.core_api_url, settings.api_token, purchase_timeout=settings.cookie_purchase_timeout)
    config = RemoteConfig(
        source="actualizer",
        city=settings.config_city,
        api_url=settings.core_api_url,
        token=settings.api_token,
    )
    if settings.mode == "pool":
        from actualizator.pool_fetcher import PoolFetcher
        from actualizator.pool_worker import run_pool
        from pvz_common.proxy_client import ProxyClient

        pool_heartbeat = Heartbeat(
            source="actualizer_pool",
            city="pool",
            api_url=settings.core_api_url,
            token=settings.api_token,
            kind="scrape",
        )
        pool_core = CoreClient(
            settings.core_api_url,
            settings.api_token,
            timeout=POOL_CORE_TIMEOUT,
            purchase_timeout=POOL_CORE_PURCHASE_TIMEOUT,
        )
        proxy = SyncProxyClient(ProxyClient(None, low_priority=True))
        return run_pool(
            settings,
            pool_core,
            PoolFetcher(settings, pool_core, settings.cookie_slot),
            proxy,
            pool_heartbeat,
        )

    heartbeat = Heartbeat(
        source="actualizer",
        city=settings.heartbeat_city,
        api_url=settings.core_api_url,
        token=settings.api_token,
        kind="scrape",
    )
    fetcher = Fetcher(settings, core, settings.cookie_slot)

    logger.info(
        f"актуализатор запущен | город конфига={settings.config_city} слот={settings.cookie_slot} "
        f"источники={','.join(settings.only_sources) or 'все'} покупка кук={'да' if settings.cookie_buy else 'нет'}"
    )

    while True:
        try:
            snapshot = asyncio.run(config.get())
        except Exception as err:
            logger.warning(f"конфиг недоступен: {err}")
            time.sleep(settings.disabled_sleep)
            continue

        if not snapshot.enabled:
            logger.info("выключен тумблером enabled — спим")
            time.sleep(settings.disabled_sleep)
            continue

        fetcher.apply_config(snapshot.proxy_string, snapshot.proxy_change_url)

        if not fetcher.ready():
            logger.warning("нет прокси — задачи не берём, ждём")
            heartbeat.fail("нет прокси")
            time.sleep(settings.disabled_sleep)
            continue

        tasks = core.lease(settings.batch_size, ",".join(settings.only_sources) or None)

        if not tasks:
            logger.info("очередь пуста — спим")
            time.sleep(settings.idle_sleep)
            continue

        results = []
        parsed = 0
        fetcher.start_batch()

        for task in tasks:
            source = task.get("source") or "avito"
            result, status = fetcher.check(task["source_url"], source)
            parsed += result in ("alive", "not_found")

            results.append({
                "listing_id": task["listing_id"],
                "source": source,
                "result": result,
                "http_code": status,
            })

            logger.info(f"[{task['listing_id']}] {source}/{task['city']} http={status} {result}")

            if fetcher.batch_interrupted:
                logger.warning(
                    f"остаток пачки ({len(tasks) - len(results)} задач) не трогаем — "
                    f"аренда истечёт и они вернутся в очередь"
                )
                break

            fetcher.pause()

        core.report(results)

        if parsed:
            heartbeat.ok({"checked": len(results), "parsed": parsed})
        else:
            heartbeat.fail("ни одна карточка не разобрана", {"checked": len(results)})

        fetcher.periodic_repair()
        fetcher.cooldown()


if __name__ == "__main__":
    sys.exit(run())
