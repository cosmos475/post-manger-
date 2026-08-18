"""
Help: /help command + "❓ Help" menu button, and all guide sub-screens.

Purely informational -- displays static text explaining bot purpose and
feature usage. Does not touch caption editing, post deletion, or any other
core logic; reads no database state and calls no core/ functions.
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from bot import keyboards

router = Router(name="help")


ABOUT_TEXT = (
    "📖 About This Bot\n\n"
    "📋 Caption Manager & Post Utility Bot\n\n"
    "This bot helps you bulk-edit captions and manage\n"
    "old posts across your Telegram channels, groups,\n"
    "and forum topics — without doing it one message\n"
    "at a time.\n\n"
    "Two main tools:\n"
    "📂 Caption Manager — edit captions in bulk\n"
    "📮 Post Manager — delete old posts in bulk\n\n"
    "Only one task runs at a time. You can pause,\n"
    "resume, or stop anytime from Job Status."
)

CAPTION_MANAGER_TEXT = (
    "📂 Caption Manager Guide (Channel only)\n\n"
    "Caption Manager edits captions on posts already\n"
    "in your channel — in bulk, across a range you pick.\n\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "🛠 Setup (do this first)\n"
    "━━━━━━━━━━━━━━━━━━━━\n\n"
    "1️⃣ 📡 Configure Channel\n"
    "   Forward any post from your channel to the bot.\n"
    "   Bot must be admin there with Edit Message rights.\n\n"
    "2️⃣ 🎯 Set Processing Range\n"
    "   Forward the first post, then the last post of\n"
    "   the range you want to process.\n\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "🧩 The 3 Features\n"
    "━━━━━━━━━━━━━━━━━━━━\n\n"
    "🔤 Find & Replace\n"
    "   Finds a word/phrase in captions and replaces it\n"
    "   with another. Works on normal words,\n"
    "   → Set your Find word ( which you want to replace) then set Replace word \n\n"
    "🧹 Caption Cleanup\n"
    "   Two independent switches:\n"
    "   • Remove Direct URLs — deletes plain links like\n"
    "     https://..., www...., t.me/..., telegram.me/...\n"
    "   • Remove Hyperlink Formatting — removes Telegram\n"
    "     hyperlinks but keeps the visible text.\n"
    "   → You can use both features at same time \n\n"
    "💉 Caption Injector\n"
    "   Adds your own text to the BOTTOM of every\n"
    "   caption automatically.\n"
    "   → Set the text once, then tap Enable. Turn it\n"
    "   off anytime without losing the saved text.\n\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "✅ Enable ≠ Saved\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "For all 3 features: saving a value only stores it.\n"
    "Nothing runs until you explicitly tap Enable on\n"
    "that feature's screen.\n\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "▶️ Run it\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "Tap ▶️ Preview & Run from the Main Menu. It scans\n"
    "once, shows what will change, then you confirm to\n"
    "start. Pause/Resume/Stop anytime from Job Status."
)

POST_MANAGER_TEXT = (
    "📮 Post Manager Guide (Channel, Group, or Topic)\n\n"
    "1️⃣ 🎯 Configure Target\n"
    "   📡 Channel — forward a post\n"
    "   👥 Group — send /settarget inside the group\n"
    "   🗂️ Topic — send /settarget inside that topic\n\n"
    "2️⃣ 🗂️ Delete Range\n"
    "   Channel: forward first/last post\n"
    "   Group/Topic: paste first/last message link\n\n"
    "3️⃣ 👁️ Preview, then confirm to start deleting."
)

KEEP_ALIVE_SETTINGS_TEXT = (
    "🟢 Keep Alive & ⚙️ Settings\n\n"
    "🟢 Keep Alive\n"
    "🔄 Ping Now — sends a quick test ping\n"
    "📊 Status — shows the last ping result\n\n"
    "⚙️ Settings\n"
    "⏱️ Delay — wait time between messages (1.0–3.0s),\n"
    "shared by Caption Manager and Post Manager\n\n"
    "💬 Commands\n"
    "/cancel — stop whatever you're setting up right now\n"
    "and return to the Main Menu"
)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(
        "❓ Help",
        reply_markup=keyboards.help_menu(),
    )


@router.callback_query(F.data == "menu:help")
async def cb_open_help(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        "❓ Help",
        reply_markup=keyboards.help_menu(),
    )
    await callback.answer()


@router.callback_query(F.data == "help:about")
async def cb_help_about(callback: CallbackQuery) -> None:
    await callback.message.edit_text(ABOUT_TEXT, reply_markup=keyboards.back_to_help())
    await callback.answer()


@router.callback_query(F.data == "help:caption_manager")
async def cb_help_caption_manager(callback: CallbackQuery) -> None:
    await callback.message.edit_text(CAPTION_MANAGER_TEXT, reply_markup=keyboards.back_to_help())
    await callback.answer()


@router.callback_query(F.data == "help:post_manager")
async def cb_help_post_manager(callback: CallbackQuery) -> None:
    await callback.message.edit_text(POST_MANAGER_TEXT, reply_markup=keyboards.back_to_help())
    await callback.answer()


@router.callback_query(F.data == "help:keep_alive_settings")
async def cb_help_keep_alive_settings(callback: CallbackQuery) -> None:
    await callback.message.edit_text(KEEP_ALIVE_SETTINGS_TEXT, reply_markup=keyboards.back_to_help())
    await callback.answer()
