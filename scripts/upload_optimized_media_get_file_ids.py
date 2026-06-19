"""
Скрипт для загрузки оптимизированных медиа файлов в Telegram и получения file_id.

Использует оптимизированные файлы из Photo/video_pic_optimized,
которые загружаются намного быстрее.
"""

import json
import sys
import asyncio
from pathlib import Path
from typing import Dict, List, Optional
import re

# Добавляем корень проекта в PYTHONPATH
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Устанавливаем UTF-8 для вывода в консоль Windows
if sys.platform == 'win32':
    import codecs
    sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')
    sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer, 'strict')

from aiogram import Bot
from aiogram.types import FSInputFile
from core.config import Config


def get_lesson_number_from_filename(filename: str) -> Optional[int]:
    """Извлекает номер урока из имени файла."""
    match = re.match(r'^(\d{3})', filename)
    if match:
        return int(match.group(1))
    match = re.match(r'^(\d+)', filename)
    if match:
        return int(match.group(1))
    return None


def get_media_type(filename: str) -> Optional[str]:
    """Определяет тип медиа по расширению файла."""
    ext = Path(filename).suffix.lower()
    if ext in ['.mp4', '.avi', '.mov', '.mkv', '.webm']:
        return 'video'
    elif ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp']:
        return 'photo'
    return None


async def upload_media_file(bot: Bot, file_path: Path, media_type: str, test_chat_id: int, max_retries: int = 3) -> Optional[str]:
    """Загружает медиа файл в Telegram и получает file_id с повторными попытками."""
    file_size = file_path.stat().st_size / (1024 * 1024)
    
    for attempt in range(1, max_retries + 1):
        try:
            file_input = FSInputFile(file_path)
            timeout = 180 if file_size > 5 else 60  # 3 минуты для больших, 1 минута для маленьких
            
            if media_type == 'photo':
                message = await asyncio.wait_for(
                    bot.send_photo(test_chat_id, file_input),
                    timeout=timeout
                )
                return message.photo[-1].file_id
            elif media_type == 'video':
                message = await asyncio.wait_for(
                    bot.send_video(test_chat_id, file_input),
                    timeout=timeout
                )
                return message.video.file_id
        except asyncio.TimeoutError:
            if attempt < max_retries:
                await asyncio.sleep(attempt * 3)
                continue
        except Exception as e:
            if attempt < max_retries and ("timeout" in str(e).lower() or "Connection" in str(e)):
                await asyncio.sleep(attempt * 3)
                continue
            else:
                if attempt == max_retries:
                    print(f"   ❌ Ошибка: {e}")
                return None
    return None


async def process_optimized_media(bot: Bot, optimized_dir: Path, test_chat_id: int, update_existing: bool = False) -> Dict[int, List[Dict[str, str]]]:
    """Обрабатывает оптимизированные медиа файлы."""
    media_by_lesson: Dict[int, List[Dict[str, str]]] = {}
    
    if not optimized_dir.exists():
        print(f"❌ Директория {optimized_dir} не существует!")
        return media_by_lesson
    
        # Загружаем текущий lessons.json, чтобы проверить существующие file_id
    lessons_file = project_root / "data" / "lessons.json"
    existing_file_ids = {}
    if lessons_file.exists() and not update_existing:
        with open(lessons_file, 'r', encoding='utf-8') as f:
            lessons = json.load(f)
        for lesson_key, lesson_data in lessons.items():
            media_list = lesson_data.get("media", [])
            for media_item in media_list:
                file_id = media_item.get("file_id")
                path = media_item.get("path", "")
                # Проверяем только оптимизированные файлы (видео могли быть переоптимизированы)
                if file_id and path and "video_pic_optimized" in path:
                    existing_file_ids[path] = file_id
    
    all_files = [f for f in optimized_dir.iterdir() if f.is_file()]
    total_files = len(all_files)
    processed = 0
    skipped_existing = 0
    
    for file_path in all_files:
        filename = file_path.name
        lesson_num = get_lesson_number_from_filename(filename)
        media_type = get_media_type(filename)
        
        if lesson_num is None or media_type is None:
            continue
        
        processed += 1
        file_size = file_path.stat().st_size / (1024 * 1024)
        relative_path = f"Photo/video_pic_optimized/{filename}"
        
        # Для видео всегда перезагружаем (могли быть переоптимизированы)
        # Для фото пропускаем, если file_id уже есть
        if media_type == "photo" and relative_path in existing_file_ids and not update_existing:
            print(f"[{processed}/{total_files}] ⏭️  Пропускаю {media_type} для урока {lesson_num}: {filename} (file_id уже есть)")
            skipped_existing += 1
            if lesson_num not in media_by_lesson:
                media_by_lesson[lesson_num] = []
            media_by_lesson[lesson_num].append({
                "type": media_type,
                "path": relative_path,
                "file_id": existing_file_ids[relative_path]
            })
            continue
        
        print(f"[{processed}/{total_files}] 📤 Загружаю {media_type} для урока {lesson_num}: {filename} ({file_size:.2f} МБ)")
        
        file_id = await upload_media_file(bot, file_path, media_type, test_chat_id, max_retries=3)
        
        if file_id:
            if lesson_num not in media_by_lesson:
                media_by_lesson[lesson_num] = []
            
            relative_path = f"Photo/video_pic_optimized/{filename}"
            media_by_lesson[lesson_num].append({
                "type": media_type,
                "path": relative_path,
                "file_id": file_id
            })
            print(f"   ✅ Получен file_id: {file_id[:30]}...")
        else:
            print(f"   ❌ Не удалось получить file_id")
        
        await asyncio.sleep(0.5)
    
    print()
    if skipped_existing > 0:
        print(f"⏭️  Пропущено {skipped_existing} файлов (file_id уже есть)")
    print(f"📊 Загружено медиа для {len(media_by_lesson)} уроков")
    
    return media_by_lesson


def update_lessons_with_file_ids(lessons_file: Path, media_by_lesson: Dict[int, List[Dict[str, str]]]):
    """Обновляет lessons.json с file_id для оптимизированных медиа."""
    with open(lessons_file, 'r', encoding='utf-8') as f:
        lessons = json.load(f)
    
    updated_count = 0
    
    for lesson_num, media_list in media_by_lesson.items():
        lesson_key = str(lesson_num)
        if lesson_key not in lessons:
            continue
        
        # Обновляем медиа с file_id
        lessons[lesson_key]["media"] = media_list
        updated_count += 1
        print(f"✅ Обновлен урок {lesson_num} с {len(media_list)} file_id")
    
    if updated_count > 0:
        backup_file = lessons_file.with_suffix('.json.backup_final')
        with open(backup_file, 'w', encoding='utf-8') as f:
            json.dump(lessons, f, ensure_ascii=False, indent=2)
        
        with open(lessons_file, 'w', encoding='utf-8') as f:
            json.dump(lessons, f, ensure_ascii=False, indent=2)
        
        print(f"✅ Обновлено {updated_count} уроков")


async def main():
    """Основная функция."""
    optimized_dir = project_root / "Photo" / "video_pic_optimized"
    lessons_file = project_root / "data" / "lessons.json"
    
    print("=" * 70)
    print("📤 Загрузка оптимизированных медиа в Telegram")
    print("=" * 70)
    print()
    
    if not Config.COURSE_BOT_TOKEN:
        print("❌ COURSE_BOT_TOKEN не настроен!")
        return
    
    test_chat_id = Config.ADMIN_CHAT_ID
    if not test_chat_id:
        print("⚠️  ADMIN_CHAT_ID не настроен.")
        test_chat_id = int(input("Введите ваш Telegram ID: ").strip())
    
    bot = Bot(token=Config.COURSE_BOT_TOKEN)
    
    try:
        print("📤 Загрузка оптимизированных медиа...")
        print("   (будут загружены только файлы без file_id)")
        print()
        media_by_lesson = await process_optimized_media(bot, optimized_dir, test_chat_id, update_existing=False)
        
        if media_by_lesson:
            print()
            print("📝 Обновление lessons.json...")
            print()
            update_lessons_with_file_ids(lessons_file, media_by_lesson)
        
        print()
        print("=" * 70)
        print("✅ Готово!")
        print("=" * 70)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
