import os


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


class Settings:
    def __init__(self) -> None:
        self.core_api_url = os.getenv("CORE_API_URL", "http://core_web:8080")
        self.api_token = os.getenv("PVZ_API_TOKEN", "")
        self.config_city = os.getenv("ACTUALIZER_CONFIG_CITY", "msk")
        self.cookie_slot = os.getenv("ACTUALIZER_COOKIE_SLOT", "act-msk")
        self.batch_size = _int("ACTUALIZER_BATCH", 10)
        self.idle_sleep = _int("ACTUALIZER_IDLE_SLEEP", 300)
        self.disabled_sleep = _int("ACTUALIZER_DISABLED_SLEEP", 60)
        self.pause_min = _float("ACTUALIZER_PAUSE_MIN", 4.0)
        self.pause_max = _float("ACTUALIZER_PAUSE_MAX", 12.0)
        self.request_timeout = _float("ACTUALIZER_REQUEST_TIMEOUT", 60.0)
        self.block_limit = _int("ACTUALIZER_BLOCK_LIMIT", 3)
        self.rotate_cooldown = _int("ACTUALIZER_ROTATE_COOLDOWN", 120)
        self.net_retries = _int("ACTUALIZER_NET_RETRIES", 3)
        self.storage_dir = os.getenv("ACTUALIZER_STORAGE", "storage")

    @property
    def configured(self) -> bool:
        return bool(self.api_token)
