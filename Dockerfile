FROM python:3.11-slim
LABEL org.opencontainers.image.source=https://github.com/Duff89/parser_avito

RUN apt-get update && apt-get install \
-y --ignore-missing --no-install-recommends --no-install-suggests \
	libatk-bridge2.0-0t64 \
	libatk1.0-0t64 \
	libatspi2.0-0t64 \
	libcairo2 \
	libdbus-1-3 \
	libdrm2 \
	libgbm1 \
	libglib2.0-0t64 \
	libnspr4 \
	libnss3 \
	libpango-1.0-0 \
	libxcomposite1 \
	libxdamage1 \
	libxfixes3 \
	libxrandr2 \
	libxkbcommon0 \
	libasound2 \
	&& apt-get autopurge \
	&& apt-get clean \
	&& apt-get distclean

WORKDIR /app

COPY parser_avito/requirements.txt /app/requirements.txt
COPY core/shared/python/pvz_common/requirements.txt /app/shared_requirements.txt
ARG PIP_INDEX_URL=https://pypi.org/simple
ENV PIP_INDEX_URL=${PIP_INDEX_URL}
RUN pip install --upgrade pip && pip install -r /app/shared_requirements.txt -r /app/requirements.txt

RUN python -m playwright install chromium-headless-shell

COPY core/shared/python/pvz_common /app/pvz_common
COPY parser_avito /app
COPY parser_avito/entrypoint.sh /entrypoint.sh

ENTRYPOINT ["/bin/bash", "/entrypoint.sh"]
