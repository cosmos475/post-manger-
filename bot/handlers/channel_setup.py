"""
Channel configuration flow.

Setup is done by forwarding any post from the target channel to the bot.
The bot reads the forward origin metadata to detect the channel automatically
(chat id + title), then asks for confirmation before saving it as the single
active channel. Changing the channel later reuses this exact same flow.
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

router = Router(name="channel_setup")


class ChannelSetupStates(StatesGroup):
    waiting_for_forward = State()


@router.callback_query(F.data == "menu:set_channel")
async def cb_start_channel_setup(callback: CallbackQuery, state: FSMContext) -> None:
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

    current_channel_text = ""
    if channel is not None:
        current_channel_text = (
            f"Current Channel:\n*{channel.title}*\nChat ID: `{channel.chat_id}`\n\n"
            "Forward another post to replace it.\n\n"
        )

    await state.set_state(ChannelSetupStates.waiting_for_forward)
    await callback.message.edit_text(
        f"📡 {current_channel_text}"
        "Forward any post from your channel here.\n\n"
        "The bot must already be an admin in that channel with edit-message rights.",
        reply_markup=keyboards.main_menu_button(),
        parse_mode="Markdown",
    )
    await callback.answer()


@router.message(ChannelSetupStates.waiting_for_forward)
async def handle_channel_forward(message: Message, state: FSMContext) -> None:
    origin = message.forward_origin

    if origin is None or origin.type != MessageOriginType.CHANNEL:
        await message.answer(
            "That doesn't look like a forwarded channel post. "
            "Please forward a post directly from the target channel.",
            reply_markup=keyboards.main_menu_button(),
        )
        return

    chat = origin.chat
    chat_id = chat.id
    title = chat.title or "Unknown Channel"

    await state.update_data(pending_channel_id=chat_id, pending_channel_title=title)
    await message.answer(
        f"Detected channel: *{title}*\n(ID: `{chat_id}`)\n\nConfirm this is correct?",
        reply_markup=keyboards.confirm_channel(chat_id, title),
        parse_mode="Markdown",
    )


@router.callback_query(F.data.startswith("channel:confirm:"))
async def cb_confirm_channel(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    chat_id = data.get("pending_channel_id")
    title = data.get("pending_channel_title")

    if chat_id is None:
        await callback.answer("Setup expired, please try again.", show_alert=True)
        await state.clear()
        return

    pool = get_pool()
    await queries.set_channel_config(pool, chat_id, title)
    await state.clear()

    await callback.message.edit_text(
        f"✅ Channel configured: *{title}*",
        reply_markup=keyboards.back_to_menu(),
        parse_mode="Markdown",
    )
    await callback.answer()
