"""
Скрипт для загрузки фото для урока 30 и получения file_id.

Инструкция:
1. Отправьте фото в бот курса (@YourCourseBot)
2. Скопируйте file_id из логов или используйте этот скрипт
3. Обновите intro_photo_file_id в data/lessons.json
"""

import asyncio
import logging
from aiogram import Bot
from aiogram.types import FSInputFile
from pathlib import Path
from core.config import Config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def upload_photo():
    """Загружает фото и получает file_id."""
    bot = Bot(token=Config.COURSE_BOT_TOKEN)
    
    # Путь к фото (поместите фото в папку проекта)
    photo_path = Path("lesson_30_photo.jpg")
    
    if not photo_path.exists():
        logger.error(f"Фото не найдено: {photo_path}")
        logger.info("Поместите фото в корень проекта с именем 'lesson_30_photo.jpg'")
        return
    
    try:
        # Отправляем фото себе (замените на ваш chat_id)
        admin_chat_id = Config.ADMIN_CHAT_ID
        if not admin_chat_id:
            logger.error("ADMIN_CHAT_ID не настроен в .env")
            return
        
        photo_file = FSInputFile(photo_path)
        message = await bot.send_photo(admin_chat_id, photo_file)
        
        # Получаем file_id
        file_id = message.photo[-1].file_id
        logger.info(f"✅ Фото загружено!")
        logger.info(f"📋 file_id: {file_id}")
        logger.info(f"\nОбновите intro_photo_file_id в data/lessons.json для урока 30:")
        logger.info(f'  "intro_photo_file_id": "{file_id}"')
        
    except Exception as e:
        logger.error(f"❌ Ошибка при загрузке фото: {e}")
    finally:
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(upload_photo())
