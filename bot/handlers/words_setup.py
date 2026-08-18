"""
Find & Replace configuration screen (Caption Manager submenu).

Configuration-only: shows current find/replace values, Enable/Disable,
Edit, and Clear. No scanning, preview, or job creation happens here --
Preview & Run is the only execution point (see job_control.py).

Words are persisted to the database (bot_settings) so they survive menu
navigation and process restarts. Saving words does NOT automatically
enable the feature -- enabled state is tracked separately.

Find/replace values support multi-word phrases (spaces allowed), not just
single words -- e.g. "Follow the Selection Seva channel on WhatsApp" is a
valid find phrase. The underlying matching engine (core/caption_engine.py)
already handles multi-word phrases correctly via literal-string matching
with adaptive word-boundary logic -- no change was needed there, only this
UI-level validation, which previously rejected any input containing a
space.
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message

from bot import keyboards
from db import queries
from db.connection import get_pool

router = Router(name="words_setup")

# Simple, predictable cap: character count, not word count. Prevents
# accidentally pasting an entire caption as the find/replace value while
# still comfortably allowing realistic promotional phrases.
MAX_PHRASE_LENGTH = 200


class WordsSetupStates(StatesGroup):
    waiting_for_find_word = State()
    waiting_for_replace_word = State()


async def _render_find_replace_screen(callback: CallbackQuery) -> None:
    pool = get_pool()
    job_config = await queries.get_job_config(pool)

    if job_config.find_word is not None and job_config.replace_word is not None:
        current_text = f"Find: {job_config.find_word}\nReplace with: {job_config.replace_word}\n\n"
    else:
        current_text = "No words configured yet.\n\n"

    status_text = "Enabled ✅" if job_config.find_replace_enabled else "Disabled ❌"

    extra_buttons = [
        [InlineKeyboardButton(text="✏️ Edit", callback_data="fr:edit")],
        [InlineKeyboardButton(text="🗑️ Clear", callback_data="fr:clear")],
    ]

    await callback.message.edit_text(
        f"🔤 Find & Replace\n\nStatus: {status_text}\n\n{current_text}",
        reply_markup=keyboards.feature_toggle_menu(
            "fr", job_config.find_replace_enabled, extra_buttons, back_to="caption_manager"
        ),
    )


@router.callback_query(F.data == "cm:find_replace")
async def cb_open_find_replace(callback: CallbackQuery) -> None:
    job_manager = callback.bot.job_manager
    active = await job_manager.get_active_job()
    if active is not None:
        await callback.answer(
            "A task is already running. Please wait until it finishes or cancel it from Job Status.",
            show_alert=True,
        )
        return

    await _render_find_replace_screen(callback)
    await callback.answer()


@router.callback_query(F.data == "fr:enable")
async def cb_find_replace_enable(callback: CallbackQuery) -> None:
    pool = get_pool()
    job_config = await queries.get_job_config(pool)
    if job_config.find_word is None or job_config.replace_word is None:
        await callback.answer("Please set find/replace words first.", show_alert=True)
        return

    await queries.set_find_replace_enabled(pool, True)
    await _render_find_replace_screen(callback)
    await callback.answer("Find & Replace enabled.")


@router.callback_query(F.data == "fr:disable")
async def cb_find_replace_disable(callback: CallbackQuery) -> None:
    pool = get_pool()
    await queries.set_find_replace_enabled(pool, False)
    await _render_find_replace_screen(callback)
    await callback.answer("Find & Replace disabled.")


@router.callback_query(F.data == "fr:clear")
async def cb_find_replace_clear(callback: CallbackQuery) -> None:
    pool = get_pool()
    await queries.set_job_words(pool, "", "")
    await queries.set_find_replace_enabled(pool, False)
    await _render_find_replace_screen(callback)
    await callback.answer("Cleared.")


@router.callback_query(F.data == "fr:edit")
async def cb_find_replace_edit(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(WordsSetupStates.waiting_for_find_word)
    await callback.message.edit_text(
        "Send the word or phrase you want to find (whole-word, case-insensitive). "
        f"Up to {MAX_PHRASE_LENGTH} characters -- spaces are allowed.",
        reply_markup=keyboards.back_to_caption_manager(),
    )
    await callback.answer()


@router.message(WordsSetupStates.waiting_for_find_word)
async def handle_find_word(message: Message, state: FSMContext) -> None:
    word = (message.text or "").strip()
    if not word:
        await message.answer("Please send some text.", reply_markup=keyboards.back_to_caption_manager())
        return
    if len(word) > MAX_PHRASE_LENGTH:
        await message.answer(
            f"That's too long ({len(word)} characters). Please keep it under {MAX_PHRASE_LENGTH} characters.",
            reply_markup=keyboards.back_to_caption_manager(),
        )
        return

    await state.update_data(find_word=word)
    await state.set_state(WordsSetupStates.waiting_for_replace_word)
    await message.answer(
        f"Find set: {word}\n\nNow send the replacement text.",
        reply_markup=keyboards.back_to_caption_manager(),
    )


@router.message(WordsSetupStates.waiting_for_replace_word)
async def handle_replace_word(message: Message, state: FSMContext) -> None:
    word = (message.text or "").strip()
    if not word:
        await message.answer("Please send some text.", reply_markup=keyboards.back_to_caption_manager())
        return
    if len(word) > MAX_PHRASE_LENGTH:
        await message.answer(
            f"That's too long ({len(word)} characters). Please keep it under {MAX_PHRASE_LENGTH} characters.",
            reply_markup=keyboards.back_to_caption_manager(),
        )
        return

    data = await state.get_data()
    find_word = data.get("find_word")

    # Preserve any Telegram formatting (most commonly a hyperlink on part of
    # the text, e.g. only "ALEX" linked in "ALEX is King") the user applied
    # when sending the replacement text. Only safe to reuse the entity
    # offsets as-is when message.text was not modified by .strip() (i.e. no
    # leading/trailing whitespace was trimmed) -- otherwise offsets would be
    # off by the trimmed amount.
    replace_word_entities = None
    if message.entities and (message.text or "") == word:
        replace_word_entities = [
            {"type": e.type, "offset": e.offset, "length": e.length, "url": e.url}
            for e in message.entities
            if e.type in ("text_link", "url")
        ]
        if not replace_word_entities:
            replace_word_entities = None

    pool = get_pool()
    await queries.set_job_words(pool, find_word, word, replace_word_entities)
    await state.set_state(None)

    await message.answer(
        f"✅ Words configured:\n\nFind: {find_word}\nReplace with: {word}\n\n"
        "Use Enable on the Find & Replace screen to activate this feature.",
        reply_markup=keyboards.back_to_caption_manager(),
    )
