"""
Скрипт для парсинга Telegram канала и создания структуры уроков.

Использование:
1. Установите telethon: pip install telethon
2. Получите api_id и api_hash на https://my.telegram.org/
3. Запустите: python scripts/parse_channel.py
"""

import asyncio
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

try:
    from telethon import TelegramClient
    from telethon.tl.functions.messages import GetHistoryRequest
    TELETHON_AVAILABLE = True
except ImportError:
    TELETHON_AVAILABLE = False
    print("❌ Telethon не установлен. Установите: pip install telethon")


async def parse_channel(
    api_id: int,
    api_hash: str,
    channel_username: str,
    session_name: str = "session"
):
    """
    Парсит сообщения из Telegram канала.
    
    Args:
        api_id: Telegram API ID
        api_hash: Telegram API Hash
        channel_username: Username канала (например, @StartNowAI_bot)
        session_name: Имя файла сессии
    """
    if not TELETHON_AVAILABLE:
        raise ImportError("Telethon не установлен")
    
    client = TelegramClient(session_name, api_id, api_hash)
    
    await client.start()
    print(f"✅ Подключено к Telegram")
    
    try:
        channel = await client.get_entity(channel_username)
        print(f"✅ Канал найден: {channel.title}")
    except Exception as e:
        print(f"❌ Ошибка при получении канала: {e}")
        await client.disconnect()
        return None
    
    offset_id = 0
    limit = 100
    messages = []
    
    print("📥 Начинаю парсинг сообщений...")
    
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
                # Получаем текст сообщения с сохранением форматирования
                # Используем raw_text для сохранения эмодзи и форматирования
                text = msg.raw_text or msg.message or ""
                
                # Проверяем наличие медиа
                has_photo = bool(msg.photo)
                has_video = bool(msg.video)
                has_document = bool(msg.document)
                
                # Получаем медиа файл, если есть
                media_info = None
                if has_photo or has_video or has_document:
                    try:
                        media_path = await client.download_media(msg, file=f"media/temp_{msg.id}")
                        if media_path:
                            media_info = {
                                "type": "photo" if has_photo else ("video" if has_video else "document"),
                                "file_id": msg.id,
                                "path": str(media_path)
                            }
                    except Exception as e:
                        print(f"⚠️ Не удалось скачать медиа для сообщения {msg.id}: {e}")
                
                messages.append({
                    "id": msg.id,
                    "date": msg.date.astimezone(timezone.utc).strftime("%Y-%m-%d"),
                    "text": text,
                    "has_photo": has_photo,
                    "has_video": has_video,
                    "has_document": has_document,
                    "media": media_info
                })
            
            print(f"   Получено {len(messages)} сообщений...")
            offset_id = history.messages[-1].id
            
            if len(history.messages) < limit:
                break
                
        except Exception as e:
            print(f"❌ Ошибка при получении истории: {e}")
            break
    
    await client.disconnect()
    
    print(f"✅ Всего получено {len(messages)} сообщений")
    
    # Сохраняем сырые данные
    raw_file = Path("data/raw_channel.json")
    raw_file.parent.mkdir(parents=True, exist_ok=True)
    
    with open(raw_file, "w", encoding="utf-8") as f:
        json.dump(messages, f, ensure_ascii=False, indent=2)
    
    print(f"✅ Сырые данные сохранены в {raw_file}")
    
    return messages


def group_by_days(messages):
    """Группирует сообщения по дням."""
    days = defaultdict(list)
    
    for msg in messages:
        if msg["text"].strip():
            days[msg["date"]].append({
                "id": msg["id"],
                "text": msg["text"],
                "media": msg.get("media")
            })
    
    return dict(days)


def create_lessons_structure(days_mapping, by_days_data):
    """
    Создает структуру уроков на основе маппинга дней канала к дням курса.
    
    Args:
        days_mapping: dict вида {"1": ["2023-11-01"], "2": ["2023-11-02"]}
        by_days_data: dict с сообщениями, сгруппированными по дням
    """
    lessons = {}
    
    for course_day, channel_dates in days_mapping.items():
        texts = []
        media_files = []
        
        for date in channel_dates:
            if date in by_days_data:
                for msg in by_days_data[date]:
                    if msg["text"].strip():
                        texts.append(msg["text"])
                    if msg.get("media"):
                        media_files.append(msg["media"])
        
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
    
    return lessons


async def main():
    """Основная функция."""
    print("=" * 60)
    print("ПАРСИНГ TELEGRAM КАНАЛА ДЛЯ КУРСА")
    print("=" * 60)
    print()
    
    # Настройки (замените на свои)
    API_ID = input("Введите API ID (получите на https://my.telegram.org/): ").strip()
    API_HASH = input("Введите API Hash: ").strip()
    CHANNEL_USERNAME = input("Введите username канала (например, @StartNowAI_bot): ").strip()
    
    if not API_ID or not API_HASH or not CHANNEL_USERNAME:
        print("❌ Все поля обязательны!")
        return
    
    try:
        API_ID = int(API_ID)
    except ValueError:
        print("❌ API ID должен быть числом!")
        return
    
    # Парсим канал
    messages = await parse_channel(API_ID, API_HASH, CHANNEL_USERNAME)
    
    if not messages:
        print("❌ Не удалось получить сообщения")
        return
    
    # Группируем по дням
    print("\n📅 Группирую сообщения по дням...")
    by_days = group_by_days(messages)
    
    by_days_file = Path("data/by_days.json")
    with open(by_days_file, "w", encoding="utf-8") as f:
        json.dump(by_days, f, ensure_ascii=False, indent=2)
    
    print(f"✅ Данные по дням сохранены в {by_days_file}")
    print(f"\n📊 Найдено дней с сообщениями: {len(by_days)}")
    
    # Показываем структуру для ручного маппинга
    print("\n" + "=" * 60)
    print("СЛЕДУЮЩИЙ ШАГ: СОЗДАНИЕ МАППИНГА")
    print("=" * 60)
    print("\nСоздайте файл data/days_mapping.json со структурой:")
    print('{\n  "1": ["2023-11-01"],\n  "2": ["2023-11-02"],\n  ...\n}')
    print("\nГде ключ - день курса (1-30), значение - список дат из канала")
    print("\nПосле создания маппинга запустите: python scripts/build_lessons.py")


if __name__ == "__main__":
    if not TELETHON_AVAILABLE:
        print("Установите telethon: pip install telethon")
    else:
        asyncio.run(main())

