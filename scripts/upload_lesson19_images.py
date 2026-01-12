"""
Скрипт для загрузки картинок урока 19 в Telegram и получения file_id
"""
import sys
import json
import asyncio
from pathlib import Path
from aiogram import Bot
from aiogram.types import FSInputFile

# Добавляем корень проекта в путь
sys.path.append(str(Path(__file__).resolve().parent.parent))

from core.config import Config

# Настройка кодировки для Windows
if sys.platform == 'win32':
    import codecs
    sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')
    sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer, 'strict')

async def upload_images():
    """Загружает картинки урока 19 в Telegram и получает file_id"""
    bot = Bot(token=Config.COURSE_BOT_TOKEN)
    admin_chat_id = Config.ADMIN_CHAT_ID
    
    # Путь к директории с картинками
    images_dir = Path(r"C:\Users\79184.WIN-OOR1JAM5834\BOTVOT\Photo\video_pic\019 Эмоциональные_уровни_Ocean_of_emotion")
    
    # Получаем все файлы картинок
    image_files = sorted(images_dir.glob("*.jpg"))
    
    print(f"Найдено {len(image_files)} картинок")
    
    images_data = []
    
    for i, image_file in enumerate(image_files, 1):
        print(f"\n[{i}/{len(image_files)}] Загружаю {image_file.name}...")
        
        # Пробуем загрузить с повторными попытками
        max_retries = 3
        file_id = None
        
        for attempt in range(max_retries):
            try:
                # Отправляем фото в админ-чат
                photo_file = FSInputFile(image_file)
                sent_message = await bot.send_photo(
                    chat_id=admin_chat_id,
                    photo=photo_file,
                    caption=f"Уровень {i-1} из урока 19"
                )
                
                # Получаем file_id
                file_id = sent_message.photo[-1].file_id  # Берем самое большое фото
                break  # Успешно загружено
                
            except Exception as e:
                if attempt < max_retries - 1:
                    print(f"   ⚠️ Попытка {attempt + 1}/{max_retries} не удалась, повторяю через 2 секунды...")
                    await asyncio.sleep(2)
                else:
                    print(f"❌ Ошибка при загрузке {image_file.name} после {max_retries} попыток: {e}")
        
        images_data.append({
            "number": i - 1,  # Номер от 0
            "filename": image_file.name,
            "path": f"Photo/video_pic/019 Эмоциональные_уровни_Ocean_of_emotion/{image_file.name}",
            "file_id": file_id
        })
        
        if file_id:
            print(f"✅ Загружено: {image_file.name}")
            print(f"   file_id: {file_id}")
        else:
            print(f"⚠️ Не удалось загрузить: {image_file.name}")
        
        # Небольшая пауза между загрузками
        await asyncio.sleep(1)
    
    # Сохраняем данные в JSON файл
    output_file = Path(__file__).parent.parent / "data" / "lesson19_images.json"
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(images_data, f, ensure_ascii=False, indent=2)
    
    print(f"\n✅ Данные сохранены в {output_file}")
    print(f"\nВсего картинок: {len(images_data)}")
    print(f"Успешно загружено: {sum(1 for img in images_data if img['file_id'])}")
    
    await bot.session.close()

if __name__ == "__main__":
    asyncio.run(upload_images())
