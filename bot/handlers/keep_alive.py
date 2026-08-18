"""
Keep Alive (owner only): Manual Ping + configurable Auto / Task Protection
background pinging.

Manual Ping sends an async HTTP GET to WEBHOOK_URL + "/health" (the existing
health endpoint already registered in main.py) on demand, and shows the last
ping result -- this behavior is unchanged from before. Fully isolated: no
DB, no job pipeline, no webhook handling changes. Manual ping's last-ping
result is kept in-memory only (module-level state) -- resets on restart,
acceptable for a manual diagnostic feature.

Auto / Task Protection modes are handled by core.keep_alive_manager
.KeepAliveManager (bot.keep_alive_manager), which owns its own background
loop and its own last-ping status. This module only reads that manager's
state for display and calls reconfigure() on mode/interval changes -- it
never touches JobManager/JobRunner directly.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import aiohttp
from aiogram import F, Router
from aiogram.types import CallbackQuery

from bot import keyboards
from db import queries

router = Router(name="keep_alive")

IST = ZoneInfo("Asia/Kolkata")

# In-memory last-ping state for MANUAL ping only (not persisted -- see
# module docstring). Kept separate from KeepAliveManager's own status so
# Manual Ping's existing behavior is untouched.
_last_ping_time: str | None = None
_last_ping_status: str | None = None


def _now_ist_label() -> str:
    return datetime.now(IST).strftime("%I:%M %p IST")


@router.callback_query(F.data == "menu:keep_alive")
async def cb_open_keep_alive(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        "🟢 Keep Alive",
        reply_markup=keyboards.keep_alive_menu(),
    )
    await callback.answer()


@router.callback_query(F.data == "ka:settings")
async def cb_open_settings(callback: CallbackQuery) -> None:
    manager = callback.bot.keep_alive_manager
    await callback.message.edit_text(
        "🟢 Keep Alive Settings\n\n"
        "Choose a mode, then an interval (used by Auto and Task Protection):",
        reply_markup=keyboards.keep_alive_settings_menu(manager.mode, manager.interval_seconds),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("ka:mode:"))
async def cb_set_mode(callback: CallbackQuery) -> None:
    mode = callback.data.split(":", 2)[2]
    manager = callback.bot.keep_alive_manager
    pool = callback.bot.db_pool

    await manager.reconfigure(mode, manager.interval_seconds)
    await queries.set_keep_alive_config(pool, mode, manager.interval_seconds)

    await callback.message.edit_text(
        "🟢 Keep Alive Settings\n\n"
        "Choose a mode, then an interval (used by Auto and Task Protection):",
        reply_markup=keyboards.keep_alive_settings_menu(manager.mode, manager.interval_seconds),
    )
    await callback.answer(f"Mode set to {keyboards.KEEP_ALIVE_MODE_LABELS[mode]}")


@router.callback_query(F.data.startswith("ka:interval:"))
async def cb_set_interval(callback: CallbackQuery) -> None:
    interval_seconds = int(callback.data.split(":", 2)[2])
    manager = callback.bot.keep_alive_manager
    pool = callback.bot.db_pool

    await manager.reconfigure(manager.mode, interval_seconds)
    await queries.set_keep_alive_config(pool, manager.mode, interval_seconds)

    await callback.message.edit_text(
        "🟢 Keep Alive Settings\n\n"
        "Choose a mode, then an interval (used by Auto and Task Protection):",
        reply_markup=keyboards.keep_alive_settings_menu(manager.mode, manager.interval_seconds),
    )
    await callback.answer(f"Interval set to {keyboards.KEEP_ALIVE_INTERVAL_LABELS[interval_seconds]}")


@router.callback_query(F.data == "ka:ping")
async def cb_ping_now(callback: CallbackQuery) -> None:
    global _last_ping_time, _last_ping_status

    await callback.message.edit_text(
        "⏳ Sending Ping...",
        reply_markup=keyboards.keep_alive_menu(),
    )
    await callback.answer()

    webhook_url = callback.bot.webhook_url
    health_url = f"{webhook_url.rstrip('/')}/health"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(health_url, timeout=aiohttp.ClientTimeout(total=15)) as response:
                status_code = response.status
                status_text = response.reason or ""

        ping_time = _now_ist_label()
        status_label = f"{status_code} {status_text}".strip()
        _last_ping_time = ping_time
        _last_ping_status = status_label

        if status_code == 200:
            await callback.message.edit_text(
                f"✅ Ping Successful\n\nHTTP Status: {status_label}\n\nTime: {ping_time}",
                reply_markup=keyboards.keep_alive_menu(),
            )
        else:
            await callback.message.edit_text(
                f"⚠️ Ping Returned Non-200\n\nHTTP Status: {status_label}\n\nTime: {ping_time}",
                reply_markup=keyboards.keep_alive_menu(),
            )

    except Exception as exc:
        ping_time = _now_ist_label()
        _last_ping_time = ping_time
        _last_ping_status = "Failed"
        await callback.message.edit_text(
            f"❌ Ping Failed\n\nError: {exc}\n\nTime: {ping_time}",
            reply_markup=keyboards.keep_alive_menu(),
        )


@router.callback_query(F.data == "ka:status")
async def cb_keep_alive_status(callback: CallbackQuery) -> None:
    manager = callback.bot.keep_alive_manager
    mode = manager.mode

    # For Manual mode, preserve the exact original status display (manual
    # ping's own in-memory state, "Mode\nManual"). For Auto / Task
    # Protection, show KeepAliveManager's own automatic-ping status plus
    # mode-specific fields, without removing any existing field.
    if mode == "manual":
        last_ping = _last_ping_time or "No ping sent yet"
        last_status = _last_ping_status or "N/A"
        await callback.message.edit_text(
            f"🟢 Keep Alive Status\n\n"
            f"Last Ping\n{last_ping}\n\n"
            f"HTTP Status\n{last_status}\n\n"
            f"Endpoint\n/health\n\n"
            f"Mode\n{keyboards.KEEP_ALIVE_MODE_LABELS[mode]}",
            reply_markup=keyboards.keep_alive_menu(),
        )
        await callback.answer()
        return

    last_ping = manager.last_ping_time or "No ping sent yet"
    last_status = manager.last_ping_status or "N/A"
    interval_label = keyboards.KEEP_ALIVE_INTERVAL_LABELS.get(
        manager.interval_seconds, f"{manager.interval_seconds}s"
    )

    lines = [
        "🟢 Keep Alive Status",
        "",
        f"Last Ping\n{last_ping}",
        "",
        f"HTTP Status\n{last_status}",
    ]
    if manager.last_ping_error:
        lines.append("")
        lines.append(f"Error\n{manager.last_ping_error}")
    lines.append("")
    lines.append("Endpoint\n/health")
    lines.append("")
    lines.append(f"Mode\n{keyboards.KEEP_ALIVE_MODE_LABELS[mode]}")
    lines.append("")
    lines.append(f"Interval\n{interval_label}")

    if mode == "task_protection":
        job_manager = callback.bot.job_manager
        protection_active = job_manager.is_job_active
        lines.append("")
        lines.append(f"Protection\n{'🟢 Active' if protection_active else '⚪ Idle'}")
        lines.append("")
        lines.append(f"Current Task\n{manager.current_task_label()}")
    else:
        lines.append("")
        lines.append(f"Current Task\n{manager.current_task_label()}")

    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=keyboards.keep_alive_menu(),
    )
    await callback.answer()
