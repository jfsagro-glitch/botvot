"""Тестовый скрипт для проверки работы бота"""
import asyncio
import logging
from aiogram import Bot
from core.config import Config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def test_bot():
    """Тест подключения к боту"""
    try:
        bot = Bot(token=Config.SALES_BOT_TOKEN)
        me = await bot.get_me()
        logger.info(f"Бот подключен: @{me.username} ({me.first_name})")
        await bot.session.close()
        return True
    except Exception as e:
        logger.error(f"Ошибка подключения: {e}")
        return False

if __name__ == "__main__":
    result = asyncio.run(test_bot())
    if result:
        print("\n✅ Бот работает! Теперь запустите: python -m bots.sales_bot")
    else:
        print("\n❌ Ошибка подключения. Проверьте токен в .env")

