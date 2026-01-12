"""
Скрипт для изменения формата заголовков уроков на две строки.
Формат: "День X - Название" -> "День X\nНазвание"
"""

import json
import sys
import re
from pathlib import Path

# Добавляем корень проекта в PYTHONPATH
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Устанавливаем UTF-8 для вывода в консоль Windows
if sys.platform == 'win32':
    import codecs
    sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')
    sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer, 'strict')

def fix_titles():
    """Исправляет формат заголовков уроков."""
    lessons_file = project_root / "data" / "lessons.json"
    
    if not lessons_file.exists():
        print(f"❌ Файл {lessons_file} не найден!")
        return
    
    # Читаем файл
    with open(lessons_file, 'r', encoding='utf-8') as f:
        lessons = json.load(f)
    
    print("=" * 70)
    print("🔧 Исправление формата заголовков уроков")
    print("=" * 70)
    print()
    
    updated_count = 0
    
    # Проходим по всем урокам
    for lesson_key, lesson_data in lessons.items():
        if not isinstance(lesson_data, dict):
            continue
        
        title = lesson_data.get("title", "")
        if not title:
            continue
        
        # Проверяем, есть ли формат "День X - Название"
        # Ищем паттерн "День X - Название" или "День X -Название"
        match = re.match(r'^День\s+(\d+)\s*-\s*(.+)$', title)
        if match:
            day_number = match.group(1)
            lesson_name = match.group(2).strip()
            new_title = f"День {day_number}\n{lesson_name}"
            
            # Обновляем заголовок
            lesson_data["title"] = new_title
            updated_count += 1
            print(f"✅ Урок {lesson_key}: '{title}' -> '{new_title}'")
        else:
            # Проверяем, не в правильном ли формате уже
            if '\n' in title:
                print(f"⏭️  Урок {lesson_key}: уже в правильном формате - '{title}'")
            else:
                print(f"⚠️  Урок {lesson_key}: нестандартный формат - '{title}'")
    
    # Создаем резервную копию
    backup_file = lessons_file.with_suffix('.json.backup_titles')
    with open(backup_file, 'w', encoding='utf-8') as f:
        json.dump(lessons, f, ensure_ascii=False, indent=2)
    print(f"\n💾 Создана резервная копия: {backup_file}")
    
    # Сохраняем обновленный файл
    with open(lessons_file, 'w', encoding='utf-8') as f:
        json.dump(lessons, f, ensure_ascii=False, indent=2)
    
    print()
    print("=" * 70)
    print(f"✅ Готово! Обновлено {updated_count} заголовков")
    print("=" * 70)

if __name__ == "__main__":
    fix_titles()
