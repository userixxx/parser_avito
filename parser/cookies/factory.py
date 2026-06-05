from loguru import logger

from parser.cookies.base import CookiesProvider
from parser.cookies.external_api import ExternalApiCookiesProvider
from parser.cookies.own_cookies import OwnCookiesProvider


def build_cookies_provider(config) -> CookiesProvider | None:
    if getattr(config, "use_cookie_pool", False):
        from pvz_common.config_boot import load_boot_config, BootConfigError
        from parser.cookies.pooled import PooledCookiesProvider
        try:
            boot = load_boot_config("avito")
            fallback = ExternalApiCookiesProvider(config.cookies_api_key)
            return PooledCookiesProvider(boot.api_url, boot.token, boot.city, fallback)
        except BootConfigError as e:
            logger.warning(f"Пул кук недоступен ({e}) — фолбэк на прямой spfa")
            return ExternalApiCookiesProvider(config.cookies_api_key)
    if config.use_bypass_api:
        return ExternalApiCookiesProvider(config.cookies_api_key)
    elif config.use_own_cookies:
        return OwnCookiesProvider()

    return None


