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


def _floats(name: str, default: str) -> list[float]:
    values = []

    for chunk in os.getenv(name, default).split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            values.append(float(chunk))
        except ValueError:
            continue

    return values or [float(part) for part in default.split(",")]


class Settings:
    def __init__(self) -> None:
        self.core_api_url = os.getenv("CORE_API_URL", "http://core_web:8080")
        self.api_token = os.getenv("PVZ_API_TOKEN", "")
        self.config_city = os.getenv("ACTUALIZER_CONFIG_CITY", "msk")
        self.cookie_slot = os.getenv("ACTUALIZER_COOKIE_SLOT", "act-msk")
        self.batch_size = _int("ACTUALIZER_BATCH", 10)
        self.idle_sleep = _int("ACTUALIZER_IDLE_SLEEP", 300)
        self.disabled_sleep = _int("ACTUALIZER_DISABLED_SLEEP", 60)
        self.pause_min = _float("ACTUALIZER_PAUSE_MIN", 30.0)
        self.pause_max = _float("ACTUALIZER_PAUSE_MAX", 60.0)
        self.request_timeout = _float("ACTUALIZER_REQUEST_TIMEOUT", 60.0)
        self.block_limit = _int("ACTUALIZER_BLOCK_LIMIT", 3)
        self.rotate_cooldown = _int("ACTUALIZER_ROTATE_COOLDOWN", 120)
        self.net_retries = _int("ACTUALIZER_NET_RETRIES", 3)
        self.backoff_ladder = _floats("ACTUALIZER_BACKOFF_LADDER", "60,180,600,1800")
        self.rotate_pause = _float("ACTUALIZER_ROTATE_PAUSE", 120.0)
        self.equipment_pause = _float("ACTUALIZER_EQUIPMENT_PAUSE", 300.0)
        self.halt_sleep = _float("ACTUALIZER_HALT_SLEEP", 3600.0)
        self.no_cookie_sleep = _float("ACTUALIZER_NO_COOKIE_SLEEP", 600.0)
        self.equipment_city = os.getenv("ACTUALIZER_EQUIPMENT_CITY", "global")
        self.avito_mobile = _bool("ACTUALIZER_AVITO_MOBILE", False)
        self.avito_mobile_host = os.getenv("ACTUALIZER_AVITO_MOBILE_HOST", "m.avito.ru")
        self.mobile_rotate_pause = _float("ACTUALIZER_MOBILE_ROTATE_PAUSE", 20.0)
        self.storage_dir = os.getenv("ACTUALIZER_STORAGE", "storage")

    @property
    def configured(self) -> bool:
        return bool(self.api_token)
