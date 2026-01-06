"""Тест ответа бота на команду"""
import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart
from aiogram.types import Message
from core.config import Config

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

bot = Bot(token=Config.SALES_BOT_TOKEN)
dp = Dispatcher()

@dp.message(CommandStart())
async def cmd_start(message: Message):
    logger.info(f"✅ Получена команда /start от пользователя {message.from_user.id}")
    await message.answer("✅ Тест: Бот получает команды и отвечает!")

async def main():
    logger.info("🚀 Запуск тестового бота...")
    try:
        me = await bot.get_me()
        logger.info(f"✅ Бот подключен: @{me.username}")
        logger.info("⏳ Ожидание команд...")
        await dp.start_polling(bot, skip_updates=True)
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}", exc_info=True)
    finally:
        await bot.session.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Остановка бота...")

