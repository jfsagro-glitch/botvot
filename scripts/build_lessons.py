"""
Скрипт для создания финальной структуры уроков из маппинга дней.

Использование:
1. Создайте data/days_mapping.json с маппингом дней
2. Запустите: python scripts/build_lessons.py
"""

import json
from pathlib import Path


def build_lessons():
    """Создает финальную структуру уроков."""
    print("=" * 60)
    print("СОЗДАНИЕ СТРУКТУРЫ УРОКОВ")
    print("=" * 60)
    print()
    
    # Загружаем маппинг дней
    mapping_file = Path("data/days_mapping.json")
    if not mapping_file.exists():
        print(f"❌ Файл {mapping_file} не найден!")
        print("\nСоздайте файл со структурой:")
        print('{\n  "1": ["2023-11-01"],\n  "2": ["2023-11-02"],\n  ...\n}')
        return
    
    with open(mapping_file, "r", encoding="utf-8") as f:
        days_mapping = json.load(f)
    
    print(f"✅ Загружен маппинг для {len(days_mapping)} дней курса")
    
    # Загружаем данные по дням
    by_days_file = Path("data/by_days.json")
    if not by_days_file.exists():
        print(f"❌ Файл {by_days_file} не найден!")
        print("Сначала запустите: python scripts/parse_channel.py")
        return
    
    with open(by_days_file, "r", encoding="utf-8") as f:
        by_days_data = json.load(f)
    
    print(f"✅ Загружены данные по {len(by_days_data)} дням канала")
    
    # Создаем структуру уроков
    lessons = {}
    
    for course_day, channel_dates in sorted(days_mapping.items(), key=lambda x: int(x[0])):
        texts = []
        media_files = []
        
        for date in channel_dates:
            if date in by_days_data:
                for msg in by_days_data[date]:
                    if msg.get("text", "").strip():
                        texts.append(msg["text"].strip())
                    if msg.get("media"):
                        media_info = {
                            "type": msg["media"].get("type", "photo"),
                            "file_id": msg["media"].get("file_id"),
                            "path": msg["media"].get("path", "")
                        }
                        media_files.append(media_info)
        
        # Объединяем тексты
        combined_text = "\n\n".join(texts) if texts else ""
        
        # Формируем структуру урока
        lesson = {
            "title": f"День {course_day}",
            "text": combined_text,
            "media": media_files[:5],  # Ограничиваем количество медиа
            "task": "",  # Заполняется вручную
            "task_basic": "",  # Задание для базового тарифа
            "task_feedback": "",  # Задание для тарифа с обратной связью
            "buttons": ["submit_task", "ask_question", "discussion"],
            "silent": False  # Флаг "дня тишины"
        }
        
        lessons[course_day] = lesson
        print(f"✅ День {course_day}: {len(texts)} текстов, {len(media_files)} медиа")
    
    # Сохраняем структуру уроков
    lessons_file = Path("data/lessons.json")
    lessons_file.parent.mkdir(parents=True, exist_ok=True)
    
    with open(lessons_file, "w", encoding="utf-8") as f:
        json.dump(lessons, f, ensure_ascii=False, indent=2)
    
    print(f"\n✅ Структура уроков сохранена в {lessons_file}")
    print(f"\n📝 Следующие шаги:")
    print("1. Откройте data/lessons.json")
    print("2. Заполните поля 'task', 'task_basic', 'task_feedback' для каждого урока")
    print("3. При необходимости установите 'silent': true для дней тишины")
    print("4. Перезапустите курс-бот")


if __name__ == "__main__":
    build_lessons()

