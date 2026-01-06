"""Простой тестовый бот для проверки работы"""
import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart
from aiogram.types import Message
from core.config import Config

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

bot = Bot(token=Config.SALES_BOT_TOKEN)
dp = Dispatcher()

@dp.message(CommandStart())
async def cmd_start(message: Message):
    logger.info(f"Received /start from {message.from_user.id}")
    await message.answer("Привет! Бот работает!")

async def main():
    logger.info("Тестовый бот запускается...")
    try:
        me = await bot.get_me()
        logger.info(f"Бот подключен: @{me.username}")
        await dp.start_polling(bot, skip_updates=True)
    except Exception as e:
        logger.error(f"Ошибка: {e}", exc_info=True)
    finally:
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())

