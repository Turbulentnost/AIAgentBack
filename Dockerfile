FROM python:3.11-slim

WORKDIR /app

# PyPI: при таймаутах pypi.org используйте зеркало через build-arg / .env (см. .env.example).
# Проверенные альтернативы: Aliyun, Tsinghua; pypi.org — как extra-index.
ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
ARG PIP_EXTRA_INDEX_URL=https://pypi.org/simple
ARG PIP_DEFAULT_TIMEOUT=120

RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-rus \
    tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY src ./src
COPY scripts ./scripts
COPY alembic ./alembic
COPY alembic.ini ./

ENV PIP_DEFAULT_TIMEOUT=${PIP_DEFAULT_TIMEOUT}
ENV PIP_DISABLE_PIP_VERSION_CHECK=1

RUN printf '%s\n' \
    '[global]' \
    "index-url = ${PIP_INDEX_URL}" \
    "extra-index-url = ${PIP_EXTRA_INDEX_URL}" \
    "timeout = ${PIP_DEFAULT_TIMEOUT}" \
    'trusted-host = mirrors.aliyun.com' \
    '               pypi.tuna.tsinghua.edu.cn' \
    '               pypi.org' \
    '               mirror.yandex.ru' \
    > /etc/pip.conf \
    && pip install --no-cache-dir -e ".[api,documents]"

ENV PYTHONPATH=/app/src
ENV PYTHONUNBUFFERED=1

CMD ["python", "scripts/run_api.py"]
