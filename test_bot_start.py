"""Тест запуска бота"""
import asyncio
import logging
import sys
from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart
from aiogram.types import Message
from core.config import Config

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

async def test_simple_bot():
    """Простой тест бота"""
    try:
        logger.info("Инициализация бота...")
        bot = Bot(token=Config.SALES_BOT_TOKEN)
        dp = Dispatcher()
        
        @dp.message(CommandStart())
        async def cmd_start(message: Message):
            logger.info(f"Получена команда /start от {message.from_user.id}")
            await message.answer("✅ Бот работает! Это тестовый ответ.")
        
        logger.info("Проверка подключения...")
        me = await bot.get_me()
        logger.info(f"✅ Бот подключен: @{me.username}")
        
        logger.info("Запуск polling...")
        await dp.start_polling(bot, skip_updates=True)
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}", exc_info=True)
        raise
    finally:
        await bot.session.close()

if __name__ == "__main__":
    try:
        asyncio.run(test_simple_bot())
    except KeyboardInterrupt:
        logger.info("Остановка...")

