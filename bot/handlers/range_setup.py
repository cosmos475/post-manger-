"""
Processing-range configuration flow.

Per architecture: the user forwards the first and last message of the range
they want processed. The bot reads `forward_origin.message_id` from each
forward to capture the message_id boundaries, and assumes IDs in between are
contiguous (gaps are handled as Skipped during the actual run, not here).

The confirmed range is persisted to the database (bot_settings) so it
survives menu navigation and process restarts, mirroring channel
configuration. FSM state is used only transiently during the two-step
forward sequence itself.
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.enums import MessageOriginType
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from bot import keyboards
from db import queries
from db.connection import get_pool

router = Router(name="range_setup")


class RangeSetupStates(StatesGroup):
    waiting_for_first = State()
    waiting_for_last = State()


@router.callback_query(F.data == "menu:set_range")
async def cb_start_range_setup(callback: CallbackQuery, state: FSMContext) -> None:
    pool = get_pool()

    job_manager = callback.bot.job_manager
    active = await job_manager.get_active_job()
    if active is not None:
        await callback.answer(
            "A task is already running. Please wait until it finishes or cancel it from Job Status.",
            show_alert=True,
        )
        return

    channel = await queries.get_channel_config(pool)
    if channel is None:
        await callback.answer("Please configure a channel first.", show_alert=True)
        return

    job_config = await queries.get_job_config(pool)
    current_range_text = ""
    if job_config.range_start_message_id is not None and job_config.range_end_message_id is not None:
        total = job_config.range_end_message_id - job_config.range_start_message_id + 1
        current_range_text = (
            f"Current range: `{job_config.range_start_message_id}` \u2192 "
            f"`{job_config.range_end_message_id}` ({total} messages)\n\n"
        )

    await state.set_state(RangeSetupStates.waiting_for_first)
    await callback.message.edit_text(
        f"🎯 {current_range_text}"
        "Forward the *first* post of the range you want to process.",
        reply_markup=keyboards.main_menu_button(),
        parse_mode="Markdown",
    )
    await callback.answer()


def _extract_forwarded_message_id(message: Message, expected_chat_id: int) -> int | None:
    """
    Returns the original message_id from a forwarded channel post, or None
    if the forward isn't from the expected channel.
    """
    origin = message.forward_origin
    if origin is None or origin.type != MessageOriginType.CHANNEL:
        return None
    if origin.chat.id != expected_chat_id:
        return None
    return origin.message_id


@router.message(RangeSetupStates.waiting_for_first)
async def handle_first_message(message: Message, state: FSMContext) -> None:
    pool = get_pool()
    channel = await queries.get_channel_config(pool)
    if channel is None:
        await message.answer("Channel is no longer configured. Please set it up again.")
        await state.clear()
        return

    message_id = _extract_forwarded_message_id(message, channel.chat_id)
    if message_id is None:
        await message.answer(
            f"That doesn't look like a forwarded post from *{channel.title}*. "
            "Please forward a post from the configured channel.",
            reply_markup=keyboards.main_menu_button(),
            parse_mode="Markdown",
        )
        return

    await state.update_data(range_start_message_id=message_id)
    await state.set_state(RangeSetupStates.waiting_for_last)
    await message.answer(
        f"✅ Start message captured (ID: `{message_id}`).\n\n"
        "Now forward the *last* post of the range.",
        reply_markup=keyboards.main_menu_button(),
        parse_mode="Markdown",
    )


@router.message(RangeSetupStates.waiting_for_last)
async def handle_last_message(message: Message, state: FSMContext) -> None:
    pool = get_pool()
    channel = await queries.get_channel_config(pool)
    if channel is None:
        await message.answer("Channel is no longer configured. Please set it up again.")
        await state.clear()
        return

    message_id = _extract_forwarded_message_id(message, channel.chat_id)
    if message_id is None:
        await message.answer(
            f"That doesn't look like a forwarded post from *{channel.title}*. "
            "Please forward a post from the configured channel.",
            reply_markup=keyboards.main_menu_button(),
            parse_mode="Markdown",
        )
        return

    data = await state.get_data()
    start_id = data.get("range_start_message_id")

    if start_id is None:
        await message.answer("Something went wrong -- please restart range setup.")
        await state.clear()
        return

    end_id = message_id
    if end_id < start_id:
        # Be forgiving of order-of-forwarding; swap so start <= end always.
        start_id, end_id = end_id, start_id

    total = end_id - start_id + 1

    await queries.set_job_range(pool, start_id, end_id)
    await state.set_state(None)

    await message.answer(
        f"✅ Range set: `{start_id}` \u2192 `{end_id}` ({total} messages).",
        reply_markup=keyboards.back_to_menu(),
        parse_mode="Markdown",
    )
