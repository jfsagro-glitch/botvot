"""Минимальный рабочий бот для теста"""
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
    """Минимальный тест"""
    try:
        logger.info("=== ЗАПУСК МИНИМАЛЬНОГО БОТА ===")
        
        # Создаем бота и диспетчер
        bot = Bot(token=Config.SALES_BOT_TOKEN)
        dp = Dispatcher()
        
        # Регистрируем обработчик /start
        @dp.message(CommandStart())
        async def cmd_start(message: Message):
            logger.info(f"✅✅✅ ОБРАБОТЧИК СРАБОТАЛ! User: {message.from_user.id}")
            await message.answer("✅✅✅ БОТ РАБОТАЕТ! Это тестовый ответ!")
        
        # Проверяем подключение
        me = await bot.get_me()
        logger.info(f"✅ Бот подключен: @{me.username} (ID: {me.id})")
        logger.info(f"✅ Обработчиков зарегистрировано: {len(dp.message.handlers)}")
        logger.info("")
        logger.info("=" * 60)
        logger.info("ОТПРАВЬТЕ /start В TELEGRAM: t.me/StartNowQ_bot")
        logger.info("=" * 60)
        logger.info("")
        
        # Запускаем polling
        await dp.start_polling(bot, skip_updates=True)
        
    except Exception as e:
        logger.error(f"❌ ОШИБКА: {e}", exc_info=True)
    finally:
        if 'bot' in locals():
            await bot.session.close()
            logger.info("Бот остановлен")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Остановка по Ctrl+C...")

