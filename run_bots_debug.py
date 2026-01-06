"""Запуск ботов с отладкой"""
import asyncio
import logging
import sys
from bots.sales_bot import SalesBot
from bots.course_bot import CourseBot
from core.config import Config

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

async def run_sales_bot():
    """Запустить Sales Bot"""
    try:
        logger.info("=== ЗАПУСК SALES BOT ===")
        if not Config.validate():
            logger.error("❌ Невалидная конфигурация!")
            return
        
        bot = SalesBot()
        logger.info("✅ Sales Bot инициализирован")
        await bot.start()
    except Exception as e:
        logger.error(f"❌ Ошибка Sales Bot: {e}", exc_info=True)

async def run_course_bot():
    """Запустить Course Bot"""
    try:
        logger.info("=== ЗАПУСК COURSE BOT ===")
        if not Config.validate():
            logger.error("❌ Невалидная конфигурация!")
            return
        
        bot = CourseBot()
        logger.info("✅ Course Bot инициализирован")
        await bot.start()
    except Exception as e:
        logger.error(f"❌ Ошибка Course Bot: {e}", exc_info=True)

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "course":
        asyncio.run(run_course_bot())
    else:
        asyncio.run(run_sales_bot())

