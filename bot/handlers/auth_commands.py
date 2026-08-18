"""
Owner-only commands for managing the authorized-users allow-list:
/addauth, /removeauth, /listauth.

These commands are owner-only regardless of who else is in the allow-list
-- only the real owner (OWNER_ID) may grant or revoke access, matching the
existing owner-only pattern used for other admin-style commands in this
project (e.g. /settarget's admin-rights requirement).
"""

from __future__ import annotations

from aiogram import Bot, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from db import queries
from db.connection import get_pool

router = Router(name="auth_commands")


def _is_owner(message: Message, owner_id: int) -> bool:
    return message.from_user is not None and message.from_user.id == owner_id


@router.message(Command("addauth"))
async def cmd_addauth(message: Message, command: CommandObject, bot: Bot, owner_id: int) -> None:
    if not _is_owner(message, owner_id):
        return  # non-owner: silently ignore, same as any other owner-only command

    args = (command.args or "").strip()
    if not args:
        await message.answer("Usage: /addauth user_id")
        return

    try:
        user_id = int(args.split()[0])
    except ValueError:
        await message.answer("Please provide a valid numeric user ID.\nUsage: /addauth user_id")
        return

    pool = get_pool()
    await queries.add_authorized_user(pool, user_id, display_name=None)

    await message.answer(f"✅ User {user_id} added to authorized users.")

    try:
        await bot.send_message(
            chat_id=user_id,
            text="✅ You've been granted access to this bot.\n\nSend /start to begin.",
        )
    except Exception:
        pass  # user may not have started a chat with the bot yet -- non-fatal


@router.message(Command("removeauth"))
async def cmd_removeauth(message: Message, command: CommandObject, bot: Bot, owner_id: int) -> None:
    if not _is_owner(message, owner_id):
        return

    args = (command.args or "").strip()
    if not args:
        await message.answer("Usage: /removeauth user_id")
        return

    try:
        user_id = int(args.split()[0])
    except ValueError:
        await message.answer("Please provide a valid numeric user ID.\nUsage: /removeauth user_id")
        return

    pool = get_pool()
    removed = await queries.remove_authorized_user(pool, user_id)

    if not removed:
        await message.answer(f"User {user_id} was not in the authorized list.")
        return

    await message.answer(f"✅ User {user_id} removed from authorized users.")

    try:
        await bot.send_message(
            chat_id=user_id,
            text="🔒 Your access to this bot has been removed.",
        )
    except Exception:
        pass  # user may have blocked the bot -- non-fatal


@router.message(Command("listauth"))
async def cmd_listauth(message: Message, bot: Bot, owner_id: int) -> None:
    if not _is_owner(message, owner_id):
        return

    pool = get_pool()
    users = await queries.list_authorized_users(pool)

    if not users:
        await message.answer("👥 Authorized Users (0)\n\nNo users authorized yet. Use /addauth user_id.")
        return

    lines = [f"👥 Authorized Users ({len(users)})", ""]
    for i, u in enumerate(users, start=1):
        name = u.display_name or "Unknown"
        lines.append(f"{i}. {name} ({u.user_id})")

    await message.answer("\n".join(lines))
