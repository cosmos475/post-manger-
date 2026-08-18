"""
Application entrypoint.

Runs as a Render Free Web Service: a single aiohttp web server bound to
$PORT, receiving Telegram updates via webhook POST. No separate worker
process -- job processing runs as an in-process asyncio.Task, owned by
JobManager (see core/job_manager.py), inside this same process.
"""

from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

from bot.dispatcher import create_dispatcher
from config import ConfigError, load_config
from core.job_manager import JobManager
from core.keep_alive_manager import KeepAliveManager
from db.connection import close_pool, get_pool, init_pool
from db.queries import get_keep_alive_config, init_schema

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger(__name__)


async def _on_startup(app: web.Application) -> None:
    config = app["config"]
    bot: Bot = app["bot"]

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
    bot: Bot = app["bot"]
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

    app.on_startup.append(_on_startup)
    app.on_shutdown.append(_on_shutdown)

    return app


def main() -> None:
    app = create_app()
    config = app["config"]
    web.run_app(app, host="0.0.0.0", port=config.port)


if __name__ == "__main__":
    main()
