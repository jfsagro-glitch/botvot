"""
Извлечение уроков из Word файла.

Использование:
python scripts/extract_from_word.py
"""

import json
from pathlib import Path
import sys

try:
    from docx import Document
    DOCX_AVAILABLE = True
except ImportError:
    DOCX_AVAILABLE = False
    print("python-docx не установлен. Установите: pip install python-docx")
    sys.exit(1)


def extract_lessons_from_word(word_file_path: str):
    """
    Извлекает уроки из Word файла.
    
    Args:
        word_file_path: Путь к Word файлу
    """
    doc = Document(word_file_path)
    
    lessons = {}
    current_day = None
    current_text = []
    current_task = ""
    
    print("📖 Чтение Word файла...")
    
    for para in doc.paragraphs:
        text = para.text.strip()
        
        if not text:
            continue
        
        # Ищем начало нового дня (например, "День 1", "День 22" и т.д.)
        if "День" in text and any(char.isdigit() for char in text):
            # Сохраняем предыдущий урок, если есть
            if current_day and current_text:
                lessons[str(current_day)] = {
                    "title": f"День {current_day}",
                    "text": "\n\n".join(current_text),
                    "media": [],
                    "task": current_task,
                    "task_basic": current_task,
                    "task_feedback": current_task + "\n\n💡 Для тарифа с обратной связью: Опишите подробнее.",
                    "buttons": ["submit_task", "ask_question", "discussion"],
                    "silent": False
                }
            
            # Извлекаем номер дня
            try:
                day_num = int(''.join(filter(str.isdigit, text.split("День")[1].split()[0])))
                current_day = day_num
                current_text = [text]
                current_task = ""
                print(f"   Найден День {day_num}")
            except:
                current_text.append(text)
        
        # Ищем задание
        elif "🗝" in text or "#Задание" in text or "Задание" in text:
            current_task = text
            current_text.append(text)
        
        else:
            current_text.append(text)
    
    # Сохраняем последний урок
    if current_day and current_text:
        lessons[str(current_day)] = {
            "title": f"День {current_day}",
            "text": "\n\n".join(current_text),
            "media": [],
            "task": current_task,
            "task_basic": current_task,
            "task_feedback": current_task + "\n\n💡 Для тарифа с обратной связью: Опишите подробнее.",
            "buttons": ["submit_task", "ask_question", "discussion"],
            "silent": False
        }
    
    return lessons


def main():
    """Основная функция."""
    print("=" * 60)
    print("ИЗВЛЕЧЕНИЕ УРОКОВ ИЗ WORD ФАЙЛА")
    print("=" * 60)
    print()
    
    word_file = Path("lessons/Вопросы, которые меняют всё.docx")
    
    if not word_file.exists():
        print(f"❌ Файл {word_file} не найден!")
        return
    
    # Извлекаем уроки
    lessons = extract_lessons_from_word(str(word_file))
    
    print(f"\n✅ Извлечено {len(lessons)} уроков из Word файла")
    
    # Загружаем существующие уроки
    lessons_file = Path("data/lessons.json")
    existing_lessons = {}
    
    if lessons_file.exists():
        with open(lessons_file, "r", encoding="utf-8") as f:
            existing_lessons = json.load(f)
        print(f"📚 Загружено {len(existing_lessons)} существующих уроков")
    
    # Объединяем (новые данные перезаписывают старые)
    for day, lesson_data in lessons.items():
        existing_lessons[day] = lesson_data
    
    # Сохраняем
    with open(lessons_file, "w", encoding="utf-8") as f:
        json.dump(existing_lessons, f, ensure_ascii=False, indent=2)
    
    print(f"\n✅ Уроки сохранены в {lessons_file}")
    print(f"   Всего уроков: {len(existing_lessons)}")
    print(f"\n📝 Примечание:")
    print("   Word файл не содержит форматирование и эмодзи из Telegram.")
    print("   Для полного сохранения форматирования запустите парсинг канала:")
    print("   python scripts/parse_and_update_lessons.py")


if __name__ == "__main__":
    if not DOCX_AVAILABLE:
        print("Установите python-docx: pip install python-docx")
    else:
        main()

