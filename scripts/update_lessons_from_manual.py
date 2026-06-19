"""
Скрипт для обновления lessons.json из ручных данных.

Использование:
1. Создайте data/lessons_manual.json с уроками
2. Запустите: python scripts/update_lessons_from_manual.py
"""

import json
from pathlib import Path


def update_lessons():
    """Обновляет lessons.json из ручных данных."""
    print("=" * 60)
    print("ОБНОВЛЕНИЕ УРОКОВ ИЗ РУЧНЫХ ДАННЫХ")
    print("=" * 60)
    print()
    
    # Загружаем ручные данные
    manual_file = Path("data/lessons_manual.json")
    if not manual_file.exists():
        print(f"❌ Файл {manual_file} не найден!")
        print("Создайте файл с уроками в формате JSON")
        return
    
    with open(manual_file, "r", encoding="utf-8") as f:
        manual_lessons = json.load(f)
    
    print(f"Загружено {len(manual_lessons)} уроков из ручных данных")
    
    # Загружаем существующие уроки, если есть
    lessons_file = Path("data/lessons.json")
    existing_lessons = {}
    
    if lessons_file.exists():
        with open(lessons_file, "r", encoding="utf-8") as f:
            existing_lessons = json.load(f)
        print(f"Загружено {len(existing_lessons)} существующих уроков")
    
    # Обновляем/добавляем уроки
    updated_count = 0
    new_count = 0
    
    for day, lesson_data in manual_lessons.items():
        if day in existing_lessons:
            existing_lessons[day].update(lesson_data)
            updated_count += 1
            print(f"Обновлен урок {day}: {lesson_data.get('title', 'Без названия')}")
        else:
            existing_lessons[day] = lesson_data
            new_count += 1
            print(f"Добавлен урок {day}: {lesson_data.get('title', 'Без названия')}")
    
    # Сохраняем обновленные уроки
    lessons_file.parent.mkdir(parents=True, exist_ok=True)
    
    with open(lessons_file, "w", encoding="utf-8") as f:
        json.dump(existing_lessons, f, ensure_ascii=False, indent=2)
    
    print(f"\nУроки сохранены в {lessons_file}")
    print(f"\n📊 Статистика:")
    print(f"   - Обновлено: {updated_count}")
    print(f"   - Добавлено: {new_count}")
    print(f"   - Всего уроков: {len(existing_lessons)}")


if __name__ == "__main__":
    update_lessons()

