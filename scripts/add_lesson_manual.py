"""
Ручное добавление урока в lessons.json.

Использование:
python scripts/add_lesson_manual.py
"""

import json
from pathlib import Path


def add_lesson_manual():
    """Добавляет урок вручную."""
    print("=" * 60)
    print("РУЧНОЕ ДОБАВЛЕНИЕ УРОКА")
    print("=" * 60)
    print()
    
    # Загружаем существующие уроки
    lessons_file = Path("data/lessons.json")
    lessons = {}
    
    if lessons_file.exists():
        with open(lessons_file, "r", encoding="utf-8") as f:
            lessons = json.load(f)
        print(f"Загружено {len(lessons)} существующих уроков")
    
    # Запрашиваем данные урока
    print("\nВведите данные урока:")
    day = input("Номер дня (1-30): ").strip()
    
    if not day.isdigit() or int(day) < 1 or int(day) > 30:
        print("Неверный номер дня!")
        return
    
    title = input("Заголовок урока: ").strip()
    if not title:
        title = f"День {day}"
    
    print("\nВведите текст урока (для завершения введите пустую строку):")
    text_lines = []
    while True:
        line = input()
        if not line:
            break
        text_lines.append(line)
    text = "\n".join(text_lines)
    
    task = input("\nЗадание (можно оставить пустым): ").strip()
    
    # Спрашиваем про медиа
    media = []
    while True:
        media_path = input("\nПуть к медиа файлу (или Enter для завершения): ").strip()
        if not media_path:
            break
        media_type = input("Тип медиа (photo/video/document): ").strip() or "photo"
        media.append({
            "type": media_type,
            "path": media_path
        })
    
    # Создаем урок
    lesson = {
        "title": title,
        "text": text,
        "media": media,
        "task": task,
        "task_basic": task,
        "task_feedback": task + "\n\n💡 Для тарифа с обратной связью: Опишите подробнее ваши результаты.",
        "buttons": ["submit_task", "ask_question", "discussion"],
        "silent": False
    }
    
    lessons[day] = lesson
    
    # Сохраняем
    with open(lessons_file, "w", encoding="utf-8") as f:
        json.dump(lessons, f, ensure_ascii=False, indent=2)
    
    print(f"\nУрок {day} добавлен/обновлен!")
    print(f"Всего уроков: {len(lessons)}")


if __name__ == "__main__":
    add_lesson_manual()

