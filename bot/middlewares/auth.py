"""
Owner-or-authorized-user middleware.

The owner (OWNER_ID) always has access. Additionally, any user explicitly
granted access via /addauth (stored in the authorized_users table) is also
let through. Everyone else is blocked -- for a /start command specifically,
the bot replies with a short "private bot" message instead of staying
silent; for every other update, it's dropped silently (matches prior
behavior for non-/start interactions, e.g. accidental button taps from
someone who was never granted access).
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update

from db import queries
from db.connection import get_pool

logger = logging.getLogger(__name__)

PRIVATE_BOT_MESSAGE = (
    "🔒 This bot is private.\n\n"
    "You don't have access yet. Contact the owner if you'd like to use this bot."
)


class OwnerOnlyMiddleware(BaseMiddleware):
    """
    Lets through updates from the owner or from any user in the
    authorized_users allow-list. Drops everyone else.
    """

    def __init__(self, owner_id: int) -> None:
        super().__init__()
        self._owner_id = owner_id

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")

        if user is None:
            # No identifiable user on this update (e.g. a channel post update
            # not triggered by a person) -- ignore silently.
            return None

        if user.id == self._owner_id:
            return await handler(event, data)

        pool = get_pool()
        authorized = await queries.is_authorized(pool, user.id)

        if authorized:
            # Opportunistically backfill display name if it was missing
            # (e.g. the owner ran /addauth by ID before this user ever
            # messaged the bot).
            display_name = getattr(user, "full_name", None) or user.first_name
            if display_name:
                await queries.update_authorized_display_name(pool, user.id, display_name)
            return await handler(event, data)

        logger.warning("Ignoring update from unauthorized user_id=%s", user.id)

        # For a /start command specifically, let the person know the bot is
        # private instead of appearing completely unresponsive.
        incoming_message = event.message if isinstance(event, Update) else None
        if incoming_message is not None and (incoming_message.text or "").strip().lower().startswith("/start"):
            try:
                await incoming_message.answer(PRIVATE_BOT_MESSAGE)
            except Exception:
                logger.exception("Failed to send private-bot message to unauthorized user_id=%s", user.id)

        return None
