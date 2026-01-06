"""Тест регистрации обработчиков"""
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

async def test_handlers():
    """Тест обработчиков"""
    try:
        logger.info("=== ТЕСТ ОБРАБОТЧИКОВ ===")
        
        bot = Bot(token=Config.SALES_BOT_TOKEN)
        dp = Dispatcher()
        
        # Регистрация обработчика
        @dp.message(CommandStart())
        async def handle_start(message: Message):
            logger.info(f"✅ ОБРАБОТЧИК СРАБОТАЛ! User: {message.from_user.id}")
            await message.answer("✅ Тест: Обработчик работает!")
        
        logger.info("✅ Обработчик зарегистрирован")
        
        me = await bot.get_me()
        logger.info(f"✅ Бот подключен: @{me.username}")
        logger.info("⏳ Запуск polling...")
        logger.info("Отправьте /start в t.me/StartNowQ_bot")
        
        await dp.start_polling(bot, skip_updates=True)
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}", exc_info=True)
    finally:
        await bot.session.close()

if __name__ == "__main__":
    try:
        asyncio.run(test_handlers())
    except KeyboardInterrupt:
        logger.info("Остановка...")

