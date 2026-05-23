import json
import os
import re

from kafka import KafkaProducer
from loguru import logger

from models import Item


def _extract_area(title: str | None) -> float | None:
    if not title:
        return None
    match = re.search(r'(\d+[.,]?\d*)\s*м[²2]', title)
    if not match:
        return None
    return float(match.group(1).replace(',', '.'))


def _extract_total_price(pd) -> int | None:
    if not pd or not pd.value:
        return None

    if 'за м²' not in (pd.postfix or ''):
        return pd.value

    exponent = pd.exponent or ''
    match = re.match(r'([\d]+(?:[.,][\d]+)?)\s*(тыс|млн)', exponent.strip())
    if not match:
        return None
    num = float(match.group(1).replace(',', '.'))
    multiplier = 1_000 if 'тыс' in match.group(2) else 1_000_000
    return round(num * multiplier)


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

    def publish(self, ad: Item) -> bool:
        if not self.enabled():
            return False
        if not ad.coords or 'lat' not in ad.coords or 'lng' not in ad.coords:
            return False

        clean_path = ad.urlPath.split('?')[0]
        message = {
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
            'description':  ad.description,
            'published_at': ad.sortTimeStamp,
            'seller_id':    ad.sellerId,
            'is_promotion': ad.isPromotion,
        }

        price = _extract_total_price(ad.priceDetailed)
        if price is not None:
            message['price'] = price

        area = _extract_area(ad.title)
        if area is not None:
            message['area'] = area

        try:
            self._get_producer().send(
                self._topic,
                key=str(ad.id).encode(),
                value=message,
            )
            self._get_producer().flush(timeout=5)
            logger.info(f"Kafka: published {ad.id} | {message['address']}")
            return True
        except Exception as err:
            logger.error(f"Kafka: failed to publish {ad.id}: {err}")
            return False
