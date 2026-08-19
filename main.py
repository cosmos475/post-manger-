"""
Application entrypoint.

Two run modes, controlled by the optional RUN_MODE env var:

  RUN_MODE=webhook (default, unset = this)
      Runs as a Render/Heroku/Koyeb/Railway Web Service: a single aiohttp
      web server bound to $PORT, receiving Telegram updates via webhook
      POST. No separate worker process -- job processing runs as an
      in-process asyncio.Task, owned by JobManager (see
      core/job_manager.py), inside this same process.

  RUN_MODE=polling
      For environments without a stable public HTTPS URL (Google Colab,
      local development, etc.). No web server or webhook is set up;
      instead aiogram's long-polling loop pulls updates directly. Job
      processing setup is identical either way.

Existing deployments that don't set RUN_MODE are completely unaffected --
"webhook" remains the default and that code path is unchanged.
"""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

from bot.dispatcher import create_dispatcher
from config import Config, ConfigError, load_config
from core.job_manager import JobManager
from core.keep_alive_manager import KeepAliveManager
from db.connection import close_pool, get_pool, init_pool
from db.queries import get_keep_alive_config, init_schema

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger(__name__)


async def _startup(bot: Bot, config: Config) -> None:
    """Shared startup logic for both webhook and polling modes."""
    pool = await init_pool(config.database_url)
    await init_schema(pool)
    logger.info("Database schema initialized.")

    if config.scratch_chat_id is None:
        logger.warning(
            "SCRATCH_CHAT_ID is not set. Preview and job runs will fail until it is "
            "configured -- see .env.example for setup instructions."
        )

    job_manager = JobManager(pool=pool, bot=bot, scratch_chat_id=config.scratch_chat_id or 0)
    bot.job_manager = job_manager
    bot.scratch_chat_id = config.scratch_chat_id
    bot.webhook_url = config.webhook_url

    recovered = await job_manager.recover_on_startup()
    if recovered is not None:
        logger.info("Startup recovery: job %s status=%s", recovered.id, recovered.status.value)

    keep_alive_manager = KeepAliveManager(bot=bot)
    bot.keep_alive_manager = keep_alive_manager
    bot.db_pool = pool
    saved_mode, saved_interval = await get_keep_alive_config(pool)
    await keep_alive_manager.start(saved_mode, saved_interval)
    logger.info("Keep Alive started (mode=%s, interval=%ss)", saved_mode, saved_interval)


async def _shutdown(bot: Bot) -> None:
    """Shared shutdown logic for both webhook and polling modes."""
    keep_alive_manager: KeepAliveManager | None = getattr(bot, "keep_alive_manager", None)
    if keep_alive_manager is not None:
        await keep_alive_manager.shutdown()
    await bot.session.close()
    await close_pool()
    logger.info("Shutdown complete.")


async def _health_check(request: web.Request) -> web.Response:
    """
    Simple health-check endpoint. Render's Web Service infrastructure and
    any external uptime pinger can hit this to confirm the process is alive.
    Deliberately does not touch the DB or Telegram, so it stays fast and
    cannot itself become a failure point.
    """
    return web.Response(text="Bot is alive")


def create_app() -> web.Application:
    """Webhook-mode aiohttp application (unchanged behavior)."""
    try:
        config = load_config()
    except ConfigError as exc:
        logger.critical("Configuration error: %s", exc)
        raise

    bot = Bot(
        token=config.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dispatcher = create_dispatcher(owner_id=config.owner_id)

    app = web.Application()
    app["config"] = config
    app["bot"] = bot
    app["dispatcher"] = dispatcher

    app.router.add_get("/health", _health_check)
    app.router.add_get("/", _health_check)

    webhook_handler = SimpleRequestHandler(
        dispatcher=dispatcher,
        bot=bot,
        secret_token=config.webhook_secret,
    )
    webhook_handler.register(app, path=config.webhook_path)

    setup_application(app, dispatcher, bot=bot)

    async def _on_startup(app: web.Application) -> None:
        await _startup(bot, config)
        webhook_info = await bot.get_webhook_info()
        if webhook_info.url != config.full_webhook_url:
            await bot.set_webhook(
                url=config.full_webhook_url,
                secret_token=config.webhook_secret,
                drop_pending_updates=False,
            )
            logger.info("Webhook set to %s", config.full_webhook_url)
        else:
            logger.info("Webhook already correctly set to %s", config.full_webhook_url)

    async def _on_shutdown(app: web.Application) -> None:
        await _shutdown(bot)

    app.on_startup.append(_on_startup)
    app.on_shutdown.append(_on_shutdown)

    return app


async def run_polling() -> None:
    """
    Polling-mode entrypoint. No HTTP server, no webhook -- just aiogram's
    long-polling loop. Used for environments without a stable public URL
    (Colab, local development). Any existing webhook is removed first, since
    Telegram doesn't allow both webhook and polling active at once.
    """
    try:
        config = load_config()
    except ConfigError as exc:
        logger.critical("Configuration error: %s", exc)
        raise

    bot = Bot(
        token=config.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dispatcher = create_dispatcher(owner_id=config.owner_id)

    await bot.delete_webhook(drop_pending_updates=False)
    logger.info("Any existing webhook removed -- starting long polling.")

    await _startup(bot, config)
    try:
        await dispatcher.start_polling(bot)
    finally:
        await _shutdown(bot)


def main() -> None:
    run_mode = load_config().run_mode

    if run_mode == "polling":
        asyncio.run(run_polling())
        return

    app = create_app()
    config = app["config"]
    web.run_app(app, host="0.0.0.0", port=config.port)


if __name__ == "__main__":
    main()
