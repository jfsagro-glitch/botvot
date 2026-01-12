"""
Скрипт для загрузки медиа файлов в Telegram и получения их file_id.

Загружает все медиа файлы из Photo/video_pic в Telegram бота,
получает file_id и обновляет lessons.json с этими file_id.
"""

import json
import sys
import asyncio
import os
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


async def upload_media_file(bot: Bot, file_path: Path, media_type: str, test_chat_id: int) -> Optional[str]:
    """
    Загружает медиа файл в Telegram и получает file_id.
    
    Args:
        bot: Экземпляр бота
        file_path: Путь к файлу
        media_type: Тип медиа ('photo' или 'video')
        test_chat_id: ID чата для отправки (можно использовать свой ID)
    
    Returns:
        file_id или None в случае ошибки
    """
    try:
        file_input = FSInputFile(file_path)
        
        if media_type == 'photo':
            message = await bot.send_photo(test_chat_id, file_input)
            file_id = message.photo[-1].file_id  # Берем самое большое фото
            return file_id
        elif media_type == 'video':
            message = await bot.send_video(test_chat_id, file_input)
            file_id = message.video.file_id
            return file_id
        
        return None
    except Exception as e:
        print(f"   ❌ Ошибка при загрузке {file_path.name}: {e}")
        return None


async def process_media_files(bot: Bot, media_dir: Path, test_chat_id: int) -> Dict[int, List[Dict[str, str]]]:
    """
    Обрабатывает все медиа файлы и загружает их в Telegram.
    
    Returns:
        Словарь с file_id для каждого урока
    """
    media_by_lesson: Dict[int, List[Dict[str, str]]] = {}
    
    if not media_dir.exists():
        print(f"❌ Директория {media_dir} не существует!")
        return media_by_lesson
    
    print(f"📂 Сканирую директорию: {media_dir}")
    print()
    
    # Получаем список всех файлов
    all_files = list(media_dir.iterdir())
    total_files = len([f for f in all_files if f.is_file()])
    processed = 0
    
    for file_path in all_files:
        if not file_path.is_file():
            continue
        
        filename = file_path.name
        lesson_num = get_lesson_number_from_filename(filename)
        media_type = get_media_type(filename)
        
        if lesson_num is None:
            print(f"⚠️  Пропускаю файл (не удалось определить номер урока): {filename}")
            continue
        
        if media_type is None:
            print(f"⚠️  Пропускаю файл (неизвестный тип): {filename}")
            continue
        
        processed += 1
        print(f"[{processed}/{total_files}] 📤 Загружаю {media_type} для урока {lesson_num}: {filename}")
        
        file_id = await upload_media_file(bot, file_path, media_type, test_chat_id)
        
        if file_id:
            if lesson_num not in media_by_lesson:
                media_by_lesson[lesson_num] = []
            
            media_by_lesson[lesson_num].append({
                "type": media_type,
                "path": f"Photo/video_pic/{filename}",  # Сохраняем путь для совместимости
                "file_id": file_id
            })
            print(f"   ✅ Получен file_id: {file_id[:20]}...")
        else:
            print(f"   ❌ Не удалось получить file_id")
        
        # Небольшая пауза между загрузками
        await asyncio.sleep(0.5)
    
    return media_by_lesson


def update_lessons_with_file_ids(lessons_file: Path, media_by_lesson: Dict[int, List[Dict[str, str]]]):
    """Обновляет lessons.json с file_id для медиа файлов."""
    # Читаем текущий lessons.json
    with open(lessons_file, 'r', encoding='utf-8') as f:
        lessons = json.load(f)
    
    updated_count = 0
    
    # Обновляем уроки
    for lesson_num, media_list in media_by_lesson.items():
        lesson_key = str(lesson_num)
        
        if lesson_key not in lessons:
            print(f"⚠️  Урок {lesson_num} не найден в lessons.json, пропускаю...")
            continue
        
        # Обновляем или добавляем медиа с file_id
        lessons[lesson_key]["media"] = media_list
        updated_count += 1
        print(f"✅ Обновлен урок {lesson_num} с {len(media_list)} медиа файлами")
    
    # Сохраняем обновленный lessons.json
    if updated_count > 0:
        # Создаем резервную копию
        backup_file = lessons_file.with_suffix('.json.backup2')
        with open(backup_file, 'w', encoding='utf-8') as f:
            json.dump(lessons, f, ensure_ascii=False, indent=2)
        print(f"💾 Создана резервная копия: {backup_file}")
        
        # Сохраняем обновленный файл
        with open(lessons_file, 'w', encoding='utf-8') as f:
            json.dump(lessons, f, ensure_ascii=False, indent=2)
        
        print(f"✅ Обновлено {updated_count} уроков в {lessons_file}")
    else:
        print("ℹ️  Нет медиа для обновления")


async def main():
    """Основная функция."""
    # Определяем пути (project_root уже определен выше)
    media_dir = project_root / "Photo" / "video_pic"
    lessons_file = project_root / "data" / "lessons.json"
    
    print("=" * 60)
    print("📤 Загрузка медиа файлов в Telegram и получение file_id")
    print("=" * 60)
    print()
    
    # Проверяем существование файлов
    if not lessons_file.exists():
        print(f"❌ Файл {lessons_file} не найден!")
        return
    
    # Проверяем токен бота
    if not Config.COURSE_BOT_TOKEN:
        print("❌ COURSE_BOT_TOKEN не настроен в .env файле!")
        return
    
    # ID чата для тестовой отправки (можно использовать свой ID)
    # Получаем из конфига или используем админский чат
    test_chat_id = Config.ADMIN_CHAT_ID
    if not test_chat_id:
        print("⚠️  ADMIN_CHAT_ID не настроен. Используйте свой Telegram ID.")
        print("   Чтобы узнать свой ID, напишите боту @userinfobot")
        test_chat_id = input("Введите ваш Telegram ID (число): ").strip()
        try:
            test_chat_id = int(test_chat_id)
        except ValueError:
            print("❌ Неверный ID!")
            return
    
    print(f"📱 Используется чат ID: {test_chat_id}")
    print()
    
    # Инициализируем бота
    bot = Bot(token=Config.COURSE_BOT_TOKEN)
    
    try:
        # Обрабатываем медиа файлы
        print("📤 Шаг 1: Загрузка медиа файлов в Telegram...")
        print()
        media_by_lesson = await process_media_files(bot, media_dir, test_chat_id)
        
        if not media_by_lesson:
            print("❌ Не найдено медиа файлов для загрузки!")
            return
        
        print()
        print(f"📊 Загружено медиа для {len(media_by_lesson)} уроков")
        print()
        
        # Обновляем lessons.json
        print("📝 Шаг 2: Обновление lessons.json с file_id...")
        print()
        update_lessons_with_file_ids(lessons_file, media_by_lesson)
        
        print()
        print("=" * 60)
        print("✅ Готово! Все медиа файлы загружены и file_id сохранены.")
        print("=" * 60)
        print()
        print("💡 Теперь медиа файлы будут отправляться через file_id,")
        print("   что намного быстрее и надежнее, чем загрузка с диска.")
        
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
