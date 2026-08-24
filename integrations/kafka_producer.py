import json
import os
import re

from kafka import KafkaProducer
from loguru import logger

from models import Item


_AREA_UNIT = r'(?:м²|м2|m2|кв\.?\s*м\.?|кв\.?\s*метр\w*|м\.?\s*кв\.?|квадратн\w*\s*метр\w*)'
_AREA_RE = re.compile(r'(\d+(?:[.,]\d+)?)\s*' + _AREA_UNIT, re.IGNORECASE)
_AREA_LABELED_RE = re.compile(r'площад\w*\W{0,12}(\d+(?:[.,]\d+)?)\s*' + _AREA_UNIT, re.IGNORECASE)
_AREA_NEAR_OBJECT_RE = re.compile(
    r'(?:помещени\w*|склад\w*|офис\w*|павильон\w*|бокс\w*|ангар\w*|сда[её]тся|аренд\w*)'
    r'[^.;\n]{0,40}?(\d+(?:[.,]\d+)?)\s*' + _AREA_UNIT,
    re.IGNORECASE,
)

_PER_M2_MARKER = 'за м²'
_EXPONENT_RE = re.compile(r'([\d][\d\s\u00a0]*(?:[.,]\d+)?)\s*(тыс|млн|млрд)', re.IGNORECASE)
_MONEY_RE = re.compile(r'([\d][\d\s\u00a0]*(?:[.,]\d+)?)')

_AREA_MIN = 5.0
_AREA_MAX = 10_000.0


def _to_number(raw: str) -> float | None:
    cleaned = raw.replace('\u00a0', '').replace(' ', '').replace(',', '.')
    try:
        return float(cleaned)
    except ValueError:
        return None


def _parse_exponent(exponent: str | None) -> int | None:
    if not exponent:
        return None

    match = _EXPONENT_RE.search(exponent)
    if not match:
        return None

    num = _to_number(match.group(1))
    if num is None:
        return None

    unit = match.group(2).lower()
    multiplier = 1_000 if unit == 'тыс' else 1_000_000 if unit == 'млн' else 1_000_000_000

    return round(num * multiplier)


def _parse_money(text: str | None) -> int | None:
    if not text:
        return None

    match = _MONEY_RE.search(text)
    if not match:
        return None

    num = _to_number(match.group(1))

    return round(num) if num else None


def _price_pair(ad) -> tuple[float | None, int | None]:
    pd = ad.priceDetailed if ad else None

    if not pd or not pd.value:
        return None, None

    if _PER_M2_MARKER in (pd.postfix or ''):
        return float(pd.value), _parse_exponent(pd.exponent)

    normalized = ad.normalizedPrice or ''
    if _PER_M2_MARKER in normalized:
        per_m2 = _parse_money(normalized)
        return (float(per_m2) if per_m2 else None), int(pd.value)

    return None, int(pd.value)


def _area_from_price(ad) -> float | None:
    per_m2, total = _price_pair(ad)

    if not per_m2 or not total:
        return None

    area = total / per_m2

    return round(area, 1) if _AREA_MIN <= area <= _AREA_MAX else None


def _sane_area(value: str) -> float | None:
    area = _to_number(value)

    if area is None or not (_AREA_MIN <= area <= _AREA_MAX):
        return None

    return area


def _area_from_text(title: str | None, description: str | None) -> float | None:
    if title:
        match = _AREA_RE.search(title)
        if match:
            area = _sane_area(match.group(1))
            if area is not None:
                return area

    if not description:
        return None

    for pattern in (_AREA_LABELED_RE, _AREA_NEAR_OBJECT_RE):
        match = pattern.search(description)
        if match:
            area = _sane_area(match.group(1))
            if area is not None:
                return area

    return None


def _extract_area(ad, description: str | None = None) -> float | None:
    from_price = _area_from_price(ad)

    if from_price is not None:
        return from_price

    return _area_from_text(ad.title if ad else None, description)


def _extract_description(ad) -> str | None:
    if ad.description:
        return ad.description

    steps = (ad.iva or {}).get('DescriptionStep') or []

    for step in steps:
        for payload in (step.payload, step.componentData.payload if step.componentData else None):
            text = (payload or {}).get('description')
            if text:
                return text

    return None


def _extract_total_price(pd) -> int | None:
    if not pd or not pd.value:
        return None

    if _PER_M2_MARKER not in (pd.postfix or ''):
        return pd.value

    return _parse_exponent(pd.exponent)


class KafkaListingProducer:
    def __init__(self):
        self._bootstrap = os.environ.get('KAFKA_BOOTSTRAP_SERVERS', '')
        self._topic = os.environ.get('KAFKA_TOPIC', 'listings.new')
        self._city = os.environ.get('CITY', 'spb')
        self._producer: KafkaProducer | None = None

    def enabled(self) -> bool:
        return bool(self._bootstrap)

    def _get_producer(self) -> KafkaProducer:
        if self._producer is None:
            self._producer = KafkaProducer(
                bootstrap_servers=self._bootstrap,
                value_serializer=lambda v: json.dumps(v, ensure_ascii=False).encode('utf-8'),
                request_timeout_ms=10000,
                retries=3,
            )
        return self._producer

    def publish(self, ad: Item, deal_type: str = 'rent') -> bool:
        if not self.enabled():
            return False
        if not ad.coords or 'lat' not in ad.coords or 'lng' not in ad.coords:
            return False

        if deal_type not in ('rent', 'sale'):
            deal_type = 'rent'

        clean_path = ad.urlPath.split('?')[0]
        description = _extract_description(ad)
        message = {
            'source':    'avito',
            'deal_type': deal_type,
            'avito_url': f'https://www.avito.ru{clean_path}',
            'city':      self._city,
            'address': (
                ad.coords.get('address_user')
                or (ad.geo.formattedAddress if ad.geo else '')
                or ''
            ),
            'lat': float(ad.coords['lat']),
            'lon': float(ad.coords['lng']),
            'title':        ad.title,
            'description':  description,
            'published_at': ad.sortTimeStamp,
            'seller_id':    ad.sellerId,
            'seller_url':   f'https://www.avito.ru/brands/{ad.sellerId}' if ad.sellerId else None,
            'is_promotion': ad.isPromotion,
        }

        price = _extract_total_price(ad.priceDetailed)
        if price is not None:
            message['price'] = price

        area = _extract_area(ad, description)
        if area is not None:
            message['area'] = area

        try:
            self._get_producer().send(
                self._topic,
                key=str(ad.id).encode(),
                value=message,
            )
            self._get_producer().flush(timeout=5)
            logger.info(f"Kafka: published {ad.id} | deal_type={deal_type} | {message['address']}")
            return True
        except Exception as err:
            logger.error(f"Kafka: failed to publish {ad.id}: {err}")
            return False
