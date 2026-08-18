"""
/start, /cancel, and main-menu callback routing.

Per architecture: only two slash commands exist (/start, /cancel);
everything else is button-driven via callback_data, handled here and in the
other bot/handlers/*.py modules.
"""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot import keyboards

router = Router(name="start")


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "👋 Caption Manager Bot\n\nWhat would you like to do?",
        reply_markup=keyboards.main_menu(),
    )


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "Cancelled current action.",
        reply_markup=keyboards.main_menu(),
    )


@router.callback_query(lambda c: c.data == "menu:root")
async def cb_back_to_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text(
        "👋 Caption Manager Bot\n\nWhat would you like to do?",
        reply_markup=keyboards.main_menu(),
    )
    await callback.answer()
