import os
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Base URL for generating API URLs in responses
    BASE_URL: str = "http://localhost:8000"

    # Directory for bare git repositories
    DATA_DIR: str = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")

    # SQLite database URL
    DATABASE_URL: str = ""
    SQLITE_BUSY_TIMEOUT_MS: int = 5000
    SQLITE_WRITE_RETRY_ATTEMPTS: int = 2
    SQLITE_WRITE_RETRY_DELAY_MS: int = 100

    # Secret key for JWT/session signing
    SECRET_KEY: str = "change-me-in-production"

    # Admin credentials (created on first startup)
    SEED_DATA: bool = True
    ADMIN_USERNAME: str = "admin"
    ADMIN_PASSWORD: str = "admin"
    DEFAULT_ADMIN_TOKEN: str = "ghp_admin_default_token"

    # Hostname for Caddy TLS / gh CLI integration
    HOSTNAME: str = "ghemu.local"

    # Where the upstream runner's `uses:` actions are resolved and fetched
    # from. The emulator does both on the runner's behalf: it resolves the
    # ref through the API and proxies the archive, so the runner needs no
    # route to github.com and never sends its job token there. The token is
    # optional and only raises the unauthenticated API rate limit.
    ACTIONS_UPSTREAM_API_URL: str = "https://api.github.com"
    ACTIONS_UPSTREAM_ARCHIVE_URL: str = "https://codeload.github.com"
    ACTIONS_UPSTREAM_TOKEN: str = ""

    # The memory watchdog (app/services/memory_watch.py). The process has
    # been OOM-killed at 1.5 GiB three times with nothing naming a cause; it
    # now reports in-flight requests and the largest allocation sites when
    # RSS crosses these thresholds or grows by the step, to the log and to
    # DATA_DIR/memory-watch.log. TRACE=0 keeps the sampler but drops the
    # allocation sites, which is the part that costs memory.
    MEMORY_WATCH: bool = True
    MEMORY_WATCH_TRACE: bool = True
    MEMORY_WATCH_INTERVAL_SECONDS: float = 2.0
    MEMORY_WATCH_THRESHOLDS_MIB: str = "512,768,1024,1280"
    MEMORY_WATCH_GROWTH_MIB: int = 200
    MEMORY_WATCH_FRAMES: int = 3

    # Resettable Actions OIDC issuer.  The key is generated in-process by the
    # emulator; this is intentionally not a production identity provider.
    OIDC_ISSUER: str = ""
    ACTIONS_OIDC_REQUEST_TOKEN: str = "fullsend-action-request"

    # Jobs assigned to a runner that stops heartbeating are returned to the
    # queue after this interval so a replacement runner can claim them.
    RUNNER_STALE_THRESHOLD_SECONDS: int = 120

    # Single resettable enterprise used for enterprise-scoped Actions runners.
    ENTERPRISE_SLUG: str = "breadboard"

    # GitHub App JWTs are accepted without signature verification by default
    # for emulator convenience. Set this to false for strict checks.
    APP_JWT_PERMISSIVE: bool = True

    # Server config
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    # SSH transport
    SSH_ENABLED: bool = True
    SSH_PORT: int = 2222
    SSH_HOST_KEY_PATH: str = ""

    model_config = {"env_prefix": "GITHUB_EMULATOR_"}

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if not self.DATABASE_URL:
            self.DATABASE_URL = f"sqlite+aiosqlite:///{self.DATA_DIR}/github_emulator.db"


settings = Settings()
