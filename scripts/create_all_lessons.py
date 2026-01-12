"""
Создание структуры для всех 30 уроков.

Использование:
python scripts/create_all_lessons.py
"""

import json
from pathlib import Path


def create_all_lessons_structure():
    """Создает базовую структуру для всех 30 уроков."""
    print("=" * 60)
    print("СОЗДАНИЕ СТРУКТУРЫ ДЛЯ ВСЕХ 30 УРОКОВ")
    print("=" * 60)
    print()
    
    # Загружаем существующие уроки
    lessons_file = Path("data/lessons.json")
    lessons = {}
    
    if lessons_file.exists():
        with open(lessons_file, "r", encoding="utf-8") as f:
            lessons = json.load(f)
        print(f"Загружено {len(lessons)} существующих уроков")
    
    # Создаем структуру для всех 30 дней
    for day in range(1, 31):
        day_str = str(day)
        
        # Если урок уже есть, пропускаем
        if day_str in lessons:
            print(f"  День {day}: уже существует")
            continue
        
        # Создаем базовую структуру
        lessons[day_str] = {
            "title": f"День {day}",
            "text": f"Урок {day} - контент будет добавлен",
            "media": [],
            "task": f"Задание для дня {day}",
            "task_basic": f"Задание для дня {day}",
            "task_feedback": f"Задание для дня {day}\n\n💡 Для тарифа с обратной связью: Опишите подробнее ваши результаты.",
            "buttons": ["submit_task", "ask_question", "discussion"],
            "silent": False
        }
        print(f"  День {day}: создан")
    
    # Сохраняем
    with open(lessons_file, "w", encoding="utf-8") as f:
        json.dump(lessons, f, ensure_ascii=False, indent=2)
    
    print(f"\nСтруктура создана!")
    print(f"Всего уроков: {len(lessons)}")
    print(f"\nСледующие шаги:")
    print("1. Откройте data/lessons.json")
    print("2. Заполните текст, задания и медиа для каждого урока")
    print("3. Или используйте скрипты для добавления из Telegram")


if __name__ == "__main__":
    create_all_lessons_structure()

