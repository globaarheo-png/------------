from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def options_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="1", callback_data="pick:1"),
                InlineKeyboardButton(text="2", callback_data="pick:2"),
                InlineKeyboardButton(text="3", callback_data="pick:3"),
            ],
            [InlineKeyboardButton(text="Реши за меня", callback_data="pick:auto")],
        ]
    )


def recipe_keyboard(request_id: int | None = None) -> InlineKeyboardMarkup:
    favorite_callback = "favorite:add"
    if request_id is not None:
        favorite_callback = f"favorite:add:{request_id}"

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="В избранное", callback_data=favorite_callback)],
            [InlineKeyboardButton(text="Мои любимые рецепты", callback_data="favorites:list")],
            [InlineKeyboardButton(text="Еще варианты", callback_data="again")],
            [InlineKeyboardButton(text="Новый запрос", callback_data="new")],
        ]
    )
