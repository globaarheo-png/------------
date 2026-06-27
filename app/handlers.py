from __future__ import annotations

import logging
from dataclasses import dataclass, field
from html import escape
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, ErrorEvent, Message, TelegramObject

from app.error_reporting import notify_admin_about_error
from app.formatters import format_options, format_recipe
from app.keyboards import options_keyboard, recipe_keyboard
from app.models import DishOption, Recipe
from app.services.gigachat import GigaChatClient
from app.storage.supabase_storage import SupabaseStorage
from app.texts import (
    ASK_PRODUCTS_TEXT,
    HELP_TEXT,
    LOW_PRODUCTS_WARNING,
    MEDICAL_NOTE,
    NO_LAST_REQUEST_TEXT,
    NO_RECIPE_TO_SAVE_TEXT,
    SETTINGS_HELP_TEXT,
    START_TEXT,
)
from app.utils import (
    choose_simplest,
    family_portions_text,
    find_excluded_in_text,
    has_few_products,
    looks_like_food_request,
    parse_settings_text,
)


router = Router()
logger = logging.getLogger(__name__)

USER_ERROR_TEXT = "Сейчас не могу ответить, попробуйте позже"


class ErrorHandlingMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        try:
            return await handler(event, data)
        except Exception as error:
            logger.exception("Unhandled bot handler error")
            await notify_admin_about_error(
                data.get("bot") or getattr(event, "bot", None),
                "Unhandled bot handler error",
                error,
                event=event,
            )
            await answer_user_about_error(event)
            return None


async def answer_user_about_error(event: TelegramObject) -> None:
    try:
        if isinstance(event, Message):
            await event.answer(USER_ERROR_TEXT)
        elif isinstance(event, CallbackQuery):
            await event.answer(USER_ERROR_TEXT, show_alert=True)
    except Exception:
        logger.exception("Failed to send error message to user")


router.message.middleware(ErrorHandlingMiddleware())
router.callback_query.middleware(ErrorHandlingMiddleware())


@router.error()
async def error_handler(event: ErrorEvent, bot: Bot) -> None:
    error = event.exception
    logger.error("Unhandled bot error", exc_info=(type(error), error, error.__traceback__))
    await notify_admin_about_error(bot, "Unhandled bot error", error, event=event)
    await answer_user_about_error(event.update.event)


@dataclass(slots=True)
class UserSession:
    last_query: str | None = None
    current_options: list[DishOption] = field(default_factory=list)
    current_request_id: int | None = None
    current_recipe: Recipe | None = None
    waiting_settings: bool = False


SESSIONS: dict[int, UserSession] = {}


def session_for(user_id: int) -> UserSession:
    return SESSIONS.setdefault(user_id, UserSession())


@router.message(Command("start"))
async def start(message: Message, storage: SupabaseStorage) -> None:
    user = message.from_user
    if user:
        await storage.upsert_user(user.id, user.full_name or user.username)
    await message.answer(START_TEXT)


@router.message(Command("help"))
async def help_command(message: Message) -> None:
    await message.answer(HELP_TEXT)


@router.message(Command("new", "reset"))
async def new_request(message: Message) -> None:
    if message.from_user:
        SESSIONS[message.from_user.id] = UserSession()
    await message.answer(ASK_PRODUCTS_TEXT)


@router.message(Command("settings"))
async def settings_command(message: Message, command: CommandObject, storage: SupabaseStorage) -> None:
    if not message.from_user:
        return
    session = session_for(message.from_user.id)
    text = (command.args or "").strip()
    if not text:
        session.waiting_settings = True
        await message.answer(SETTINGS_HELP_TEXT)
        return

    family_settings = parse_settings_text(text)
    await storage.upsert_user(message.from_user.id, message.from_user.full_name or message.from_user.username)
    await storage.save_family_settings(message.from_user.id, family_settings)
    session.waiting_settings = False
    if storage.enabled:
        await message.answer("Настройки сохранила. Теперь можно написать продукты.")
    else:
        await message.answer(
            "Настройки приняла, но Supabase сейчас недоступен, поэтому после перезапуска я их не вспомню."
        )


@router.message(Command("again"))
async def again_command(message: Message, storage: SupabaseStorage, gigachat: GigaChatClient) -> None:
    if not message.from_user:
        return
    await send_again(message, message.from_user.id, storage, gigachat)


@router.message(Command("favorites"))
async def favorites_command(message: Message, storage: SupabaseStorage) -> None:
    if not message.from_user:
        return
    await send_favorites(message, message.from_user.id, storage)


@router.message(Command("history"))
async def history_command(message: Message, storage: SupabaseStorage) -> None:
    if not message.from_user:
        return
    history = await storage.list_history(message.from_user.id)
    if not history:
        await message.answer("Истории пока нет.")
        return

    parts = ["<b>Последние запросы:</b>"]
    for index, item in enumerate(history, start=1):
        raw_text = escape((item.get("raw_text") or "").strip()[:250])
        status = escape(item.get("status") or "")
        parts.append(f"{index}. {raw_text}\nСтатус: {status}")
    await message.answer("\n\n".join(parts))


@router.callback_query(F.data == "again")
async def again_callback(callback: CallbackQuery, storage: SupabaseStorage, gigachat: GigaChatClient) -> None:
    if callback.from_user:
        await send_again(callback.message, callback.from_user.id, storage, gigachat)
    await callback.answer()


@router.callback_query(F.data == "new")
async def new_callback(callback: CallbackQuery) -> None:
    if callback.from_user:
        SESSIONS[callback.from_user.id] = UserSession()
    if callback.message:
        await callback.message.answer(ASK_PRODUCTS_TEXT)
    await callback.answer()


@router.callback_query(F.data.startswith("favorite:add"))
async def favorite_callback(callback: CallbackQuery, storage: SupabaseStorage) -> None:
    user_id = callback.from_user.id
    session = session_for(user_id)
    await storage.upsert_user(user_id, callback.from_user.full_name or callback.from_user.username)
    request_id = favorite_request_id(callback.data)
    if session.current_recipe and (
        request_id is None or request_id == session.current_request_id
    ):
        is_saved = await storage.add_favorite(
            user_id,
            request_id if request_id is not None else session.current_request_id,
            session.current_recipe,
        )
    else:
        is_saved = await storage.add_favorite_from_request(user_id, request_id)

    if not is_saved:
        if storage.enabled:
            await callback.answer(NO_RECIPE_TO_SAVE_TEXT, show_alert=True)
        else:
            await callback.answer("Не смогла сохранить: избранное сейчас недоступно", show_alert=True)
        return
    await callback.answer("Сохранила в избранное")


@router.callback_query(F.data == "favorites:list")
async def favorites_callback(callback: CallbackQuery, storage: SupabaseStorage) -> None:
    if callback.message:
        await send_favorites(callback.message, callback.from_user.id, storage)
    await callback.answer()


@router.callback_query(F.data.startswith("pick:"))
async def pick_callback(callback: CallbackQuery, storage: SupabaseStorage, gigachat: GigaChatClient) -> None:
    if not callback.message:
        await callback.answer()
        return
    value = (callback.data or "").split(":", 1)[1]
    await pick_option(callback.message, callback.from_user.id, value, storage, gigachat)
    await callback.answer()


def favorite_request_id(callback_data: str | None) -> int | None:
    if not callback_data:
        return None
    parts = callback_data.split(":", 2)
    if len(parts) != 3:
        return None
    try:
        return int(parts[2])
    except ValueError:
        return None


@router.message(F.text)
async def text_message(message: Message, storage: SupabaseStorage, gigachat: GigaChatClient) -> None:
    if not message.from_user or not message.text:
        return

    user_id = message.from_user.id
    session = session_for(user_id)
    text = message.text.strip()

    if session.waiting_settings:
        family_settings = parse_settings_text(text)
        await storage.upsert_user(user_id, message.from_user.full_name or message.from_user.username)
        await storage.save_family_settings(user_id, family_settings)
        session.waiting_settings = False
        if storage.enabled:
            await message.answer("Настройки сохранила. Теперь напиши продукты.")
        else:
            await message.answer(
                "Настройки приняла, но Supabase сейчас недоступен, поэтому после перезапуска я их не вспомню."
            )
        return

    if text in {"1", "2", "3"} and session.current_options:
        await pick_option(message, user_id, text, storage, gigachat)
        return

    if not looks_like_food_request(text):
        await message.answer(ASK_PRODUCTS_TEXT)
        return

    await generate_and_send_options(message, user_id, text, storage, gigachat)


async def send_again(
    message: Message | None,
    user_id: int,
    storage: SupabaseStorage,
    gigachat: GigaChatClient,
) -> None:
    if not message:
        return
    session = session_for(user_id)
    if not session.last_query:
        await message.answer(NO_LAST_REQUEST_TEXT)
        return
    await generate_and_send_options(message, user_id, session.last_query, storage, gigachat)


async def send_favorites(message: Message, user_id: int, storage: SupabaseStorage) -> None:
    favorites = await storage.list_favorites(user_id)
    if not favorites:
        await message.answer("В избранном пока пусто.")
        return

    parts = ["<b>Избранные рецепты:</b>"]
    for index, item in enumerate(favorites, start=1):
        parts.append(f"\n<b>{index}. {escape(item.get('title') or 'Рецепт')}</b>")
        parts.append(escape((item.get("recipe") or "").strip()[:900]))
    await message.answer("\n".join(parts))


async def generate_and_send_options(
    message: Message,
    user_id: int,
    text: str,
    storage: SupabaseStorage,
    gigachat: GigaChatClient,
) -> None:
    session = session_for(user_id)
    family_settings = await storage.get_family_settings(user_id)
    excluded = find_excluded_in_text(text, family_settings)
    warning = None
    if excluded:
        warning = "Не буду использовать: " + ", ".join(excluded) + "."
    elif has_few_products(text):
        warning = LOW_PRODUCTS_WARNING

    thinking_message = await message.answer("Думаю над 3 быстрыми вариантами...")
    try:
        options = await gigachat.generate_options(text, family_settings, excluded)
    except Exception as error:
        logger.exception("Failed to generate dish options with GigaChat")
        await notify_admin_about_error(
            getattr(message, "bot", None),
            "Failed to generate dish options with GigaChat",
            error,
            event=message,
        )
        await thinking_message.edit_text(USER_ERROR_TEXT)
        return

    request_id = await storage.create_food_request(user_id, text, options)
    session.last_query = text
    session.current_options = options
    session.current_request_id = request_id
    session.current_recipe = None

    await thinking_message.edit_text(format_options(options, warning), reply_markup=options_keyboard())


async def pick_option(
    message: Message,
    user_id: int,
    value: str,
    storage: SupabaseStorage,
    gigachat: GigaChatClient,
) -> None:
    session = session_for(user_id)
    if not session.current_options or not session.last_query:
        await message.answer(NO_LAST_REQUEST_TEXT)
        return

    if value == "auto":
        option = choose_simplest(session.current_options)
    else:
        number = int(value)
        option = next((item for item in session.current_options if item.number == number), None)
        if option is None:
            await message.answer("Такого варианта нет. Выбери 1, 2 или 3.")
            return

    thinking_message = await message.answer(f"Готовлю рецепт: {escape(option.title)}...")
    family_settings = await storage.get_family_settings(user_id)
    try:
        recipe = await gigachat.generate_recipe(session.last_query, option, family_settings)
    except Exception as error:
        logger.exception("Failed to generate recipe with GigaChat")
        await notify_admin_about_error(
            getattr(message, "bot", None),
            "Failed to generate recipe with GigaChat",
            error,
            event=message,
        )
        await thinking_message.edit_text(USER_ERROR_TEXT)
        return

    portions_text = family_portions_text(family_settings)
    if portions_text:
        recipe.portions = portions_text

    session.current_recipe = recipe
    await storage.update_selected_recipe(session.current_request_id, option.number, recipe)
    await thinking_message.edit_text(
        f"{format_recipe(recipe)}\n\n<i>{escape(MEDICAL_NOTE)}</i>",
        reply_markup=recipe_keyboard(session.current_request_id),
    )
