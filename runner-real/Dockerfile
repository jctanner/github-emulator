FROM ubuntu:22.04

ARG RUNNER_VERSION=2.317.0

RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        git \
        jq \
        libicu70 \
        python3 \
        tar \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --shell /bin/bash runner

WORKDIR /home/runner/actions-runner

RUN curl -fsSL \
        "https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz" \
        -o /tmp/actions-runner.tar.gz \
    && tar -xzf /tmp/actions-runner.tar.gz \
    && rm /tmp/actions-runner.tar.gz \
    && ./bin/installdependencies.sh \
    && chown -R runner:runner /home/runner/actions-runner

COPY entrypoint.sh /entrypoint.sh
COPY port80_proxy.py /port80_proxy.py

USER runner

ENTRYPOINT ["/entrypoint.sh"]
