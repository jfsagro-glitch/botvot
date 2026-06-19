"""
Скрипт для добавления медиа файлов (фото и видео) в уроки.

Сканирует директорию Photo/video_pic и добавляет пути к медиа файлам
в соответствующие уроки в data/lessons.json на основе номеров файлов.
"""

import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Any
import re

# Устанавливаем UTF-8 для вывода в консоль Windows
if sys.platform == 'win32':
    import codecs
    sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')
    sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer, 'strict')


def get_lesson_number_from_filename(filename: str) -> int:
    """
    Извлекает номер урока из имени файла.
    Примеры:
    - "000 Шерлок 3.mp4" -> 0
    - "001 Корвет.mp4" -> 1
    - "002 Вопрос-кирпич.jpg" -> 2
    - "030 Интервью.mp4" -> 30
    """
    # Ищем первые 3 цифры в начале имени файла
    match = re.match(r'^(\d{3})', filename)
    if match:
        return int(match.group(1))
    
    # Если не нашли 3 цифры, пробуем найти любое число в начале
    match = re.match(r'^(\d+)', filename)
    if match:
        num = int(match.group(1))
        # Если число меньше 100, возвращаем как есть
        return num
    
    return None


def get_media_type(filename: str) -> str:
    """Определяет тип медиа по расширению файла."""
    ext = Path(filename).suffix.lower()
    if ext in ['.mp4', '.avi', '.mov', '.mkv', '.webm']:
        return 'video'
    elif ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp']:
        return 'photo'
    else:
        return None


def scan_media_directory(media_dir: Path) -> Dict[int, List[Dict[str, str]]]:
    """
    Сканирует директорию с медиа файлами и группирует их по номерам уроков.
    
    Returns:
        Dict[int, List[Dict]]: Словарь, где ключ - номер урока, значение - список медиа файлов
    """
    media_by_lesson: Dict[int, List[Dict[str, str]]] = {}
    
    if not media_dir.exists():
        print(f"❌ Директория {media_dir} не существует!")
        return media_by_lesson
    
    print(f"📂 Сканирую директорию: {media_dir}")
    
    # Сканируем все файлы в директории
    for file_path in media_dir.iterdir():
        if file_path.is_file():
            filename = file_path.name
            lesson_num = get_lesson_number_from_filename(filename)
            media_type = get_media_type(filename)
            
            if lesson_num is None:
                print(f"⚠️  Не удалось определить номер урока для файла: {filename}")
                continue
            
            if media_type is None:
                print(f"⚠️  Неизвестный тип медиа для файла: {filename}")
                continue
            
            # Формируем относительный путь от корня проекта
            # Путь должен быть в формате: Photo/video_pic/filename
            relative_path = f"Photo/video_pic/{filename}"
            
            media_item = {
                "type": media_type,
                "path": relative_path
            }
            
            if lesson_num not in media_by_lesson:
                media_by_lesson[lesson_num] = []
            
            media_by_lesson[lesson_num].append(media_item)
            print(f"✅ Найден {media_type} для урока {lesson_num}: {filename}")
    
    return media_by_lesson


def add_media_to_lessons(lessons_file: Path, media_by_lesson: Dict[int, List[Dict[str, str]]]):
    """
    Добавляет медиа файлы в соответствующие уроки в lessons.json.
    """
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
        
        # Получаем текущий список медиа (если есть)
        current_media = lessons[lesson_key].get("media", [])
        
        # Проверяем, не добавлены ли уже эти файлы
        existing_paths = {item.get("path") for item in current_media if "path" in item}
        
        # Добавляем только новые медиа
        new_media = []
        for media_item in media_list:
            if media_item["path"] not in existing_paths:
                new_media.append(media_item)
                existing_paths.add(media_item["path"])
            else:
                print(f"ℹ️  Медиа {media_item['path']} уже добавлено в урок {lesson_num}")
        
        # Объединяем существующие и новые медиа
        if new_media:
            lessons[lesson_key]["media"] = current_media + new_media
            updated_count += 1
            print(f"✅ Добавлено {len(new_media)} медиа файлов в урок {lesson_num}")
        else:
            print(f"ℹ️  Нет новых медиа для урока {lesson_num}")
    
    # Сохраняем обновленный lessons.json
    if updated_count > 0:
        # Создаем резервную копию
        backup_file = lessons_file.with_suffix('.json.backup')
        with open(backup_file, 'w', encoding='utf-8') as f:
            json.dump(lessons, f, ensure_ascii=False, indent=2)
        print(f"💾 Создана резервная копия: {backup_file}")
        
        # Сохраняем обновленный файл
        with open(lessons_file, 'w', encoding='utf-8') as f:
            json.dump(lessons, f, ensure_ascii=False, indent=2)
        
        print(f"✅ Обновлено {updated_count} уроков в {lessons_file}")
    else:
        print("ℹ️  Нет новых медиа для добавления")


def main():
    """Основная функция."""
    # Определяем пути
    project_root = Path(__file__).parent.parent
    media_dir = project_root / "Photo" / "video_pic"
    lessons_file = project_root / "data" / "lessons.json"
    
    print("=" * 60)
    print("🎬 Добавление медиа файлов в уроки")
    print("=" * 60)
    print()
    
    # Проверяем существование файлов
    if not lessons_file.exists():
        print(f"❌ Файл {lessons_file} не найден!")
        return
    
    # Сканируем медиа директорию
    print("📂 Шаг 1: Сканирование медиа файлов...")
    print()
    media_by_lesson = scan_media_directory(media_dir)
    
    if not media_by_lesson:
        print("❌ Не найдено медиа файлов для добавления!")
        return
    
    print()
    print(f"📊 Найдено медиа для {len(media_by_lesson)} уроков")
    print()
    
    # Добавляем медиа в уроки
    print("📝 Шаг 2: Добавление медиа в уроки...")
    print()
    add_media_to_lessons(lessons_file, media_by_lesson)
    
    print()
    print("=" * 60)
    print("✅ Готово!")
    print("=" * 60)


if __name__ == "__main__":
    main()
