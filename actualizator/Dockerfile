FROM python:3.11-slim

WORKDIR /app

COPY parser_avito/actualizator/requirements.txt /app/requirements.txt
COPY core/shared/python/pvz_common/requirements.txt /app/shared_requirements.txt
ARG PIP_INDEX_URL=https://pypi.org/simple
ENV PIP_INDEX_URL=${PIP_INDEX_URL}
RUN pip install --upgrade pip && pip install -r /app/shared_requirements.txt -r /app/requirements.txt

COPY core/shared/python/pvz_common /app/pvz_common
COPY parser_avito/actualizator /app/actualizator

ENTRYPOINT ["/bin/bash", "/app/actualizator/entrypoint.sh"]
