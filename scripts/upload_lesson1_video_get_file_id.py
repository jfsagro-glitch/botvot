"""
Скрипт для загрузки обработанного видео урока 1 в Telegram и получения file_id.
"""

import asyncio
import json
import sys
from pathlib import Path
from aiogram import Bot
from aiogram.types import FSInputFile

# Добавляем корень проекта в PYTHONPATH
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from core.config import Config

# Устанавливаем UTF-8 для вывода в консоль Windows
if sys.platform == 'win32':
    import codecs
    sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')
    sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer, 'strict')

async def upload_video_and_get_file_id():
    """Загружает видео в Telegram и получает file_id."""
    video_path = project_root / "Photo" / "video_pic_optimized" / "001 Корвет.mp4"
    
    if not video_path.exists():
        print(f"❌ Видео не найдено: {video_path}")
        return
    
    print("=" * 70)
    print("📤 Загрузка видео урока 1 в Telegram")
    print("=" * 70)
    print()
    print(f"📹 Файл: {video_path}")
    print(f"📊 Размер файла: {video_path.stat().st_size / (1024*1024):.2f} MB")
    print()
    
    bot = Bot(token=Config.COURSE_BOT_TOKEN)
    admin_chat_id = Config.ADMIN_CHAT_ID
    
    if not admin_chat_id:
        print("❌ ADMIN_CHAT_ID не указан в конфигурации!")
        return
    
    try:
        print(f"📤 Отправка видео в чат {admin_chat_id}...")
        video_file = FSInputFile(video_path)
        
        # Отправляем видео с увеличенным таймаутом и повторными попытками
        max_retries = 3
        message = None
        
        for attempt in range(1, max_retries + 1):
            try:
                print(f"   Попытка {attempt}/{max_retries}...")
                # Отправляем видео с увеличенным таймаутом (300 секунд для больших файлов)
                message = await bot.send_video(
                    chat_id=admin_chat_id,
                    video=video_file,
                    caption="Тест: видео урока 1 (960x600)",
                    request_timeout=300.0  # 5 минут
                )
                break
            except Exception as e:
                if attempt < max_retries:
                    wait_time = attempt * 10
                    print(f"   ⚠️  Попытка {attempt} не удалась: {e}")
                    print(f"   ⏳ Ожидание {wait_time} секунд перед повторной попыткой...")
                    await asyncio.sleep(wait_time)
                else:
                    raise
        
        if not message:
            print(f"❌ Не удалось отправить видео после {max_retries} попыток")
            return
        
        # Получаем file_id
        if message.video:
            file_id = message.video.file_id
            print(f"✅ Видео успешно отправлено!")
            print(f"📋 file_id: {file_id}")
            print()
            
            # Обновляем lessons.json
            lessons_file = project_root / "data" / "lessons.json"
            with open(lessons_file, 'r', encoding='utf-8') as f:
                lessons = json.load(f)
            
            lesson_1 = lessons.get("1", {})
            media_list = lesson_1.get("media", [])
            
            if media_list and len(media_list) > 0:
                # Обновляем file_id первого медиа (видео)
                media_list[0]["file_id"] = file_id
                lesson_1["media"] = media_list
                lessons["1"] = lesson_1
                
                # Создаем резервную копию
                backup_file = lessons_file.with_suffix('.json.backup_video1')
                with open(backup_file, 'w', encoding='utf-8') as f:
                    json.dump(lessons, f, ensure_ascii=False, indent=2)
                print(f"💾 Создана резервная копия: {backup_file}")
                
                # Сохраняем обновленный файл
                with open(lessons_file, 'w', encoding='utf-8') as f:
                    json.dump(lessons, f, ensure_ascii=False, indent=2)
                
                print(f"✅ file_id обновлен в lessons.json")
            else:
                print(f"⚠️  Медиа не найдено в уроке 1")
        else:
            print(f"❌ Не удалось получить file_id из сообщения")
        
    except Exception as e:
        print(f"❌ Ошибка при загрузке видео: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await bot.session.close()
    
    print()
    print("=" * 70)

if __name__ == "__main__":
    asyncio.run(upload_video_and_get_file_id())
