import os

from loguru import logger

from dto import AvitoConfig
from .proxy import NoProxy, ServerProxy, MobileProxy, Proxy


def build_proxy(config: AvitoConfig, on_rotation_failed=None) -> Proxy:
    if config.proxy_change_url and not config.proxy_string:
        raise ValueError("proxy_change_url указан без proxy_string")

    if config.proxy_string and config.proxy_change_url:
        logger.info("Прокси определен как мобильный")
        kwargs = {}
        if getattr(config, "mobile_mode", False):
            kwargs["cooldown"] = resolve_rotate_cooldown(config.mobile_rotate_cooldown)
            logger.info(f"Мобильный режим: ротация IP не чаще раза в {kwargs['cooldown']}с")

        return MobileProxy(
            config.proxy_string,
            config.proxy_change_url,
            api_proxy=resolve_api_proxy(),
            on_rotation_failed=on_rotation_failed,
            **kwargs,
        )

    if config.proxy_string:
        logger.info("Прокси определен как серверный")
        return ServerProxy(config.proxy_string)

    return NoProxy()


def resolve_rotate_cooldown(fallback: int) -> int:
    raw = os.getenv("AVITO_MOBILE_ROTATE_COOLDOWN")

    if not raw:
        return fallback

    try:
        return max(0, int(raw))
    except ValueError:
        logger.warning(f"AVITO_MOBILE_ROTATE_COOLDOWN={raw!r} не число, беру {fallback}с")
        return fallback


def resolve_api_proxy() -> str | None:
    return os.getenv("MOBILEPROXY_PROXY") or os.getenv("TELEGRAM_PROXY") or None
