"""Тест регистрации обработчиков"""
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

async def main():
    bot = Bot(token=Config.SALES_BOT_TOKEN)
    dp = Dispatcher()
    
    @dp.message(CommandStart())
    async def cmd_start(message: Message):
        logger.info(f"✅ HANDLER ВЫЗВАН! User: {message.from_user.id}")
        await message.answer("✅ БОТ РАБОТАЕТ! Обработчик сработал!")
    
    me = await bot.get_me()
    logger.info(f"✅ Бот: @{me.username}")
    logger.info("⏳ Polling запущен. Отправьте /start в t.me/StartNowQ_bot")
    
    try:
        await dp.start_polling(bot, skip_updates=True)
    except KeyboardInterrupt:
        logger.info("Остановка...")
    finally:
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())

