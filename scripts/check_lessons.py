"""Проверка количества и содержания уроков."""
import json
from pathlib import Path

lessons_file = Path("data/lessons.json")
if lessons_file.exists():
    with open(lessons_file, "r", encoding="utf-8") as f:
        lessons = json.load(f)
    
    print(f"Текущее количество уроков: {len(lessons)}")
    print(f"Дни: {sorted([int(k) for k in lessons.keys()])}")
    
    for day in sorted([int(k) for k in lessons.keys()]):
        lesson = lessons[str(day)]
        media_count = len(lesson.get("media", []))
        has_intro = "intro_text" in lesson
        print(f"\nДень {day}: {lesson.get('title', 'Без названия')}")
        print(f"  - Медиа: {media_count}")
        print(f"  - Вводный текст: {'Да' if has_intro else 'Нет'}")
        print(f"  - Длина текста: {len(lesson.get('text', ''))} символов")
else:
    print("Файл lessons.json не найден")

