"""Простой тестовый бот для проверки"""
import asyncio
import logging
import sys
from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart
from aiogram.types import Message
from core.config import Config

# Настройка логирования с выводом в консоль
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

bot = Bot(token=Config.SALES_BOT_TOKEN)
dp = Dispatcher()

@dp.message(CommandStart())
async def cmd_start(message: Message):
    logger.info(f"✅ Получена команда /start от пользователя {message.from_user.id}")
    await message.answer("✅ Бот работает! Это тестовый ответ.")

async def main():
    logger.info("=== ТЕСТОВЫЙ БОТ ===")
    try:
        me = await bot.get_me()
        logger.info(f"✅ Бот подключен: @{me.username}")
        logger.info("⏳ Ожидание команд...")
        logger.info("Отправьте /start в Telegram: t.me/StartNowQ_bot")
        await dp.start_polling(bot, skip_updates=True)
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}", exc_info=True)
    finally:
        await bot.session.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Остановка...")

