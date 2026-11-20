FROM node:22-slim AS frontend-build

WORKDIR /frontend
COPY src/frontend/package.json src/frontend/package-lock.json ./
RUN npm ci
COPY src/frontend/ ./
RUN npm run build

FROM python:3.12-slim

# Install git, supervisor, and Caddy
# Caddy from a pinned GitHub release, checked against the SHA-512 its
# release publishes (caddy_<version>_checksums.txt).
ARG CADDY_VERSION=2.11.7
ARG CADDY_SHA512=a7a433a1b133efc3c8d10eb0b99d52a24b5ef5c322dc77f5282182b1c0402139ab83f3a99f0c52409df77d20123fb0b523edad8a66d8f5e49136197bf61ef0e7
RUN apt-get update && \
    apt-get install -y --no-install-recommends git supervisor curl ca-certificates && \
    curl -fsSL -o /tmp/caddy.tar.gz "https://github.com/caddyserver/caddy/releases/download/v${CADDY_VERSION}/caddy_${CADDY_VERSION}_linux_amd64.tar.gz" && \
    echo "${CADDY_SHA512}  /tmp/caddy.tar.gz" | sha512sum -c - && \
    tar -xzf /tmp/caddy.tar.gz -C /usr/local/bin caddy && \
    chmod +x /usr/local/bin/caddy && \
    rm -rf /tmp/caddy.tar.gz /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Install the locked dependencies, every package checked against the hash
# in uv.lock. uv only exports the lock; pip installs. supervisord comes from
# the Debian package above.
COPY --from=ghcr.io/astral-sh/uv:0.12.4@sha256:d0a6eca6c669dc7e9c51218707b8438a3d30402733d739dcc00adb3e213e8f5c /uv /bin/uv
COPY pyproject.toml uv.lock ./
RUN uv export --frozen --no-dev --no-emit-project --format requirements-txt -o /tmp/requirements.txt && \
    pip install --no-cache-dir --require-hashes -r /tmp/requirements.txt && \
    rm /tmp/requirements.txt

# Copy application code
COPY . .
COPY --from=frontend-build /frontend/dist /app/src/frontend/dist

# Copy supervisor and Caddy configs
COPY supervisord.conf /etc/supervisor/conf.d/github-emulator.conf
COPY Caddyfile /etc/caddy/Caddyfile

# Create data directory
RUN mkdir -p /data

# Environment variables
ENV GITHUB_EMULATOR_DATA_DIR=/data
ENV GITHUB_EMULATOR_DATABASE_URL=sqlite+aiosqlite:////data/github_emulator.db
ENV GITHUB_EMULATOR_BASE_URL=https://ghemu.local
ENV GITHUB_EMULATOR_HOSTNAME=ghemu.local
ENV PYTHONPATH=/app/src

# Expose ports (HTTPS + HTTP + direct API + SSH)
EXPOSE 443
EXPOSE 80
EXPOSE 8000
EXPOSE 2222

# Health check (internal, bypasses Caddy)
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://127.0.0.1:8000/api/v3').raise_for_status()" || exit 1

# Run with supervisor
CMD ["supervisord", "-n", "-c", "/etc/supervisor/conf.d/github-emulator.conf"]
