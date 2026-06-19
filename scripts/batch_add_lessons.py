"""
Пакетное добавление уроков из текстового файла.

Формат файла:
День 1
Заголовок урока
---
Текст урока
Может быть многострочным
---
Задание урока
---
media: путь/к/файлу.jpg
media: путь/к/видео.mp4
===

День 2
...
"""

import json
import re
from pathlib import Path


def parse_lessons_file(file_path: str):
    """Парсит файл с уроками."""
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()
    
    # Разделяем на уроки по ===
    lessons_blocks = content.split("===")
    
    lessons = {}
    
    for block in lessons_blocks:
        block = block.strip()
        if not block:
            continue
        
        lines = block.split("\n")
        
        # Ищем номер дня
        day = None
        title = None
        text_start = None
        task_start = None
        media_start = None
        
        for i, line in enumerate(lines):
            if line.startswith("День") and any(c.isdigit() for c in line):
                # Извлекаем номер дня
                day_match = re.search(r'\d+', line)
                if day_match:
                    day = day_match.group()
                    # Следующая строка может быть заголовком
                    if i + 1 < len(lines) and lines[i + 1].strip() and not lines[i + 1].startswith("---"):
                        title = lines[i + 1].strip()
                    else:
                        title = f"День {day}"
                break
        
        if not day:
            continue
        
        # Ищем разделители
        for i, line in enumerate(lines):
            if line.strip() == "---":
                if text_start is None:
                    text_start = i
                elif task_start is None:
                    task_start = i
                elif media_start is None:
                    media_start = i
        
        # Извлекаем текст
        text = ""
        if text_start is not None and task_start is not None:
            text_lines = lines[text_start + 1:task_start]
            text = "\n".join(text_lines).strip()
        elif text_start is not None:
            text_lines = lines[text_start + 1:]
            text = "\n".join(text_lines).strip()
        
        # Извлекаем задание
        task = ""
        if task_start is not None and media_start is not None:
            task_lines = lines[task_start + 1:media_start]
            task = "\n".join(task_lines).strip()
        elif task_start is not None:
            task_lines = lines[task_start + 1:]
            task = "\n".join(task_lines).strip()
        
        # Извлекаем медиа
        media = []
        if media_start is not None:
            media_lines = lines[media_start + 1:]
            for line in media_lines:
                if line.startswith("media:"):
                    media_path = line.replace("media:", "").strip()
                    media_type = "photo"
                    if media_path.endswith((".mp4", ".avi", ".mov")):
                        media_type = "video"
                    elif media_path.endswith((".pdf", ".doc", ".docx")):
                        media_type = "document"
                    media.append({
                        "type": media_type,
                        "path": media_path
                    })
        
        # Создаем урок
        lessons[day] = {
            "title": title or f"День {day}",
            "text": text,
            "media": media,
            "task": task,
            "task_basic": task,
            "task_feedback": task + "\n\n💡 Для тарифа с обратной связью: Опишите подробнее.",
            "buttons": ["submit_task", "ask_question", "discussion"],
            "silent": False
        }
    
    return lessons


def main():
    """Основная функция."""
    print("=" * 60)
    print("ПАКЕТНОЕ ДОБАВЛЕНИЕ УРОКОВ")
    print("=" * 60)
    print()
    
    file_path = input("Путь к файлу с уроками (или Enter для lessons/lessons.txt): ").strip()
    if not file_path:
        file_path = "lessons/lessons.txt"
    
    file_path = Path(file_path)
    
    if not file_path.exists():
        print(f"Файл {file_path} не найден!")
        print("\nСоздайте файл в формате:")
        print("""
День 1
Заголовок урока
---
Текст урока
---
Задание
---
media: путь/к/файлу.jpg
===

День 2
...
""")
        return
    
    # Парсим файл
    lessons = parse_lessons_file(str(file_path))
    
    print(f"\nНайдено {len(lessons)} уроков")
    
    # Загружаем существующие
    lessons_file = Path("data/lessons.json")
    existing_lessons = {}
    
    if lessons_file.exists():
        with open(lessons_file, "r", encoding="utf-8") as f:
            existing_lessons = json.load(f)
    
    # Объединяем
    for day, lesson_data in lessons.items():
        existing_lessons[day] = lesson_data
    
    # Сохраняем
    with open(lessons_file, "w", encoding="utf-8") as f:
        json.dump(existing_lessons, f, ensure_ascii=False, indent=2)
    
    print(f"\nУроки сохранены в {lessons_file}")
    print(f"Всего уроков: {len(existing_lessons)}")


if __name__ == "__main__":
    main()

