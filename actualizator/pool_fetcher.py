from loguru import logger

from actualizator.fetcher import Fetcher


class PoolFetcher(Fetcher):
    def __init__(self, settings, core, cookie_slot: str):
        super().__init__(settings, core, cookie_slot)
        self.settings.block_retries = 1

    def _escalation_plan(self) -> list[str]:
        return []

    def _on_block(self) -> None:
        if self.consecutive_blocks < self.settings.block_limit:
            return

        self.consecutive_blocks = 0
        self.batch_interrupted = True
        self.cookie = None

    def cooldown(self) -> None:
        self.cookie_starved = False
        self.escalation_pending = False

    def periodic_repair(self) -> None:
        return

    def rotate_ip(self) -> bool:
        return False

    def _ensure_cookie(self) -> dict | None:
        if self.cookie is None:
            self.cookie = self.core.lease_cookie(
                self.cookie_slot,
                allow_purchase=False,
                prefer_idle=True,
            )

            if self.cookie is not None:
                logger.info(
                    f"кука слота {self.cookie_slot}: id={self.cookie.get('cookie_id')} "
                    f"type={self.cookie.get('type')} imp={self.cookie.get('impersonate')}"
                )

        return self.cookie
