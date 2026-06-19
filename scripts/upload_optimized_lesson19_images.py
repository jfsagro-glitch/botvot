"""
Скрипт для загрузки оптимизированных картинок уровней урока 19 в Telegram и получения file_id.
"""

import sys
import json
import asyncio
from pathlib import Path
import os

# Добавляем корень проекта в PYTHONPATH
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Устанавливаем UTF-8 для вывода в консоль Windows
if sys.platform == 'win32':
    import codecs
    sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')
    sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer, 'strict')

from core.config import Config
from aiogram import Bot
from aiogram.types import FSInputFile

async def upload_images():
    """Загружает оптимизированные картинки в Telegram и получает file_id."""
    
    # Определяем пути
    optimized_dir = project_root / "Photo" / "video_pic_optimized" / "019 Эмоциональные_уровни_Ocean_of_emotion"
    lesson19_images_file = project_root / "data" / "lesson19_images.json"
    
    print("=" * 70)
    print("📤 Загрузка оптимизированных картинок урока 19 в Telegram")
    print("=" * 70)
    print()
    
    if not optimized_dir.exists():
        print(f"❌ Директория {optimized_dir} не существует!")
        return
    
    # Загружаем существующие данные
    if lesson19_images_file.exists():
        with open(lesson19_images_file, 'r', encoding='utf-8') as f:
            images_data = json.load(f)
    else:
        print("❌ Файл lesson19_images.json не найден!")
        return
    
    # Инициализируем бота
    bot = Bot(token=Config.COURSE_BOT_TOKEN)
    
    print(f"📂 Директория: {optimized_dir}")
    print(f"📊 Всего картинок: {len(images_data)}")
    print()
    
    uploaded_count = 0
    failed_count = 0
    
    try:
        for image_data in images_data:
            number = image_data.get("number", 0)
            filename = image_data.get("filename", "")
            file_id = image_data.get("file_id")
            
            # Пропускаем, если уже есть file_id
            if file_id:
                print(f"[{number}] ⏭️  Пропускаю {filename} (уже есть file_id)")
                continue
            
            # Ищем файл
            file_path = optimized_dir / filename
            if not file_path.exists():
                # Пробуем оригинальный путь
                original_path = image_data.get("path", "")
                if original_path:
                    original_file = project_root / original_path.replace('/', os.sep)
                    if original_file.exists():
                        file_path = original_file
                    else:
                        print(f"[{number}] ❌ Файл не найден: {filename}")
                        failed_count += 1
                        continue
                else:
                    print(f"[{number}] ❌ Файл не найден: {filename}")
                    failed_count += 1
                    continue
            
            print(f"[{number}] 📤 Загружаю {filename}...")
            
            # Загружаем файл с повторными попытками
            max_retries = 3
            uploaded = False
            
            for attempt in range(max_retries):
                try:
                    photo_file = FSInputFile(file_path)
                    message = await bot.send_photo(
                        Config.ADMIN_CHAT_ID,
                        photo_file
                    )
                    
                    # Получаем file_id
                    if message.photo:
                        file_id = message.photo[-1].file_id
                        image_data["file_id"] = file_id
                        image_data["path"] = f"Photo/video_pic_optimized/019 Эмоциональные_уровни_Ocean_of_emotion/{filename}"
                        uploaded_count += 1
                        uploaded = True
                        print(f"   ✅ Загружено! file_id: {file_id[:50]}...")
                        break
                except Exception as e:
                    if attempt < max_retries - 1:
                        print(f"   ⚠️  Попытка {attempt + 1}/{max_retries} не удалась: {e}, повторяю...")
                        await asyncio.sleep(2)
                    else:
                        print(f"   ❌ Ошибка после {max_retries} попыток: {e}")
                        failed_count += 1
            
            # Небольшая пауза между загрузками
            if uploaded:
                await asyncio.sleep(0.5)
        
        # Сохраняем обновленные данные
        with open(lesson19_images_file, 'w', encoding='utf-8') as f:
            json.dump(images_data, f, ensure_ascii=False, indent=2)
        
        print()
        print("=" * 70)
        print(f"✅ Готово! Загружено {uploaded_count} картинок")
        if failed_count > 0:
            print(f"❌ Не удалось загрузить {failed_count} картинок")
        print("=" * 70)
        
    finally:
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(upload_images())
