"""
Keep Alive background service.

Owns a single background asyncio.Task that periodically pings the bot's own
`/health` endpoint (see main.py's `_health_check`) to keep the Render free
Web Service instance from idling out. Fully independent of JobManager/
JobRunner -- for Task Protection mode it only ever READS
`bot.job_manager.is_job_active`, never touches job execution.

Three modes (see db.queries.get_keep_alive_config / set_keep_alive_config):
  - "manual"          : no background loop. Manual "Ping Now" still works
                         via bot/handlers/keep_alive.py, unrelated to this
                         class.
  - "auto"            : background loop pings every `interval_seconds`,
                         regardless of job state.
  - "task_protection" : background loop pings every `interval_seconds`,
                         but ONLY while bot.job_manager.is_job_active is
                         True. When idle, the loop sleeps in short steps and
                         checks job state again rather than pinging.

Only one loop task can exist at a time. `reconfigure()` is the single entry
point for mode/interval changes -- it always cancels any existing task
before optionally starting a new one, so callers (settings handlers,
startup) never need to manage the task directly.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import aiohttp
from aiogram import Bot

logger = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")

MODE_MANUAL = "manual"
MODE_AUTO = "auto"
MODE_TASK_PROTECTION = "task_protection"

VALID_MODES = (MODE_MANUAL, MODE_AUTO, MODE_TASK_PROTECTION)
VALID_INTERVALS_SECONDS = (300, 420, 540, 600, 720, 900)  # 5/7/9/10/12/15 min

_PING_TIMEOUT_SECONDS = 15
# When idling in task_protection mode (no active job), re-check job state at
# this cadence rather than sleeping for the full interval in one shot -- so
# protection engages promptly once a job starts.
_IDLE_POLL_SECONDS = 10


def _now_ist_label() -> str:
    return datetime.now(IST).strftime("%I:%M %p IST")


class KeepAliveManager:
    """Independent Keep Alive background service. See module docstring."""

    def __init__(self, bot: Bot) -> None:
        self._bot = bot
        self._mode: str = MODE_MANUAL
        self._interval_seconds: int = 540
        self._task: asyncio.Task | None = None

        # In-memory status, surfaced by bot/handlers/keep_alive.py. Mirrors
        # the existing manual-ping status pattern (module-level, resets on
        # restart -- acceptable for a diagnostic display).
        self.last_ping_time: str | None = None
        self.last_ping_status: str | None = None
        self.last_ping_error: str | None = None

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def interval_seconds(self) -> int:
        return self._interval_seconds

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    def current_task_label(self) -> str:
        """Best-effort human label for the active job, or 'None' if idle."""
        job_manager = getattr(self._bot, "job_manager", None)
        if job_manager is None or not job_manager.is_job_active:
            return "None"
        # TaskType isn't known synchronously without a DB read; JobManager
        # only exposes is_job_active as a sync property. A generic label is
        # sufficient here without adding new reads into JobManager.
        return "Active Job"

    async def start(self, mode: str, interval_seconds: int) -> None:
        """Initial startup call -- loads saved config and starts the loop if required."""
        await self.reconfigure(mode, interval_seconds)

    async def reconfigure(self, mode: str, interval_seconds: int) -> None:
        """
        Apply a new mode/interval. Always safe to call, including with the
        same values as currently set. Cancels any existing loop first, so
        there is never more than one background task alive at once.
        """
        if mode not in VALID_MODES:
            raise ValueError(f"Invalid keep-alive mode: {mode!r}")

        await self._stop_task()

        self._mode = mode
        self._interval_seconds = interval_seconds

        if mode != MODE_MANUAL:
            self._task = asyncio.create_task(self._loop())
            logger.info("KeepAliveManager: started loop (mode=%s, interval=%ss)", mode, interval_seconds)
        else:
            logger.info("KeepAliveManager: manual mode, no background loop.")

    async def shutdown(self) -> None:
        """Call once during application shutdown."""
        await self._stop_task()

    async def _stop_task(self) -> None:
        if self._task is None:
            return
        task = self._task
        self._task = None
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("KeepAliveManager: error while cancelling background task.")

    async def _loop(self) -> None:
        """
        Background loop body. Wrapped so an unexpected error in a single
        iteration can never kill the whole loop -- only CancelledError
        (from _stop_task) is allowed to actually exit it.
        """
        try:
            while True:
                if self._mode == MODE_TASK_PROTECTION:
                    job_manager = getattr(self._bot, "job_manager", None)
                    active = job_manager is not None and job_manager.is_job_active
                    if not active:
                        await asyncio.sleep(_IDLE_POLL_SECONDS)
                        continue

                await self._ping_once()
                await asyncio.sleep(self._interval_seconds)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Should be unreachable given the try/except inside _ping_once,
            # but guarantees the loop task itself can never crash silently.
            logger.exception("KeepAliveManager: unexpected error in background loop.")

    async def _ping_once(self) -> None:
        """
        Perform a single health ping. All failures are caught and recorded
        as status-only information -- never propagated, never affects
        Caption Manager / Post Manager / JobRunner.
        """
        webhook_url = getattr(self._bot, "webhook_url", None)
        if not webhook_url:
            return
        health_url = f"{webhook_url.rstrip('/')}/health"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    health_url, timeout=aiohttp.ClientTimeout(total=_PING_TIMEOUT_SECONDS)
                ) as response:
                    status_code = response.status
                    status_text = response.reason or ""

            self.last_ping_time = _now_ist_label()
            self.last_ping_status = f"{status_code} {status_text}".strip()
            self.last_ping_error = None

        except Exception as exc:
            self.last_ping_time = _now_ist_label()
            self.last_ping_status = "Failed"
            self.last_ping_error = str(exc)
            logger.warning("KeepAliveManager: automatic ping failed: %s", exc)
