"""
Application configuration.

Loads and validates all required environment variables at startup.
Fails fast with a clear error if anything required is missing, rather than
surfacing cryptic errors later at runtime.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


@dataclass(frozen=True)
class Config:
    # Telegram
    bot_token: str
    owner_id: int

    # Webhook (Render Web Service)
    webhook_url: str  # full public URL, e.g. https://myapp.onrender.com
    webhook_path: str  # path segment, e.g. /webhook/<secret>
    webhook_secret: str  # used to validate incoming webhook requests

    # Database (Neon Postgres)
    database_url: str

    # Server
    port: int

    # Scratch chat used for forwardMessage-based caption reads (see architecture).
    # Must be a private chat/group/channel where the bot is admin, used only
    # as a throwaway destination for reading captions via forwardMessage.
    scratch_chat_id: int | None

    @property
    def full_webhook_url(self) -> str:
        """Full URL Telegram should POST updates to."""
        return f"{self.webhook_url.rstrip('/')}{self.webhook_path}"


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigError(f"Missing required environment variable: {name}")
    return value


def _require_int(name: str) -> int:
    raw = _require(name)
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"Environment variable {name} must be an integer, got: {raw!r}") from exc


def _optional_int(name: str) -> int | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"Environment variable {name} must be an integer, got: {raw!r}") from exc


def load_config() -> Config:
    """
    Load configuration from environment variables.

    Raises:
        ConfigError: if any required variable is missing or malformed.
    """
    return Config(
        bot_token=_require("BOT_TOKEN"),
        owner_id=_require_int("OWNER_ID"),
        webhook_url=_require("WEBHOOK_URL"),
        webhook_path=os.environ.get("WEBHOOK_PATH", "/webhook").strip() or "/webhook",
        webhook_secret=_require("WEBHOOK_SECRET"),
        database_url=_require("DATABASE_URL"),
        port=_optional_int("PORT") or 10000,
        scratch_chat_id=_optional_int("SCRATCH_CHAT_ID"),
    )
