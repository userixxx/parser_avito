import re

BLOCK_CODES = (403, 429, 439)

AVITO_NOT_FOUND_MARKERS = (
    '"type":"notFound"',
    '\\"type\\":\\"notFound\\"',
)

AVITO_ALIVE_MARKERS = (
    'data-marker="item-view',
    '"type":"item"',
    '\\"type\\":\\"item\\"',
    "item-view/item-price",
)

CIAN_OFFER_STATUS = re.compile(r'"offerData":\{"offer":\{[^}]{0,400}?"status":"([a-zA-Z_]+)"')
CIAN_ALIVE_STATUS = "published"
CIAN_REMOVED_TEXT = "Объявление снято с публикации"

YANDEX_SSR_FAIL = re.compile(r"_ssr_fail_status_code=(\d+)")
YANDEX_ALIVE_MARKER = "<title"


def classify_avito(body: str) -> str:
    if any(marker in body for marker in AVITO_NOT_FOUND_MARKERS):
        return "not_found"

    if any(marker in body for marker in AVITO_ALIVE_MARKERS):
        return "alive"

    return "blocked"


def classify_cian(body: str) -> str:
    match = CIAN_OFFER_STATUS.search(body)

    if match is not None:
        return "alive" if match.group(1) == CIAN_ALIVE_STATUS else "not_found"

    if CIAN_REMOVED_TEXT in body:
        return "not_found"

    return "blocked"


def classify_yandex(body: str) -> str:
    ssr_fail = YANDEX_SSR_FAIL.search(body)

    if ssr_fail is not None:
        return "not_found" if ssr_fail.group(1) == "404" else "blocked"

    if YANDEX_ALIVE_MARKER in body:
        return "alive"

    return "blocked"


DETECTORS = {
    "avito": classify_avito,
    "cian": classify_cian,
    "yandex": classify_yandex,
}

NOT_FOUND_CODES = {
    "cian": (404,),
}


def classify(source: str, status: int, body: str) -> str:
    detector = DETECTORS.get(source)

    if detector is None:
        return "error"

    if status in BLOCK_CODES:
        return "blocked"

    if status in NOT_FOUND_CODES.get(source, ()):
        return "not_found"

    if status != 200:
        return "error"

    return detector(body)
