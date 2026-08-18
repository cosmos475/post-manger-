"""
Job runner: the per-message processing loop for a single bulk job.

Runs as an in-process asyncio.Task (per architecture decision: no separate
worker, Render free Web Service only). Progress is persisted to the DB after
every single message -- not batched -- so a crash/restart can resume exactly
from `cursor_message_id + 1` without losing or double-counting work.

Supports two task types (Job.task_type):
  - caption_edit: read -> transform -> write caption (original behavior,
    unchanged).
  - post_delete: delete message directly (Post Manager). No content
    inspection needed, so no scratch-chat/forwardMessage step -- just a
    direct deleteMessage call per message_id.

Idempotency note: caption transforms (link removal, whole-word replace) are
naturally idempotent -- re-running them on an already-cleaned caption is a
no-op. Message deletion is also naturally idempotent -- deleting an
already-deleted message just returns "not found", handled as Skipped. This
means at-least-once processing (possible if the process dies between a
successful write/delete and the progress-persist call) is safe in both cases.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from enum import Enum

import asyncpg
from aiogram import Bot

from core import telegram_ops
from core.caption_engine import CaptionEntity, transform_caption
from core.telegram_ops import DeleteOutcome, ReadOutcome, WriteOutcome
from db import queries
from db.models import Job, JobStatus, MessageLogStatus, TaskType

logger = logging.getLogger(__name__)


def _dicts_to_entities(raw: list[dict] | None) -> list[CaptionEntity] | None:
    """Converts entity dicts (as stored in the DB/JSONB) back to CaptionEntity objects."""
    if not raw:
        return None
    return [
        CaptionEntity(type=d["type"], offset=d["offset"], length=d["length"], url=d.get("url"))
        for d in raw
    ]

# Progress updates are pushed to the UI every N messages, per requirements.
PROGRESS_UPDATE_INTERVAL = 20

# After every PROGRESS_UPDATE_INTERVAL processed messages, pause for this
# long before continuing -- separate from the existing per-message
# `delay_seconds` throttle. Set to 0 to disable. Caption Manager only
# (post_delete jobs never pass a nonzero value from job_control.py, so
# Post Manager's timing is unaffected unless this constant itself is
# changed for both task types).
BATCH_COOLDOWN_SECONDS = 30


class StopReason(str, Enum):
    """Why a job run loop exited."""

    COMPLETED = "completed"  # reached range_end_message_id
    STOPPED_BY_USER = "stopped_by_user"  # cooperative stop requested
    FAILED = "failed"  # unexpected/unrecoverable error


@dataclass(frozen=True)
class RunOutcome:
    stop_reason: StopReason
    final_job: Job


@dataclass
class ProgressSnapshot:
    """Lightweight progress snapshot passed to the progress callback."""

    current_message_id: int
    total_count: int
    processed_count: int
    edited_count: int
    skipped_count: int
    failed_count: int
    task_type: TaskType = TaskType.CAPTION_EDIT
    # "editing" (default) or "sleeping" -- lets the bot layer render a
    # distinct status line during the batch-cooldown pause. Unused by any
    # existing caller, so existing behavior (post_delete, or any callback
    # not yet updated to read it) is unaffected.
    status: str = "editing"
    sleeping_seconds: int = 0


@dataclass(frozen=True)
class PreviewResult:
    """Result of a stateless dry-run scan over a message range (caption edit only)."""

    total_scanned: int
    would_edit_count: int
    would_skip_count: int
    would_fail_count: int


@dataclass(frozen=True)
class DeletePreviewResult:
    """Result of a stateless preview for a Post Manager delete range."""

    total_scanned: int


async def run_dry_run_preview(
    bot: Bot,
    scratch_chat_id: int,
    channel_chat_id: int,
    range_start_message_id: int,
    range_end_message_id: int,
    find_word: str,
    replace_word: str,
    remove_links: bool,
    inject_text_value: str | None = None,
    remove_urls: bool = False,
    replace_word_entities: list | None = None,
    inject_text_entities: list | None = None,
    promo_phrases: list[str] | None = None,
    add_hyperlink_url: str | None = None,
    quote_removal_enabled: bool = False,
) -> PreviewResult:
    """
    Stateless preview scan: read + transform every message in the range,
    but never write and never create a DB job row (per architecture: dry-run
    is stateless, only a confirmed run creates a resumable job). Caption
    Manager only.

    Single unified scan reflecting all enabled Caption Manager features
    (Remove Direct URLs, Remove Hyperlinks, Find & Replace, Caption
    Injector) in one pass -- callers pass empty/False/None for any
    disabled feature.

    This performs the same number of read calls as a real run would, since
    reading is the only way to know if a caption would actually change
    (see architecture note on copyMessage/forwardMessage limitations).
    """
    would_edit = 0
    would_skip = 0
    would_fail = 0

    for message_id in range(range_start_message_id, range_end_message_id + 1):
        read_result = await telegram_ops.read_caption_via_forward(
            bot=bot,
            source_chat_id=channel_chat_id,
            scratch_chat_id=scratch_chat_id,
            message_id=message_id,
        )

        if read_result.outcome == ReadOutcome.NOT_FOUND:
            would_skip += 1
        elif read_result.outcome in (ReadOutcome.PERMISSION_ERROR, ReadOutcome.OTHER_ERROR):
            would_fail += 1
        else:
            caption = read_result.caption
            entities = read_result.entities or []
            if not caption:
                would_skip += 1
            else:
                result = transform_caption(
                    text=caption,
                    entities=entities,
                    find_word=find_word,
                    replace_word_with=replace_word,
                    remove_links=remove_links,
                    inject_text_value=inject_text_value,
                    remove_urls=remove_urls,
                    replace_word_entities=replace_word_entities,
                    inject_text_entities=inject_text_entities,
                    promo_phrases=promo_phrases,
                    add_hyperlink_url=add_hyperlink_url,
                    remove_quotes_enabled=quote_removal_enabled,
                )
                if result.changed and result.text.strip():
                    would_edit += 1
                else:
                    would_skip += 1

        await telegram_ops.throttle()

    total = range_end_message_id - range_start_message_id + 1
    return PreviewResult(
        total_scanned=total,
        would_edit_count=would_edit,
        would_skip_count=would_skip,
        would_fail_count=would_fail,
    )


def build_delete_preview(range_start_message_id: int, range_end_message_id: int) -> DeletePreviewResult:
    """
    Post Manager preview is a simple count -- unlike Caption Manager, there
    is no content to inspect before deciding whether a delete would apply
    (a message either exists or it doesn't, discovered only at delete time),
    so no scan/read calls are made here at all.
    """
    total = range_end_message_id - range_start_message_id + 1
    return DeletePreviewResult(total_scanned=total)


class JobRunner:
    """
    Drives a single job's message-by-message processing loop.

    One JobRunner instance corresponds to one asyncio.Task. job_manager is
    responsible for creating/tracking that task and enforcing only one
    runs at a time, across both task types.
    """

    def __init__(
        self,
        pool: asyncpg.Pool,
        bot: Bot,
        scratch_chat_id: int,
        delay_seconds: float,
        progress_callback=None,
    ) -> None:
        """
        Args:
            pool: shared asyncpg connection pool.
            bot: aiogram Bot instance.
            scratch_chat_id: private chat used for forwardMessage-based reads
                (caption_edit jobs only; unused for post_delete).
            delay_seconds: configurable throttle delay shared by both task
                types (Settings.action_delay_seconds).
            progress_callback: optional async callable(ProgressSnapshot) -> None,
                invoked every PROGRESS_UPDATE_INTERVAL messages so the bot
                layer can push a Telegram message/edit to the user.
        """
        self._pool = pool
        self._bot = bot
        self._scratch_chat_id = scratch_chat_id
        self._delay_seconds = delay_seconds
        self._progress_callback = progress_callback
        self._stop_requested = False

    def request_stop(self) -> None:
        """
        Cooperative stop signal. The loop checks this between messages and
        exits cleanly at the next opportunity -- never mid-message, so
        progress persisted always reflects a fully processed message.
        """
        self._stop_requested = True

    async def run(self, job: Job) -> RunOutcome:
        """
        Process messages from `job.cursor_message_id + 1` through
        `job.range_end_message_id`, inclusive. Branches per-message logic
        on `job.task_type`; the loop/progress/cursor scaffold is shared.

        This method is safe to call on a freshly created job (cursor ==
        range_start - 1) or on a resumed job (cursor == wherever it left off).
        """
        current_job = job
        processed_since_last_update = 0

        start_id = current_job.cursor_message_id + 1
        end_id = current_job.range_end_message_id

        # Immediate 0/N (or resumed-cursor/N) snapshot before any message is
        # processed, so the UI shows progress right away instead of only
        # after the first PROGRESS_UPDATE_INTERVAL batch. No-op for
        # post_delete (progress_callback is only ever passed for
        # caption_edit today; this stays generic/safe either way).
        if self._progress_callback is not None:
            initial_snapshot = ProgressSnapshot(
                current_message_id=start_id - 1,
                total_count=current_job.total_count,
                processed_count=job.edited_count + job.skipped_count + job.failed_count,
                edited_count=current_job.edited_count,
                skipped_count=current_job.skipped_count,
                failed_count=current_job.failed_count,
                task_type=current_job.task_type,
                status="editing",
            )
            await self._progress_callback(initial_snapshot)

        for message_id in range(start_id, end_id + 1):
            if self._stop_requested:
                await queries.update_job_status(self._pool, current_job.id, JobStatus.STOPPED)
                current_job = await queries.get_job(self._pool, current_job.id)
                return RunOutcome(stop_reason=StopReason.STOPPED_BY_USER, final_job=current_job)

            if current_job.task_type == TaskType.POST_DELETE:
                outcome, processed_delta = await self._process_one_message_delete(current_job, message_id)
                edited_delta = 0
            else:
                outcome = await self._process_one_message(current_job, message_id)
                processed_delta = 0
                edited_delta = 1 if outcome == MessageLogStatus.EDITED else 0

            # Persist progress after this single message, unconditionally.
            await queries.update_job_progress(
                self._pool,
                job_id=current_job.id,
                cursor_message_id=message_id,
                edited_delta=edited_delta,
                skipped_delta=1 if outcome == MessageLogStatus.SKIPPED else 0,
                failed_delta=1 if outcome == MessageLogStatus.FAILED else 0,
                processed_delta=processed_delta,
            )

            processed_since_last_update += 1

            if self._progress_callback is not None and (
                processed_since_last_update >= PROGRESS_UPDATE_INTERVAL or message_id == end_id
            ):
                current_job = await queries.get_job(self._pool, current_job.id)
                snapshot = ProgressSnapshot(
                    current_message_id=message_id,
                    total_count=current_job.total_count,
                    processed_count=current_job.edited_count + current_job.skipped_count + current_job.failed_count,
                    edited_count=current_job.edited_count,
                    skipped_count=current_job.skipped_count,
                    failed_count=current_job.failed_count,
                    task_type=current_job.task_type,
                    status="editing",
                )
                hit_batch_boundary = processed_since_last_update >= PROGRESS_UPDATE_INTERVAL
                await self._progress_callback(snapshot)
                processed_since_last_update = 0

                # Batch cooldown: separate from the existing per-message
                # `delay_seconds` throttle below. Only runs on an actual
                # 20-message boundary (not on the final partial batch), only
                # for caption_edit, and only if not the last message overall
                # (no point cooling down right before the loop ends anyway).
                if (
                    hit_batch_boundary
                    and message_id != end_id
                    and current_job.task_type == TaskType.CAPTION_EDIT
                    and BATCH_COOLDOWN_SECONDS > 0
                    and not self._stop_requested
                ):
                    sleeping_snapshot = ProgressSnapshot(
                        current_message_id=message_id,
                        total_count=current_job.total_count,
                        processed_count=current_job.edited_count + current_job.skipped_count + current_job.failed_count,
                        edited_count=current_job.edited_count,
                        skipped_count=current_job.skipped_count,
                        failed_count=current_job.failed_count,
                        task_type=current_job.task_type,
                        status="sleeping",
                        sleeping_seconds=BATCH_COOLDOWN_SECONDS,
                    )
                    await self._progress_callback(sleeping_snapshot)
                    remaining = BATCH_COOLDOWN_SECONDS
                    while remaining > 0 and not self._stop_requested:
                        chunk = min(1, remaining)
                        await asyncio.sleep(chunk)
                        remaining -= chunk

                    # Cooldown finished (and not interrupted by a stop) --
                    # flip the same message back to "editing" immediately,
                    # rather than leaving the sleeping status shown until
                    # the next 20-message progress update.
                    if not self._stop_requested:
                        resumed_snapshot = ProgressSnapshot(
                            current_message_id=message_id,
                            total_count=current_job.total_count,
                            processed_count=current_job.edited_count + current_job.skipped_count + current_job.failed_count,
                            edited_count=current_job.edited_count,
                            skipped_count=current_job.skipped_count,
                            failed_count=current_job.failed_count,
                            task_type=current_job.task_type,
                            status="editing",
                        )
                        await self._progress_callback(resumed_snapshot)

            # Proactive throttling between messages (Bot API rate-limit safety),
            # using the configured shared delay for both task types.
            await telegram_ops.throttle(self._delay_seconds)

        await queries.update_job_status(self._pool, current_job.id, JobStatus.COMPLETED)
        current_job = await queries.get_job(self._pool, current_job.id)
        return RunOutcome(stop_reason=StopReason.COMPLETED, final_job=current_job)

    async def _process_one_message(self, job: Job, message_id: int) -> MessageLogStatus:
        """
        Read -> transform -> write for a single message_id (caption_edit).

        Returns the MessageLogStatus recorded, and also writes a row to
        message_logs for later inspection.
        """
        read_result = await telegram_ops.read_caption_via_forward(
            bot=self._bot,
            source_chat_id=job.channel_chat_id,
            scratch_chat_id=self._scratch_chat_id,
            message_id=message_id,
        )

        if read_result.outcome == ReadOutcome.NOT_FOUND:
            return await self._log_and_return(
                job.id, message_id, MessageLogStatus.SKIPPED, "message not found / cannot be read"
            )

        if read_result.outcome == ReadOutcome.PERMISSION_ERROR:
            return await self._log_and_return(
                job.id, message_id, MessageLogStatus.FAILED, read_result.error_detail or "permission error"
            )

        if read_result.outcome == ReadOutcome.OTHER_ERROR:
            return await self._log_and_return(
                job.id, message_id, MessageLogStatus.FAILED, read_result.error_detail or "unknown read error"
            )

        # read_result.outcome == ReadOutcome.OK from here on.
        caption = read_result.caption
        entities = read_result.entities or []

        if not caption:
            return await self._log_and_return(
                job.id, message_id, MessageLogStatus.SKIPPED, "no caption"
            )

        result = transform_caption(
            text=caption,
            entities=entities,
            find_word=job.find_word or "",
            replace_word_with=job.replace_word or "",
            remove_links=job.remove_links,
            inject_text_value=job.inject_text,
            remove_urls=job.remove_urls,
            replace_word_entities=_dicts_to_entities(job.replace_word_entities),
            inject_text_entities=_dicts_to_entities(job.inject_text_entities),
            promo_phrases=job.promo_phrases,
            add_hyperlink_url=job.add_hyperlink_url,
            remove_quotes_enabled=job.quote_removal_enabled,
        )

        if not result.changed:
            return await self._log_and_return(
                job.id, message_id, MessageLogStatus.SKIPPED, "no matching word or link found"
            )

        if not result.text.strip():
            return await self._log_and_return(
                job.id, message_id, MessageLogStatus.SKIPPED,
                "resulting caption would be empty (e.g. all lines removed by Promotional Line Remover)",
            )

        write_result = await telegram_ops.write_caption(
            bot=self._bot,
            chat_id=job.channel_chat_id,
            message_id=message_id,
            new_caption=result.text,
            new_entities=result.entities,
            is_text_message=read_result.is_text_message,
        )

        if write_result.outcome in (WriteOutcome.OK, WriteOutcome.NOT_MODIFIED):
            return await self._log_and_return(job.id, message_id, MessageLogStatus.EDITED, None)

        if write_result.outcome == WriteOutcome.PERMISSION_ERROR:
            return await self._log_and_return(
                job.id, message_id, MessageLogStatus.FAILED, write_result.error_detail or "permission error"
            )

        return await self._log_and_return(
            job.id, message_id, MessageLogStatus.FAILED, write_result.error_detail or "unknown write error"
        )

    async def _process_one_message_delete(self, job: Job, message_id: int) -> tuple[MessageLogStatus, int]:
        """
        Delete a single message_id (post_delete). No content inspection
        needed -- direct deleteMessage call. Returns (status, processed_delta)
        where processed_delta is 1 only on a successful delete.
        """
        delete_result = await telegram_ops.delete_message_safe(
            bot=self._bot,
            chat_id=job.channel_chat_id,
            message_id=message_id,
        )

        if delete_result.outcome == DeleteOutcome.OK:
            status = await self._log_and_return(job.id, message_id, MessageLogStatus.EDITED, None)
            return status, 1

        if delete_result.outcome == DeleteOutcome.NOT_FOUND:
            status = await self._log_and_return(
                job.id, message_id, MessageLogStatus.SKIPPED, "message not found / already deleted"
            )
            return status, 0

        status = await self._log_and_return(
            job.id, message_id, MessageLogStatus.FAILED,
            delete_result.error_detail or "delete failed",
        )
        return status, 0

    async def _log_and_return(
        self,
        job_id: int,
        message_id: int,
        status: MessageLogStatus,
        reason: str | None,
    ) -> MessageLogStatus:
        try:
            await queries.add_message_log(self._pool, job_id, message_id, status, reason)
        except Exception:
            # Logging failure must never abort message processing -- log
            # locally and continue; progress tracking in `jobs` table is the
            # source of truth for counts, message_logs is supplementary detail.
            logger.exception(
                "Failed to write message_log row (job_id=%s, message_id=%s, status=%s)",
                job_id,
                message_id,
                status,
            )
        return status
