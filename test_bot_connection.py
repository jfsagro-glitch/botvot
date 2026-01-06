"""Быстрый тест подключения бота"""
import asyncio
import logging
from aiogram import Bot
from core.config import Config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def test():
    try:
        bot = Bot(token=Config.SALES_BOT_TOKEN)
        me = await bot.get_me()
        logger.info(f"✅ Бот подключен: @{me.username} ({me.first_name})")
        logger.info(f"   ID: {me.id}")
        
        # Проверяем, что бот может получать обновления
        updates = await bot.get_updates(limit=1)
        logger.info(f"✅ Бот может получать обновления (получено: {len(updates)})")
        
        await bot.session.close()
        return True
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}", exc_info=True)
        return False

if __name__ == "__main__":
    result = asyncio.run(test())
    if result:
        print("\n✅ Бот работает! Отправьте /start в Telegram.")
    else:
        print("\n❌ Проблема с ботом. Проверьте токен и интернет.")

