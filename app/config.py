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

    # Resettable Actions OIDC issuer.  The key is generated in-process by the
    # emulator; this is intentionally not a production identity provider.
    OIDC_ISSUER: str = ""
    ACTIONS_OIDC_REQUEST_TOKEN: str = "fullsend-action-request"

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
