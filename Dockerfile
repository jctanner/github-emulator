FROM python:3.12-slim

# Install git, supervisor, and Caddy
RUN apt-get update && \
    apt-get install -y --no-install-recommends git supervisor curl && \
    curl -sL "https://caddyserver.com/api/download?os=linux&arch=amd64" -o /usr/local/bin/caddy && \
    chmod +x /usr/local/bin/caddy && \
    rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Copy supervisor and Caddy configs
COPY supervisord.conf /etc/supervisor/conf.d/github-emulator.conf
COPY Caddyfile /etc/caddy/Caddyfile

# Create data directory
RUN mkdir -p /data

# Environment variables
ENV GITHUB_EMULATOR_DATA_DIR=/data
ENV GITHUB_EMULATOR_DATABASE_URL=sqlite+aiosqlite:///data/github_emulator.db
ENV GITHUB_EMULATOR_BASE_URL=https://ghemu.local
ENV GITHUB_EMULATOR_HOSTNAME=ghemu.local
ENV PYTHONPATH=/app

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
