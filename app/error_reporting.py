from __future__ import annotations

import logging
import traceback
from html import escape
from typing import Any

from aiogram import Bot
from aiogram.types import CallbackQuery, Message, Update


logger = logging.getLogger(__name__)

ADMIN_ERROR_CHAT_ID = 1091500563
MAX_ERROR_MESSAGE_LENGTH = 3500


async def notify_admin_about_error(
    bot: Bot | None,
    title: str,
    error: BaseException,
    *,
    event: Any | None = None,
) -> None:
    if bot is None:
        logger.warning("Cannot notify admin about error: bot instance is unavailable")
        return

    text = _format_error_message(title, error, event)
    try:
        await bot.send_message(ADMIN_ERROR_CHAT_ID, text)
    except Exception:
        logger.exception("Failed to send error notification to admin")


def _format_error_message(title: str, error: BaseException, event: Any | None) -> str:
    parts = [
        f"<b>Ошибка бота</b>: {escape(title)}",
        _format_event(event),
        "<b>Traceback:</b>",
        f"<pre>{escape(_traceback_text(error))}</pre>",
    ]
    message = "\n\n".join(part for part in parts if part)
    if len(message) <= MAX_ERROR_MESSAGE_LENGTH:
        return message
    return message[: MAX_ERROR_MESSAGE_LENGTH - 20] + "\n...</pre>"


def _traceback_text(error: BaseException) -> str:
    return "".join(traceback.format_exception(type(error), error, error.__traceback__))


def _format_event(event: Any | None) -> str:
    if event is None:
        return ""

    update = event.update if hasattr(event, "update") else event
    if isinstance(update, Update):
        event_object = update.event
    else:
        event_object = update

    user_parts = _user_parts(event_object)
    text = _event_text(event_object)
    lines = []
    if user_parts:
        lines.append("<b>Пользователь:</b> " + escape(", ".join(user_parts)))
    if text:
        lines.append("<b>Сообщение:</b> " + escape(text[:1000]))
    return "\n".join(lines)


def _user_parts(event_object: Any) -> list[str]:
    user = None
    if isinstance(event_object, Message):
        user = event_object.from_user
    elif isinstance(event_object, CallbackQuery):
        user = event_object.from_user
    if user is None:
        return []

    parts = [f"id={user.id}"]
    if user.username:
        parts.append(f"@{user.username}")
    if user.full_name:
        parts.append(user.full_name)
    return parts


def _event_text(event_object: Any) -> str:
    if isinstance(event_object, Message):
        return event_object.text or event_object.caption or ""
    if isinstance(event_object, CallbackQuery):
        return event_object.data or ""
    return ""
