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


def run() -> int:
    settings = Settings()
    build_logger(settings.storage_dir)

    if not settings.configured:
        logger.error("PVZ_API_TOKEN не задан — актуализатор не может работать")
        return 1

    core = CoreClient(settings.core_api_url, settings.api_token)
    config = RemoteConfig(
        source="actualizer",
        city=settings.config_city,
        api_url=settings.core_api_url,
        token=settings.api_token,
    )
    heartbeat = Heartbeat(
        source="actualizer",
        city="global",
        api_url=settings.core_api_url,
        token=settings.api_token,
        kind="scrape",
    )
    fetcher = Fetcher(settings, core, settings.cookie_slot)

    logger.info(f"актуализатор запущен | город конфига={settings.config_city} слот={settings.cookie_slot}")

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

        tasks = core.lease(settings.batch_size)

        if not tasks:
            logger.info("очередь пуста — спим")
            time.sleep(settings.idle_sleep)
            continue

        results = []
        parsed = 0

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
            fetcher.pause()

        core.report(results)

        if parsed:
            heartbeat.ok({"checked": len(results), "parsed": parsed})
        else:
            heartbeat.fail("ни одна карточка не разобрана", {"checked": len(results)})


if __name__ == "__main__":
    sys.exit(run())
