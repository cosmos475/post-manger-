"""
Post Manager job control: preview -> confirm -> start delete run.

Mirrors bot/handlers/job_control.py's pattern, but for post_delete jobs.
Fully independent of Caption Manager's job:start / menu:preview callbacks
(distinct callback_data), and never reads/writes Caption Manager's
bot_settings columns.
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery

from bot import keyboards
from core.job_manager import JobManagerError
from core.job_runner import ProgressSnapshot, build_delete_preview
from db import queries
from db.connection import get_pool
from db.models import JobStatus, TaskType

router = Router(name="post_manager_control")


def _format_delete_progress(snapshot: ProgressSnapshot) -> str:
    return (
        f"⏳ Progress: {snapshot.processed_count}/{snapshot.total_count}\n"
        f"✅ Deleted: {snapshot.edited_count}\n"
        f"⏭️ Skipped: {snapshot.skipped_count}\n"
        f"❌ Failed: {snapshot.failed_count}"
    )


async def _get_job_manager(callback: CallbackQuery):
    return callback.bot.job_manager


@router.callback_query(F.data == "pm:preview")
async def cb_pm_preview(callback: CallbackQuery) -> None:
    pool = get_pool()
    pm_config = await queries.get_post_manager_config(pool)

    if pm_config.target_chat_id is None:
        await callback.answer("Please configure a target first.", show_alert=True)
        return
    if pm_config.range_start_message_id is None or pm_config.range_end_message_id is None:
        await callback.answer("Please set a delete range first.", show_alert=True)
        return

    job_manager = await _get_job_manager(callback)
    active = await job_manager.get_active_job()
    if active is not None:
        await callback.answer(
            f"A task is already running. Please wait until it finishes or cancel it from Job Status.",
            show_alert=True,
        )
        return

    settings = await queries.get_settings(pool)
    preview = build_delete_preview(pm_config.range_start_message_id, pm_config.range_end_message_id)
    estimated_seconds = int(preview.total_scanned * settings.action_delay_seconds)

    await callback.message.edit_text(
        f"🗑️ Delete Preview\n\n"
        f"Target: {pm_config.target_title}\n"
        f"Operation Type: Post Deletion\n"
        f"Total Messages: {preview.total_scanned}\n"
        f"Range: {pm_config.range_start_message_id} \u2192 {pm_config.range_end_message_id}\n"
        f"Delay: {settings.action_delay_seconds}s\n"
        f"Estimated Time: ~{estimated_seconds}s\n\n"
        "Start the delete run?",
        reply_markup=keyboards.confirm_delete_run(preview.total_scanned),
    )
    await callback.answer()


@router.callback_query(F.data == "pm:job:start")
async def cb_pm_start_job(callback: CallbackQuery) -> None:
    pool = get_pool()
    pm_config = await queries.get_post_manager_config(pool)

    if pm_config.target_chat_id is None or pm_config.range_start_message_id is None or pm_config.range_end_message_id is None:
        await callback.answer("Setup incomplete, please start again.", show_alert=True)
        return

    job_manager = await _get_job_manager(callback)

    async def progress_callback(snapshot: ProgressSnapshot) -> None:
        try:
            await callback.bot.send_message(
                chat_id=callback.from_user.id,
                text=_format_delete_progress(snapshot),
            )
        except Exception:
            pass

    try:
        job = await job_manager.start_new_job(
            task_type=TaskType.POST_DELETE,
            channel_chat_id=pm_config.target_chat_id,
            range_start_message_id=pm_config.range_start_message_id,
            range_end_message_id=pm_config.range_end_message_id,
            target_thread_id=pm_config.thread_id,
            progress_callback=progress_callback,
        )
    except JobManagerError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    await callback.message.edit_text(
        f"🚀 Job {job.id} started (Post Deletion).\n\nYou'll receive progress updates every 20 messages.",
        reply_markup=keyboards.job_controls(is_paused=False),
    )
    await callback.answer()
