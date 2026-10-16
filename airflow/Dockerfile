FROM apache/airflow:2.9.1

ARG AIRFLOW_VERSION=2.9.1

USER root

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    python3-dev \
    build-essential \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

USER airflow

RUN PYTHON_VERSION="$(python -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')" && \
    pip install --no-cache-dir \
      --constraint "https://raw.githubusercontent.com/apache/airflow/constraints-${AIRFLOW_VERSION}/constraints-${PYTHON_VERSION}.txt" \
      rapidfuzz \
      pandas \
      openpyxl \
      requests \
      python-dotenv \
      scrapy \
      itemadapter \
      scrapy-rotating-proxies \
      scrapeops-scrapy-proxy-sdk \
      pytest \
      pytest-mock \
    && pip uninstall -y typing || true