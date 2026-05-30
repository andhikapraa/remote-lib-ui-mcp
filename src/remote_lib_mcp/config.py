"""Runtime configuration via pydantic-settings.

All settings are read from ``REMOTE_LIB_*`` environment variables (and an
optional ``.env`` file). Secrets are wrapped in ``SecretStr`` so they never
render in logs, ``repr``, or tracebacks.
"""

from __future__ import annotations

import os

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


class Config(BaseSettings):
    """Validated, frozen configuration sourced from the environment."""

    model_config = SettingsConfigDict(
        env_prefix="REMOTE_LIB_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    # credentials
    username: str | None = None
    password: SecretStr | None = None
    openalex_api_key: SecretStr | None = None

    # endpoints
    base_url: str = "https://remote-lib.ui.ac.id"
    sso_url: str = "https://sso.ui.ac.id"

    # http tuning
    http_timeout: float = 45.0
    http_connect_timeout: float = 15.0
    http2: bool = True
    max_connections: int = 20
    max_keepalive_connections: int = 10
    verify_ssl: bool = True
    user_agent: str = DEFAULT_USER_AGENT

    # session persistence
    session_file: str | None = None

    # browser fallback
    browser_headless: bool = True
    browser_humanize: bool = True
    browser_wait_ms: int = 3000

    # comma-separated names/slugs to switch off by default
    disabled: str = ""

    @field_validator("base_url", "sso_url")
    @classmethod
    def _check_url(cls, v: str) -> str:
        v = v.rstrip("/")
        if not v.startswith(("http://", "https://")):
            raise ValueError(f"must be an http(s) URL, got {v!r}")
        return v

    @field_validator("http_timeout", "http_connect_timeout")
    @classmethod
    def _positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("must be > 0")
        return v

    @field_validator("session_file")
    @classmethod
    def _expand(cls, v: str | None) -> str | None:
        return os.path.expanduser(v) if v else v

    # ----- convenience ----------------------------------------------------- #
    @classmethod
    def from_env(cls) -> Config:
        return cls()

    @property
    def has_credentials(self) -> bool:
        return bool(self.username and self.password)

    @property
    def disabled_resources(self) -> tuple[str, ...]:
        return tuple(s.strip() for s in self.disabled.split(",") if s.strip())
