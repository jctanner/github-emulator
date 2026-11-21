# Ubuntu 24.04 by default, or 26.04 with --build-arg UBUNTU_VERSION=26.04:
# the two LTS releases GitHub hosts. Fullsend asks for 24.04 (DefaultGHRunner
# is "ubuntu-24.04" and its reusable dispatch defaults runner_image to the
# same), while ubuntu-latest is now 26.04. This image is the stack's stand-in
# for a GitHub-hosted runner, so its label list has to describe the release
# it was built on.
ARG UBUNTU_VERSION=24.04
FROM ubuntu:${UBUNTU_VERSION}

ARG RUNNER_VERSION=2.317.0

# ICU's package name carries its version: libicu74 on 24.04, libicu78 on
# 26.04. Any other base stops here rather than building without ICU.
RUN . /etc/os-release && \
    case "$VERSION_ID" in \
        24.04) icu=libicu74 ;; \
        26.04) icu=libicu78 ;; \
        *) echo "no ICU package known for Ubuntu $VERSION_ID" >&2; exit 1 ;; \
    esac && \
    apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        git \
        jq \
        "$icu" \
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
