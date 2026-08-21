FROM python:3.12-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends ca-certificates curl git jq && \
    rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir httpx pyyaml

COPY runner.py /app/runner.py

WORKDIR /app

ENTRYPOINT ["python", "runner.py"]
