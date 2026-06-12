from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from app.config import load_settings
from app.handlers import router
from app.services.gigachat import GigaChatClient
from app.storage.supabase_storage import SupabaseStorage


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)
    settings = load_settings()

    bot = Bot(
        token=settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()
    storage = SupabaseStorage(settings)
    logger.info("Python executable: %s", sys.executable)
    logger.info("Supabase storage: %s", "enabled" if storage.enabled else "disabled")
    dp["settings"] = settings
    dp["storage"] = storage
    dp["gigachat"] = GigaChatClient(settings)
    dp.include_router(router)

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
