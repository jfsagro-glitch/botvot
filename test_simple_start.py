"""Простой тест бота с /start"""
import asyncio
import logging
import sys
from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart
from aiogram.types import Message
from core.config import Config

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

async def main():
    """Простой тест"""
    try:
        bot = Bot(token=Config.SALES_BOT_TOKEN)
        dp = Dispatcher()
        
        @dp.message(CommandStart())
        async def start_handler(message: Message):
            logger.info(f"✅ START HANDLER TRIGGERED! User: {message.from_user.id}")
            await message.answer("✅ Тест работает! Обработчик /start сработал!")
        
        @dp.message()
        async def all_messages(message: Message):
            logger.info(f"🔍 Все сообщения: {message.from_user.id} -> {message.text}")
        
        me = await bot.get_me()
        logger.info(f"✅ Бот: @{me.username}")
        logger.info("⏳ Запуск polling...")
        logger.info("Отправьте /start в t.me/StartNowQ_bot")
        
        await dp.start_polling(bot, skip_updates=True)
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}", exc_info=True)
    finally:
        if 'bot' in locals():
            await bot.session.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Остановка...")

