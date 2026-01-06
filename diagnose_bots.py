"""Диагностика ботов"""
import asyncio
import logging
import sys

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

async def diagnose():
    """Диагностика системы"""
    try:
        logger.info("=== ДИАГНОСТИКА БОТОВ ===")
        
        # 1. Проверка конфигурации
        logger.info("1. Проверка конфигурации...")
        from core.config import Config
        if not Config.validate():
            logger.error("❌ Конфигурация невалидна!")
            return
        logger.info("✅ Конфигурация OK")
        
        # 2. Проверка токенов
        logger.info("2. Проверка токенов...")
        logger.info(f"   SALES_BOT_TOKEN: {'✅' if Config.SALES_BOT_TOKEN else '❌'}")
        logger.info(f"   COURSE_BOT_TOKEN: {'✅' if Config.COURSE_BOT_TOKEN else '❌'}")
        logger.info(f"   ADMIN_CHAT_ID: {Config.ADMIN_CHAT_ID}")
        
        # 3. Проверка подключения к боту
        logger.info("3. Проверка подключения к Sales Bot...")
        from aiogram import Bot
        bot = Bot(token=Config.SALES_BOT_TOKEN)
        try:
            me = await bot.get_me()
            logger.info(f"✅ Бот подключен: @{me.username} ({me.first_name})")
        except Exception as e:
            logger.error(f"❌ Ошибка подключения: {e}")
            return
        finally:
            await bot.session.close()
        
        # 4. Проверка базы данных
        logger.info("4. Проверка базы данных...")
        from core.database import Database
        db = Database()
        try:
            await db.connect()
            logger.info("✅ База данных подключена")
            await db.close()
        except Exception as e:
            logger.error(f"❌ Ошибка БД: {e}")
        
        logger.info("\n=== ДИАГНОСТИКА ЗАВЕРШЕНА ===")
        logger.info("Если все проверки прошли, боты должны работать.")
        logger.info("Запустите: python -m bots.sales_bot")
        
    except Exception as e:
        logger.error(f"❌ Критическая ошибка: {e}", exc_info=True)

if __name__ == "__main__":
    asyncio.run(diagnose())

