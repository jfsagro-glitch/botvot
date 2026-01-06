"""Минимальный тест бота для проверки работы"""
import asyncio
import logging
import sys
from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart
from aiogram.types import Message
from core.config import Config

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

async def main():
    logger.info("=== МИНИМАЛЬНЫЙ ТЕСТ БОТА ===")
    
    try:
        # Создаем бота и диспетчер
        bot = Bot(token=Config.SALES_BOT_TOKEN)
        dp = Dispatcher()
        
        # Регистрируем обработчик
        @dp.message(CommandStart())
        async def cmd_start(message: Message):
            logger.info(f"✅ ПОЛУЧЕНА КОМАНДА /start от {message.from_user.id}")
            await message.answer("✅ Бот работает! Команда /start обработана.")
        
        # Проверяем подключение
        me = await bot.get_me()
        logger.info(f"✅ Бот подключен: @{me.username}")
        logger.info("⏳ Запуск polling...")
        logger.info("Отправьте /start в t.me/StartNowQ_bot")
        
        # Запускаем polling
        await dp.start_polling(bot, skip_updates=True)
        
    except Exception as e:
        logger.error(f"❌ ОШИБКА: {e}", exc_info=True)
    finally:
        try:
            await bot.session.close()
        except:
            pass

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Остановка...")

