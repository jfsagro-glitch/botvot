"""
Автоматический парсинг канала и обновление уроков.

Этот скрипт:
1. Парсит канал Telegram
2. Сохраняет форматирование, эмодзи и медиа
3. Обновляет lessons.json
"""

import asyncio
import json
from pathlib import Path
import sys

# Проверяем наличие telethon
try:
    from telethon import TelegramClient
    from telethon.tl.functions.messages import GetHistoryRequest
    TELETHON_AVAILABLE = True
except ImportError:
    TELETHON_AVAILABLE = False
    print("❌ Telethon не установлен. Установите: pip install telethon")
    sys.exit(1)


async def parse_channel_for_lessons(
    api_id: int,
    api_hash: str,
    channel_id: str
):
    """
    Парсит канал и создает структуру уроков.
    
    Args:
        api_id: Telegram API ID
        api_hash: Telegram API Hash
        channel_id: ID канала (например, -1003400082074)
    """
    client = TelegramClient("session", api_id, api_hash)
    
    await client.start()
    print("✅ Подключено к Telegram")
    
    try:
        # Получаем канал по ID
        channel = await client.get_entity(int(channel_id))
        print(f"✅ Канал найден: {getattr(channel, 'title', 'N/A')}")
        print(f"   ID: {channel.id}")
        
    except Exception as e:
        print(f"❌ Ошибка при получении канала: {e}")
        await client.disconnect()
        return None
    
    offset_id = 0
    limit = 100
    all_messages = []
    media_dir = Path("media")
    media_dir.mkdir(exist_ok=True)
    
    print("\n📥 Начинаю парсинг сообщений...")
    print("   (Это может занять некоторое время...)")
    
    while True:
        try:
            history = await client(GetHistoryRequest(
                peer=channel,
                offset_id=offset_id,
                offset_date=None,
                add_offset=0,
                limit=limit,
                max_id=0,
                min_id=0,
                hash=0
            ))
            
            if not history.messages:
                break
            
            for msg in history.messages:
                # Получаем текст с сохранением форматирования
                text = ""
                if hasattr(msg, 'raw_text') and msg.raw_text:
                    text = msg.raw_text
                elif hasattr(msg, 'message') and msg.message:
                    text = msg.message
                
                # Проверяем наличие медиа
                has_photo = bool(msg.photo)
                has_video = bool(msg.video)
                has_document = bool(msg.document)
                has_media = has_photo or has_video or has_document
                
                # Получаем медиа файл, если есть
                media_info = None
                if has_media:
                    try:
                        media_filename = f"msg_{msg.id}_{msg.date.strftime('%Y%m%d_%H%M%S')}"
                        
                        if has_photo:
                            media_path = await client.download_media(
                                msg, 
                                file=f"{media_dir}/{media_filename}.jpg"
                            )
                            media_type = "photo"
                        elif has_video:
                            media_path = await client.download_media(
                                msg,
                                file=f"{media_dir}/{media_filename}.mp4"
                            )
                            media_type = "video"
                        else:
                            media_path = await client.download_media(
                                msg,
                                file=f"{media_dir}/{media_filename}"
                            )
                            media_type = "document"
                        
                        if media_path:
                            media_info = {
                                "type": media_type,
                                "file_id": msg.id,
                                "path": str(media_path)
                            }
                    except Exception as e:
                        print(f"   ⚠️ Не удалось скачать медиа для сообщения {msg.id}: {e}")
                
                all_messages.append({
                    "id": msg.id,
                    "date": msg.date.astimezone().strftime("%Y-%m-%d"),
                    "datetime": msg.date.astimezone().strftime("%Y-%m-%d %H:%M:%S"),
                    "text": text,
                    "media": media_info
                })
            
            print(f"   Получено {len(all_messages)} сообщений...")
            offset_id = history.messages[-1].id
            
            if len(history.messages) < limit:
                break
                
        except Exception as e:
            print(f"❌ Ошибка при получении истории: {e}")
            import traceback
            traceback.print_exc()
            break
    
    await client.disconnect()
    
    print(f"\n✅ Всего получено {len(all_messages)} сообщений")
    
    # Группируем по дням
    from collections import defaultdict
    by_days = defaultdict(list)
    
    for msg in all_messages:
        if msg["text"].strip() or msg.get("media"):
            by_days[msg["date"]].append({
                "id": msg["id"],
                "datetime": msg.get("datetime", msg["date"]),
                "text": msg["text"].strip(),
                "media": msg.get("media")
            })
    
    print(f"\n📊 Найдено дней с сообщениями: {len(by_days)}")
    
    # Показываем статистику
    print("\n" + "=" * 60)
    print("СТАТИСТИКА ПАРСИНГА")
    print("=" * 60)
    for date in sorted(by_days.keys()):
        msgs = by_days[date]
        media_count = sum(1 for m in msgs if m.get("media"))
        print(f"  {date}: {len(msgs)} сообщений, {media_count} с медиа")
    
    # Сохраняем сырые данные
    raw_file = Path("data/raw_channel.json")
    raw_file.parent.mkdir(parents=True, exist_ok=True)
    
    with open(raw_file, "w", encoding="utf-8") as f:
        json.dump(list(all_messages), f, ensure_ascii=False, indent=2)
    
    # Сохраняем данные по дням
    by_days_file = Path("data/by_days.json")
    with open(by_days_file, "w", encoding="utf-8") as f:
        json.dump(dict(by_days), f, ensure_ascii=False, indent=2)
    
    print(f"\n✅ Данные сохранены:")
    print(f"   - {raw_file}")
    print(f"   - {by_days_file}")
    
    return dict(by_days)


def create_lessons_from_parsed_data(by_days_data):
    """
    Создает структуру уроков из спарсенных данных.
    
    Пытается автоматически определить дни курса на основе дат.
    """
    lessons = {}
    
    # Сортируем даты
    sorted_dates = sorted(by_days_data.keys())
    
    print(f"\n📝 Создание структуры уроков...")
    print(f"   Найдено {len(sorted_dates)} дней с сообщениями")
    
    # Если дней больше 30, берем первые 30
    # Если меньше, создаем уроки для всех дней
    days_to_process = sorted_dates[:30] if len(sorted_dates) >= 30 else sorted_dates
    
    for day_num, date in enumerate(days_to_process, start=1):
        msgs = by_days_data[date]
        texts = []
        media_files = []
        
        for msg in msgs:
            if msg.get("text", "").strip():
                texts.append(msg["text"].strip())
            if msg.get("media"):
                media_files.append(msg["media"])
        
        # Объединяем тексты
        combined_text = "\n\n".join(texts) if texts else ""
        
        # Извлекаем задание из текста (ищем маркеры типа "🗝 #Задание")
        task = ""
        task_basic = ""
        task_feedback = ""
        
        # Ищем задание в тексте
        if "🗝" in combined_text or "#Задание" in combined_text:
            # Пытаемся извлечь задание
            parts = combined_text.split("🗝")
            if len(parts) > 1:
                task_text = "🗝" + parts[1]
                # Берем текст до следующего большого раздела или до конца
                task_lines = task_text.split("\n\n")
                task = "\n\n".join(task_lines[:10])  # Берем первые 10 строк
                task_basic = task
                task_feedback = task + "\n\n💡 Для тарифа с обратной связью: Опишите подробнее ваши результаты и вопросы."
        
        # Определяем заголовок
        title = f"День {day_num}"
        if combined_text:
            # Пытаемся найти заголовок в тексте
            first_line = combined_text.split("\n")[0]
            if len(first_line) < 100 and ("День" in first_line or "⭕️" in first_line):
                title = first_line.replace("⭕️", "").strip()
                if not title.startswith("День"):
                    title = f"День {day_num} - {title}"
        
        lesson = {
            "title": title,
            "text": combined_text,
            "media": media_files[:5],  # Ограничиваем количество медиа
            "task": task,
            "task_basic": task_basic if task_basic else task,
            "task_feedback": task_feedback if task_feedback else task,
            "buttons": ["submit_task", "ask_question", "discussion"],
            "silent": False
        }
        
        lessons[str(day_num)] = lesson
        print(f"   ✅ День {day_num} ({date}): {len(texts)} текстов, {len(media_files)} медиа")
    
    return lessons


async def main():
    """Основная функция."""
    print("=" * 60)
    print("ПАРСИНГ КАНАЛА И ОБНОВЛЕНИЕ УРОКОВ")
    print("=" * 60)
    print()
    
    # Используем ID канала из ссылки
    CHANNEL_ID = "-1003400082074"  # Из ссылки https://web.telegram.org/k/#-3400082074
    
    print("Введите данные для подключения к Telegram:")
    API_ID = input("API ID (получите на https://my.telegram.org/): ").strip()
    API_HASH = input("API Hash: ").strip()
    
    if not API_ID or not API_HASH:
        print("❌ Все поля обязательны!")
        return
    
    try:
        API_ID = int(API_ID)
    except ValueError:
        print("❌ API ID должен быть числом!")
        return
    
    # Парсим канал
    by_days_data = await parse_channel_for_lessons(API_ID, API_HASH, CHANNEL_ID)
    
    if not by_days_data:
        print("\n❌ Не удалось получить данные из канала")
        return
    
    # Создаем структуру уроков
    lessons = create_lessons_from_parsed_data(by_days_data)
    
    # Загружаем существующие уроки
    lessons_file = Path("data/lessons.json")
    existing_lessons = {}
    
    if lessons_file.exists():
        with open(lessons_file, "r", encoding="utf-8") as f:
            existing_lessons = json.load(f)
        print(f"\n📚 Загружено {len(existing_lessons)} существующих уроков")
    
    # Обновляем/добавляем уроки
    for day, lesson_data in lessons.items():
        existing_lessons[day] = lesson_data
    
    # Сохраняем обновленные уроки
    with open(lessons_file, "w", encoding="utf-8") as f:
        json.dump(existing_lessons, f, ensure_ascii=False, indent=2)
    
    print(f"\n✅ Уроки сохранены в {lessons_file}")
    print(f"   Всего уроков: {len(existing_lessons)}")
    print(f"\n📝 Следующие шаги:")
    print("1. Проверьте data/lessons.json")
    print("2. При необходимости отредактируйте задания и заголовки")
    print("3. Перезапустите бота")


if __name__ == "__main__":
    if not TELETHON_AVAILABLE:
        print("Установите telethon: pip install telethon")
    else:
        asyncio.run(main())

