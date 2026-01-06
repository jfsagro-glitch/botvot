"""Прямой тест обработчика /start"""
import asyncio
import logging
import sys
from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart
from aiogram.types import Message
from core.config import Config

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

async def handle_start(message: Message):
    """Обработчик /start"""
    logger.info("=" * 60)
    logger.info("✅✅✅ HANDLE_START ВЫЗВАН! ✅✅✅")
    logger.info(f"   User: {message.from_user.id}")
    logger.info(f"   Text: {message.text}")
    logger.info("=" * 60)
    await message.answer("✅ БОТ РАБОТАЕТ! Обработчик /start сработал!")

async def main():
    """Главная функция"""
    try:
        logger.info("=== ТЕСТ ПРЯМОГО ОБРАБОТЧИКА ===")
        
        bot = Bot(token=Config.SALES_BOT_TOKEN)
        dp = Dispatcher()
        
        # Регистрация через register
        dp.message.register(handle_start, CommandStart())
        
        logger.info(f"✅ Обработчиков: {len(dp.message.handlers)}")
        
        me = await bot.get_me()
        logger.info(f"✅ Бот: @{me.username}")
        logger.info("")
        logger.info("=" * 60)
        logger.info("ОТПРАВЬТЕ /start В: t.me/StartNowQ_bot")
        logger.info("=" * 60)
        logger.info("")
        
        await dp.start_polling(bot, skip_updates=True)
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}", exc_info=True)
        import traceback
        traceback.print_exc()
    finally:
        if 'bot' in locals():
            await bot.session.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Остановка...")

