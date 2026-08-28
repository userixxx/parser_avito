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


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)

    if value is None:
        return default

    return value.strip().lower() in ("1", "true", "yes", "on")


class Settings:
    def __init__(self) -> None:
        self.core_api_url = os.getenv("CORE_API_URL", "http://core_web:8080")
        self.api_token = os.getenv("PVZ_API_TOKEN", "")
        self.config_city = os.getenv("ACTUALIZER_CONFIG_CITY", "msk")
        self.cookie_slot = os.getenv("ACTUALIZER_COOKIE_SLOT", "act-msk")
        self.batch_size = _int("ACTUALIZER_BATCH", 3)
        self.idle_sleep = _int("ACTUALIZER_IDLE_SLEEP", 300)
        self.disabled_sleep = _int("ACTUALIZER_DISABLED_SLEEP", 60)
        self.pause_min = _float("ACTUALIZER_PAUSE_MIN", 30.0)
        self.pause_max = _float("ACTUALIZER_PAUSE_MAX", 60.0)
        self.request_timeout = _float("ACTUALIZER_REQUEST_TIMEOUT", 60.0)
        self.block_limit = _int("ACTUALIZER_BLOCK_LIMIT", 3)
        self.rotate_cooldown = _int("ACTUALIZER_ROTATE_COOLDOWN", 120)
        self.net_retries = _int("ACTUALIZER_NET_RETRIES", 3)
        self.block_retries = _int("ACTUALIZER_BLOCK_RETRIES", 5)
        self.block_retry_pause = _float("ACTUALIZER_BLOCK_RETRY_PAUSE", 5.0)
        self.cookie_daily_cap = _int("ACTUALIZER_COOKIE_DAILY_CAP", 30)
        self.cookie_min_interval = _float("ACTUALIZER_COOKIE_MIN_INTERVAL", 2880.0)
        self.equipment_city = os.getenv("ACTUALIZER_EQUIPMENT_CITY", "global")
        self.avito_mobile = _bool("ACTUALIZER_AVITO_MOBILE", False)
        self.avito_mobile_host = os.getenv("ACTUALIZER_AVITO_MOBILE_HOST", "m.avito.ru")
        self.mobile_rotate_cooldown = _int("ACTUALIZER_MOBILE_ROTATE_COOLDOWN", 300)
        self.cookie_stale_seconds = _float("ACTUALIZER_COOKIE_STALE_MINUTES", 15.0) * 60
        self.cookie_purchase_timeout = _float("ACTUALIZER_COOKIE_PURCHASE_TIMEOUT", 150.0)
        self.repair_idle_seconds = _float("ACTUALIZER_REPAIR_IDLE_MINUTES", 10.0) * 60
        self.repair_rotate_seconds = _float("ACTUALIZER_REPAIR_ROTATE_MINUTES", 12.0) * 60
        self.repair_cookie_seconds = _float("ACTUALIZER_REPAIR_COOKIE_MINUTES", 30.0) * 60
        self.repair_equipment_seconds = _float("ACTUALIZER_REPAIR_EQUIPMENT_MINUTES", 45.0) * 60
        self.dead_channel_errors = _int("ACTUALIZER_DEAD_CHANNEL_ERRORS", 4)
        self.dead_equipment_seconds = _float("ACTUALIZER_DEAD_EQUIPMENT_MINUTES", 15.0) * 60
        self.mobileproxy_token = os.getenv("MOBILEPROXY_API_TOKEN", "")
        self.storage_dir = os.getenv("ACTUALIZER_STORAGE", "storage")

    @property
    def configured(self) -> bool:
        return bool(self.api_token)
