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

    # Run mode: "webhook" (default, e.g. Render/Heroku/Koyeb/Railway with a
    # public HTTPS URL) or "polling" (e.g. Google Colab, local dev, any
    # environment without a stable public URL). Existing deployments that
    # don't set RUN_MODE keep working exactly as before, since "webhook"
    # is the default.
    run_mode: str

    # Webhook (only required when run_mode == "webhook")
    webhook_url: str | None  # full public URL, e.g. https://myapp.onrender.com
    webhook_path: str  # path segment, e.g. /webhook/<secret>
    webhook_secret: str | None  # used to validate incoming webhook requests

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
        """Full URL Telegram should POST updates to. Only valid in webhook mode."""
        return f"{(self.webhook_url or '').rstrip('/')}{self.webhook_path}"


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
    run_mode = os.environ.get("RUN_MODE", "webhook").strip().lower() or "webhook"
    if run_mode not in ("webhook", "polling"):
        raise ConfigError(f"RUN_MODE must be 'webhook' or 'polling', got: {run_mode!r}")

    if run_mode == "webhook":
        webhook_url = _require("WEBHOOK_URL")
        webhook_secret = _require("WEBHOOK_SECRET")
    else:
        # Polling mode never receives HTTP callbacks from Telegram, so these
        # aren't needed. Left as None rather than fake values so nothing
        # accidentally relies on a webhook URL that was never actually set.
        webhook_url = os.environ.get("WEBHOOK_URL", "").strip() or None
        webhook_secret = os.environ.get("WEBHOOK_SECRET", "").strip() or None

    return Config(
        bot_token=_require("BOT_TOKEN"),
        owner_id=_require_int("OWNER_ID"),
        run_mode=run_mode,
        webhook_url=webhook_url,
        webhook_path=os.environ.get("WEBHOOK_PATH", "/webhook").strip() or "/webhook",
        webhook_secret=webhook_secret,
        database_url=_require("DATABASE_URL"),
        port=_optional_int("PORT") or 10000,
        scratch_chat_id=_optional_int("SCRATCH_CHAT_ID"),
    )
