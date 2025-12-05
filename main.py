
from __future__ import annotations

import asyncio

from aiogram import Bot, Dispatcher
from aiogram.client.bot import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

from config import settings
from database.db import Database
from database.db_provider import set_db_instance
from handlers import admin, common, student


async def set_commands(bot: Bot) -> None:
    student_commands = [
        BotCommand(command="start", description="Запуск бота"),
        BotCommand(command="topics", description="Доступные темы"),
        BotCommand(command="test", description="Пройти тест"),
        BotCommand(command="materials", description="Материалы"),
        BotCommand(command="stats", description="Моя статистика"),
        BotCommand(command="ask", description="Задать вопрос преподавателю"),
    ]
    await bot.set_my_commands(student_commands)


async def main() -> None:
    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())
    db = Database(settings.database_path)
    await db.setup()
    set_db_instance(db)

    dp.include_router(common.router)
    dp.include_router(student.router)
    dp.include_router(admin.router)

    await set_commands(bot)
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass


