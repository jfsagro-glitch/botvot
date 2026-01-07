"""
Запуск обоих ботов в одном процессе.

Используется для деплоя на платформах, где удобнее запускать один процесс.
"""

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


async def main():
    """Запуск обоих ботов."""
    if not Config.validate():
        logger.error("❌ Неверная конфигурация. Проверьте файл .env")
        return
    
    sales_bot = None
    course_bot = None
    
    try:
        logger.info("=" * 60)
        logger.info("🚀 Запуск платформы курсов")
        logger.info("=" * 60)
        
        # Инициализация ботов
        logger.info("Инициализация продающего бота...")
        sales_bot = SalesBot()
        
        logger.info("Инициализация курс-бота...")
        course_bot = CourseBot()
        
        # Запуск ботов параллельно
        logger.info("Запуск ботов...")
        await asyncio.gather(
            sales_bot.start(),
            course_bot.start()
        )
    except KeyboardInterrupt:
        logger.info("Остановка ботов...")
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}", exc_info=True)
    finally:
        if sales_bot:
            try:
                await sales_bot.stop()
            except Exception as e:
                logger.error(f"Ошибка при остановке продающего бота: {e}")
        
        if course_bot:
            try:
                await course_bot.stop()
            except Exception as e:
                logger.error(f"Ошибка при остановке курс-бота: {e}")
        
        logger.info("Боты остановлены")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Завершение работы...")

