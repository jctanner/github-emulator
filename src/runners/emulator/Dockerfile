# Ubuntu 24.04 because that is what the workflows this runner serves are
# written for: Fullsend's DefaultGHRunner is "ubuntu-24.04" and every job in
# its reusable dispatch runs on that image by default. This is not a faithful
# hosted image - it is runner.py, a compatibility layer, on the same base -
# but a job that assumes the hosted distribution finds the same one here.
FROM ubuntu:24.04

# python-is-python3 keeps `python` valid: the deployments exec
# `python /app/runner.py`, and Ubuntu ships only `python3`.
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ca-certificates curl git jq \
        python3 python3-pip python-is-python3 && \
    rm -rf /var/lib/apt/lists/*

# System-wide on purpose: the runner is the only Python program in this
# image, and images built from it install their own pins the same way.
RUN python3 -m pip install --no-cache-dir --break-system-packages httpx pyyaml

COPY runner.py /app/runner.py

WORKDIR /app

ENTRYPOINT ["python3", "runner.py"]
