"""
Post Manager target and range configuration.

Fully independent of Caption Manager -- separate bot_settings columns,
separate setup flow. Supports three target types:
  - Channel: forward any channel post (same pattern as Caption Manager).
  - Normal Group: /settarget command sent inside the group.
  - Forum Topic: /settarget command sent inside the specific topic.

Range setup differs by target type:
  - Channel: forward first/last post (Bot API exposes original message_id
    via forward_origin for channel posts).
  - Group/Forum Topic: paste first/last message LINKS (Bot API does not
    expose original message_id for forwarded group/topic posts).
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.enums import MessageOriginType
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from bot import keyboards
from core.telegram_ops import parse_message_link
from db import queries
from db.connection import get_pool
from db.models import PostManagerTargetType

router = Router(name="post_manager_setup")


class PostManagerTargetStates(StatesGroup):
    waiting_for_channel_forward = State()


class PostManagerRangeStates(StatesGroup):
    waiting_for_first_channel = State()
    waiting_for_last_channel = State()
    waiting_for_first_link = State()
    waiting_for_last_link = State()


@router.callback_query(F.data == "menu:post_manager")
async def cb_open_post_manager(callback: CallbackQuery) -> None:
    pool = get_pool()
    pm_config = await queries.get_post_manager_config(pool)

    if pm_config.target_chat_id is not None:
        current_text = (
            f"Current Target:\n{pm_config.target_title}\n"
            f"Type: {pm_config.target_type.value if pm_config.target_type else 'unknown'}\n\n"
        )
    else:
        current_text = "No target configured yet.\n\n"

    await callback.message.edit_text(
        f"📮 Post Manager\n\n{current_text}Choose an option below.",
        reply_markup=keyboards.post_manager_menu(),
    )
    await callback.answer()


@router.callback_query(F.data == "pm:configure_target")
async def cb_configure_target(callback: CallbackQuery) -> None:
    job_manager = callback.bot.job_manager
    active = await job_manager.get_active_job()
    if active is not None:
        await callback.answer(
            "A task is already running. Please wait until it finishes or cancel it from Job Status.",
            show_alert=True,
        )
        return

    await callback.message.edit_text(
        "🎯 Choose the target type for Post Manager.",
        reply_markup=keyboards.post_manager_target_type_menu(),
    )
    await callback.answer()


@router.callback_query(F.data == "pm:target:channel")
async def cb_target_channel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(PostManagerTargetStates.waiting_for_channel_forward)
    await callback.message.edit_text(
        "📡 Forward any post from the target channel here.\n\n"
        "The bot must already be an admin in that channel with delete-message rights.",
        reply_markup=keyboards.main_menu_button(),
    )
    await callback.answer()


@router.message(PostManagerTargetStates.waiting_for_channel_forward)
async def handle_target_channel_forward(message: Message, state: FSMContext) -> None:
    origin = message.forward_origin

    if origin is None or origin.type != MessageOriginType.CHANNEL:
        await message.answer(
            "That doesn't look like a forwarded channel post. Please forward a post directly from the target channel.",
            reply_markup=keyboards.main_menu_button(),
        )
        return

    chat = origin.chat
    pool = get_pool()
    await queries.set_post_manager_target(
        pool, chat.id, PostManagerTargetType.CHANNEL, chat.title or "Unknown Channel"
    )
    await state.set_state(None)

    await message.answer(
        f"✅ Post Manager target set: {chat.title}",
        reply_markup=keyboards.back_to_menu(),
    )


@router.callback_query(F.data == "pm:target:group")
async def cb_target_group(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        "👥 Normal Group setup\n\n"
        "Send /settarget inside the group you want to configure as the target. "
        "The bot must already be a member of that group with delete-message rights.",
        reply_markup=keyboards.main_menu_button(),
    )
    await callback.answer()


@router.callback_query(F.data == "pm:target:forum_topic")
async def cb_target_forum_topic(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        "🗂️ Forum Topic setup\n\n"
        "Send /settarget inside the specific topic you want to configure as the target. "
        "The bot must already be a member of that group with delete-message rights.",
        reply_markup=keyboards.main_menu_button(),
    )
    await callback.answer()


@router.message(Command("settarget"))
async def cmd_settarget(message: Message) -> None:
    """
    Sets the Post Manager target from within a group or forum topic.
    Distinguishes group vs forum topic by the presence of message_thread_id.
    """
    if message.chat.type not in ("group", "supergroup"):
        await message.answer("This command only works inside a group or forum topic.")
        return

    pool = get_pool()
    thread_id = message.message_thread_id if message.is_topic_message else None
    target_type = PostManagerTargetType.FORUM_TOPIC if thread_id else PostManagerTargetType.GROUP
    title = message.chat.title or "Unknown Group"

    await queries.set_post_manager_target(pool, message.chat.id, target_type, title, thread_id=thread_id)

    label = "forum topic" if thread_id else "group"
    await message.answer(f"✅ Post Manager target set to this {label}: {title}")


@router.callback_query(F.data == "pm:set_range")
async def cb_set_range(callback: CallbackQuery, state: FSMContext) -> None:
    pool = get_pool()

    job_manager = callback.bot.job_manager
    active = await job_manager.get_active_job()
    if active is not None:
        await callback.answer(
            "A task is already running. Please wait until it finishes or cancel it from Job Status.",
            show_alert=True,
        )
        return

    pm_config = await queries.get_post_manager_config(pool)

    if pm_config.target_chat_id is None:
        await callback.answer("Please configure a target first.", show_alert=True)
        return

    current_range_text = ""
    if pm_config.range_start_message_id is not None and pm_config.range_end_message_id is not None:
        total = pm_config.range_end_message_id - pm_config.range_start_message_id + 1
        current_range_text = (
            f"Current range: `{pm_config.range_start_message_id}` \u2192 "
            f"`{pm_config.range_end_message_id}` ({total} messages)\n\n"
        )

    if pm_config.target_type == PostManagerTargetType.CHANNEL:
        await state.set_state(PostManagerRangeStates.waiting_for_first_channel)
        await callback.message.edit_text(
            f"🗂️ {current_range_text}Forward the *first* post of the range you want to delete.",
            reply_markup=keyboards.main_menu_button(),
            parse_mode="Markdown",
        )
    else:
        await state.set_state(PostManagerRangeStates.waiting_for_first_link)
        await callback.message.edit_text(
            f"🗂️ {current_range_text}Paste the *first* message link of the range you want to delete.\n\n"
            "Example: https://t.me/c/1234567890/55",
            reply_markup=keyboards.main_menu_button(),
            parse_mode="Markdown",
        )
    await callback.answer()


@router.message(PostManagerRangeStates.waiting_for_first_channel)
async def handle_range_first_channel(message: Message, state: FSMContext) -> None:
    pool = get_pool()
    pm_config = await queries.get_post_manager_config(pool)
    origin = message.forward_origin

    if origin is None or origin.type != MessageOriginType.CHANNEL or origin.chat.id != pm_config.target_chat_id:
        await message.answer(
            "That doesn't look like a forwarded post from the configured target channel.",
            reply_markup=keyboards.main_menu_button(),
        )
        return

    await state.update_data(pm_range_start=origin.message_id)
    await state.set_state(PostManagerRangeStates.waiting_for_last_channel)
    await message.answer(
        f"✅ Start message captured (ID: `{origin.message_id}`).\n\nNow forward the *last* post of the range.",
        reply_markup=keyboards.main_menu_button(),
        parse_mode="Markdown",
    )


@router.message(PostManagerRangeStates.waiting_for_last_channel)
async def handle_range_last_channel(message: Message, state: FSMContext) -> None:
    pool = get_pool()
    pm_config = await queries.get_post_manager_config(pool)
    origin = message.forward_origin

    if origin is None or origin.type != MessageOriginType.CHANNEL or origin.chat.id != pm_config.target_chat_id:
        await message.answer(
            "That doesn't look like a forwarded post from the configured target channel.",
            reply_markup=keyboards.main_menu_button(),
        )
        return

    data = await state.get_data()
    start_id = data.get("pm_range_start")
    if start_id is None:
        await message.answer("Something went wrong -- please restart range setup.")
        await state.clear()
        return

    end_id = origin.message_id
    if end_id < start_id:
        start_id, end_id = end_id, start_id

    await queries.set_post_manager_range(pool, start_id, end_id)
    await state.set_state(None)

    total = end_id - start_id + 1
    await message.answer(
        f"✅ Range set: `{start_id}` \u2192 `{end_id}` ({total} messages).",
        reply_markup=keyboards.back_to_menu(),
        parse_mode="Markdown",
    )


@router.message(PostManagerRangeStates.waiting_for_first_link)
async def handle_range_first_link(message: Message, state: FSMContext) -> None:
    parsed = parse_message_link((message.text or "").strip())
    if parsed is None:
        await message.answer(
            "That doesn't look like a valid message link. Please paste a link like https://t.me/c/1234567890/55",
            reply_markup=keyboards.main_menu_button(),
        )
        return

    await state.update_data(pm_range_start=parsed.message_id)
    await state.set_state(PostManagerRangeStates.waiting_for_last_link)
    await message.answer(
        f"✅ Start message captured (ID: `{parsed.message_id}`).\n\nNow paste the *last* message link of the range.",
        reply_markup=keyboards.main_menu_button(),
        parse_mode="Markdown",
    )


@router.message(PostManagerRangeStates.waiting_for_last_link)
async def handle_range_last_link(message: Message, state: FSMContext) -> None:
    parsed = parse_message_link((message.text or "").strip())
    if parsed is None:
        await message.answer(
            "That doesn't look like a valid message link. Please paste a link like https://t.me/c/1234567890/55",
            reply_markup=keyboards.main_menu_button(),
        )
        return

    data = await state.get_data()
    start_id = data.get("pm_range_start")
    if start_id is None:
        await message.answer("Something went wrong -- please restart range setup.")
        await state.clear()
        return

    end_id = parsed.message_id
    if end_id < start_id:
        start_id, end_id = end_id, start_id

    pool = get_pool()
    await queries.set_post_manager_range(pool, start_id, end_id)
    await state.set_state(None)

    total = end_id - start_id + 1
    await message.answer(
        f"✅ Range set: `{start_id}` \u2192 `{end_id}` ({total} messages).",
        reply_markup=keyboards.back_to_menu(),
        parse_mode="Markdown",
    )
