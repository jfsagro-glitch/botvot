"""Проверка статуса ботов"""
import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart
from aiogram.types import Message
from core.config import Config

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

async def check_bot():
    """Проверить работу бота"""
    try:
        logger.info("=== ПРОВЕРКА БОТА ===")
        
        # 1. Подключение
        bot = Bot(token=Config.SALES_BOT_TOKEN)
        me = await bot.get_me()
        logger.info(f"✅ Бот подключен: @{me.username} ({me.first_name})")
        
        # 2. Проверка обновлений
        updates = await bot.get_updates(limit=1)
        logger.info(f"✅ Обновления доступны (получено: {len(updates)})")
        
        # 3. Тест обработчика
        dp = Dispatcher()
        
        @dp.message(CommandStart())
        async def test_start(message: Message):
            logger.info(f"✅ Получена команда /start от {message.from_user.id}")
            await message.answer("✅ Тест: Бот получает и обрабатывает команды!")
        
        logger.info("✅ Обработчики зарегистрированы")
        logger.info("⏳ Запуск polling на 10 секунд для теста...")
        
        # Запускаем на короткое время для теста
        import signal
        import sys
        
        async def run_test():
            try:
                await dp.start_polling(bot, skip_updates=True)
            except asyncio.CancelledError:
                pass
        
        task = asyncio.create_task(run_test())
        await asyncio.sleep(10)
        task.cancel()
        
        logger.info("✅ Тест завершен")
        await bot.session.close()
        
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}", exc_info=True)

if __name__ == "__main__":
    try:
        asyncio.run(check_bot())
    except KeyboardInterrupt:
        logger.info("Остановка...")

