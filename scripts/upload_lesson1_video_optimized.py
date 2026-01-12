"""
Оптимизированный скрипт для загрузки нового видео урока 1 в Telegram и получения file_id.
Использует retry логику и оптимизированные параметры для быстрой загрузки.
"""

import sys
import asyncio
from pathlib import Path
import json
import shutil

# Настройка кодировки для Windows
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

# Добавляем корень проекта в путь
project_root = Path(__file__).resolve().parent.parent
sys.path.append(str(project_root))

from core.config import Config
from aiogram import Bot
from aiogram.types import FSInputFile
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

async def upload_video_with_retry(bot: Bot, chat_id: int, video_path: Path, 
                                  caption: str = None, max_retries: int = 3):
    """Загружает видео с повторными попытками."""
    for attempt in range(max_retries):
        try:
            print(f"📤 Попытка {attempt + 1}/{max_retries}...")
            video_file = FSInputFile(video_path)
            
            # Увеличиваем таймаут для больших файлов
            request_timeout = 300 if attempt == 0 else 600
            
            message = await bot.send_video(
                chat_id=chat_id,
                video=video_file,
                caption=caption,
                width=1080,
                height=606,
                supports_streaming=True,
                request_timeout=request_timeout
            )
            
            if message.video:
                return message.video.file_id
            else:
                raise ValueError("Не удалось получить video из сообщения")
                
        except Exception as e:
            if attempt < max_retries - 1:
                delay = (attempt + 1) * 5
                print(f"⚠️ Ошибка (попытка {attempt + 1}): {e}")
                print(f"🔄 Повтор через {delay} секунд...")
                await asyncio.sleep(delay)
            else:
                print(f"❌ Не удалось загрузить после {max_retries} попыток: {e}")
                raise

async def main():
    """Основная функция."""
    print("=" * 60)
    print("📤 ОПТИМИЗИРОВАННАЯ ЗАГРУЗКА ВИДЕО УРОКА 1")
    print("=" * 60)
    
    # Инициализируем бота
    bot = Bot(
        token=Config.COURSE_BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    
    # Путь к новому видео
    new_video_path = project_root / "Photo" / "video_pic_optimized" / "001 Корвет_fullwidth.mp4"
    original_video_path = project_root / "Photo" / "video_pic_optimized" / "001 Корвет.mp4"
    backup_video_path = project_root / "Photo" / "video_pic_optimized" / "001 Корвет_backup.mp4"
    
    if not new_video_path.exists():
        print(f"❌ Файл не найден: {new_video_path}")
        return False
    
    admin_chat_id = Config.ADMIN_CHAT_ID
    
    file_size_mb = new_video_path.stat().st_size / 1024 / 1024
    print(f"📹 Видео: {new_video_path.name}")
    print(f"📊 Размер: {file_size_mb:.2f} MB")
    print(f"💬 Отправка в чат: {admin_chat_id}")
    print()
    
    try:
        # Загружаем видео с retry логикой
        file_id = await upload_video_with_retry(
            bot,
            admin_chat_id,
            new_video_path,
            caption="🎬 Видео урока 1 (оптимизировано для полной ширины экрана)"
        )
        
        print(f"\n✅ Видео успешно загружено!")
        print(f"📋 file_id: {file_id}")
        
        # Обновляем lessons.json
        lessons_json_path = project_root / "data" / "lessons.json"
        print(f"\n📝 Обновляю lessons.json...")
        
        with open(lessons_json_path, 'r', encoding='utf-8') as f:
            lessons_data = json.load(f)
        
        # Находим урок 1 и обновляем file_id
        if "1" in lessons_data:
            media_list = lessons_data["1"].get("media", [])
            updated = False
            for media_item in media_list:
                if media_item.get("type") == "video" and "001 Корвет" in media_item.get("path", ""):
                    media_item["file_id"] = file_id
                    print(f"✅ Обновлен file_id в lessons.json")
                    updated = True
                    break
            
            if not updated:
                print("⚠️ Не найден видео элемент в уроке 1")
                return False
            
            # Сохраняем обновленный JSON
            with open(lessons_json_path, 'w', encoding='utf-8') as f:
                json.dump(lessons_data, f, ensure_ascii=False, indent=2)
            
            print(f"✅ lessons.json сохранен")
        else:
            print("❌ Урок 1 не найден в lessons.json")
            return False
        
        # Создаем backup оригинального файла и заменяем новым
        print(f"\n📦 Заменяю файл...")
        if original_video_path.exists() and not backup_video_path.exists():
            shutil.copy2(original_video_path, backup_video_path)
            print(f"✅ Создан backup: {backup_video_path.name}")
        
        # Заменяем оригинальный файл новым
        if original_video_path.exists():
            original_video_path.unlink()
        
        new_video_path.rename(original_video_path)
        print(f"✅ Файл заменен: {original_video_path.name}")
        
        print("\n" + "=" * 60)
        print("✅ ГОТОВО! Видео загружено и обновлено")
        print("=" * 60)
        return True
        
    except Exception as e:
        print(f"\n❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        await bot.session.close()

if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
