from __future__ import annotations

import asyncio
from contextlib import contextmanager
import logging
import os
from pathlib import Path
import sys
from typing import BinaryIO, Iterator

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from app.config import load_settings
from app.handlers import router
from app.services.gigachat import GigaChatClient
from app.storage.supabase_storage import SupabaseStorage


@contextmanager
def single_instance_lock(lock_path: Path) -> Iterator[None]:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = lock_path.open("a+b")
    try:
        _lock_file(lock_file)
        yield
    finally:
        _unlock_file(lock_file)
        lock_file.close()


def _lock_file(lock_file: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt

        try:
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            raise RuntimeError("Another bot instance is already running.") from error
        return

    import fcntl

    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as error:
        raise RuntimeError("Another bot instance is already running.") from error


def _unlock_file(lock_file: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt

        try:
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        return

    import fcntl

    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)
    settings = load_settings()

    try:
        with single_instance_lock(Path("data") / "bot.lock"):
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
    except RuntimeError as error:
        logger.error("%s Stop the duplicate process or wait for it to exit.", error)


if __name__ == "__main__":
    asyncio.run(main())
