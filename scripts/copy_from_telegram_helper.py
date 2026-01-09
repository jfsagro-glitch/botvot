"""
Помощник для копирования уроков из Telegram.

Этот скрипт помогает структурировать скопированный из Telegram текст.
"""

import json
from pathlib import Path


def process_copied_text():
    """Обрабатывает скопированный из Telegram текст."""
    print("=" * 60)
    print("ОБРАБОТКА СКОПИРОВАННОГО ТЕКСТА ИЗ TELEGRAM")
    print("=" * 60)
    print()
    print("Инструкция:")
    print("1. Откройте канал https://web.telegram.org/k/#-3400082074")
    print("2. Скопируйте текст урока (Ctrl+C)")
    print("3. Вставьте сюда (Ctrl+V)")
    print("4. Нажмите Enter дважды для завершения")
    print()
    
    day = input("Номер дня (1-30): ").strip()
    if not day.isdigit():
        print("Неверный номер дня!")
        return
    
    title = input("Заголовок урока (или Enter для автоматического): ").strip()
    
    print("\nВставьте текст урока (дважды Enter для завершения):")
    text_lines = []
    empty_count = 0
    
    while True:
        try:
            line = input()
            if not line:
                empty_count += 1
                if empty_count >= 2:
                    break
            else:
                empty_count = 0
                text_lines.append(line)
        except EOFError:
            break
    
    text = "\n".join(text_lines)
    
    # Пытаемся автоматически определить заголовок
    if not title and text_lines:
        first_line = text_lines[0]
        if len(first_line) < 100:
            title = first_line
        else:
            title = f"День {day}"
    
    # Ищем задание в тексте
    task = ""
    if "🗝" in text or "#Задание" in text:
        parts = text.split("🗝")
        if len(parts) > 1:
            task_part = "🗝" + parts[1]
            # Берем до следующего большого раздела
            task_lines = task_part.split("\n\n")[:5]
            task = "\n\n".join(task_lines)
    
    # Спрашиваем про медиа
    print("\nЕсть ли картинки/видео в этом уроке? (y/n): ", end="")
    has_media = input().strip().lower() == 'y'
    
    media = []
    if has_media:
        print("Введите пути к медиа файлам (Enter для завершения):")
        while True:
            media_path = input("  Путь: ").strip()
            if not media_path:
                break
            media_type = "photo"
            if media_path.endswith((".mp4", ".avi", ".mov")):
                media_type = "video"
            media.append({
                "type": media_type,
                "path": media_path
            })
    
    # Создаем урок
    lesson = {
        "title": title or f"День {day}",
        "text": text,
        "media": media,
        "task": task,
        "task_basic": task,
        "task_feedback": task + "\n\n💡 Для тарифа с обратной связью: Опишите подробнее ваши результаты и вопросы.",
        "buttons": ["submit_task", "ask_question", "discussion"],
        "silent": False
    }
    
    # Загружаем существующие уроки
    lessons_file = Path("data/lessons.json")
    lessons = {}
    
    if lessons_file.exists():
        with open(lessons_file, "r", encoding="utf-8") as f:
            lessons = json.load(f)
    
    lessons[day] = lesson
    
    # Сохраняем
    with open(lessons_file, "w", encoding="utf-8") as f:
        json.dump(lessons, f, ensure_ascii=False, indent=2)
    
    print(f"\n✅ Урок {day} добавлен!")
    print(f"   Заголовок: {title}")
    print(f"   Длина текста: {len(text)} символов")
    print(f"   Медиа: {len(media)} файлов")
    print(f"\nВсего уроков: {len(lessons)}")


if __name__ == "__main__":
    process_copied_text()

