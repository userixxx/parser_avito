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


class KafkaListingProducer:
    def __init__(self):
        self._bootstrap = os.environ.get('KAFKA_BOOTSTRAP_SERVERS', '')
        self._topic = os.environ.get('KAFKA_TOPIC', 'listings.new')
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
        if ad.priceDetailed and ad.priceDetailed.value:
            message['price'] = ad.priceDetailed.value

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
