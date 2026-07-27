# Теги: https://hub.docker.com/r/hiyouga/pytorch/tags
ARG BASE_IMAGE=hiyouga/pytorch:th2.6.0-cu124-flashattn2.7.4-cxx11abi0-devel
FROM ${BASE_IMAGE}

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_ROOT_USER_ACTION=ignore \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY tools/LLaMA-Factory /app/tools/LLaMA-Factory
RUN pip install --no-cache-dir --upgrade pip wheel setuptools && \
    pip install --no-cache-dir -e /app/tools/LLaMA-Factory && \
    pip install --no-cache-dir \
      "bitsandbytes>=0.39.0" \
      "timm>=1.0.0" \
      "fastapi>=0.115.0" \
      "uvicorn[standard]>=0.30.0" \
      "python-multipart>=0.0.9" \
      "pydantic>=2.8.0" \
      "pymupdf>=1.24.0" \
      "pillow>=10.0.0" \
      "httpx>=0.27.0"

EXPOSE 8765
