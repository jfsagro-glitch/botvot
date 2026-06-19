"""
Полный парсинг Telegram канала с сохранением форматирования, эмодзи и медиа.

Использование:
1. Установите telethon: pip install telethon
2. Получите api_id и api_hash на https://my.telegram.org/
3. Запустите: python scripts/parse_channel_full.py
"""

import asyncio
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
import sys

try:
    from telethon import TelegramClient
    from telethon.tl.functions.messages import GetHistoryRequest
    from telethon.tl.types import MessageMediaPhoto, MessageMediaDocument
    TELETHON_AVAILABLE = True
except ImportError:
    TELETHON_AVAILABLE = False
    print("❌ Telethon не установлен. Установите: pip install telethon")
    sys.exit(1)


async def parse_channel_full(
    api_id: int,
    api_hash: str,
    channel_username: str,
    session_name: str = "session"
):
    """
    Полный парсинг сообщений из Telegram канала с сохранением всего форматирования.
    
    Args:
        api_id: Telegram API ID
        api_hash: Telegram API Hash
        channel_username: Username канала (например, @StartNowAI_bot или -1001234567890)
        session_name: Имя файла сессии
    """
    if not TELETHON_AVAILABLE:
        raise ImportError("Telethon не установлен")
    
    client = TelegramClient(session_name, api_id, api_hash)
    
    await client.start()
    print(f"✅ Подключено к Telegram")
    
    try:
        # Пробуем получить канал по username или ID
        try:
            if channel_username.startswith('-100') or channel_username.lstrip('-').isdigit():
                channel = await client.get_entity(int(channel_username))
            else:
                channel = await client.get_entity(channel_username)
        except Exception as e:
            print(f"❌ Ошибка при получении канала: {e}")
            print(f"Попробуйте использовать ID канала (например, -1001234567890)")
            await client.disconnect()
            return None
        
        print(f"✅ Канал найден: {getattr(channel, 'title', 'N/A')}")
        print(f"   ID: {channel.id}")
        
    except Exception as e:
        print(f"❌ Ошибка при получении канала: {e}")
        await client.disconnect()
        return None
    
    offset_id = 0
    limit = 100
    messages = []
    media_dir = Path("media")
    media_dir.mkdir(exist_ok=True)
    
    print("📥 Начинаю парсинг сообщений...")
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
                # Используем raw_text для сохранения эмодзи и форматирования
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
                        # Создаем уникальное имя файла
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
                                "path": str(media_path),
                                "date": msg.date.strftime("%Y-%m-%d %H:%M:%S")
                            }
                            print(f"   📷 Скачано медиа: {Path(media_path).name}")
                    except Exception as e:
                        print(f"   ⚠️ Не удалось скачать медиа для сообщения {msg.id}: {e}")
                
                messages.append({
                    "id": msg.id,
                    "date": msg.date.astimezone(timezone.utc).strftime("%Y-%m-%d"),
                    "datetime": msg.date.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
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
            import traceback
            traceback.print_exc()
            break
    
    await client.disconnect()
    
    print(f"\n✅ Всего получено {len(messages)} сообщений")
    
    # Сохраняем сырые данные
    raw_file = Path("data/raw_channel.json")
    raw_file.parent.mkdir(parents=True, exist_ok=True)
    
    with open(raw_file, "w", encoding="utf-8") as f:
        json.dump(messages, f, ensure_ascii=False, indent=2)
    
    print(f"✅ Сырые данные сохранены в {raw_file}")
    
    # Группируем по дням
    print("\n📅 Группирую сообщения по дням...")
    by_days = group_by_days(messages)
    
    by_days_file = Path("data/by_days.json")
    with open(by_days_file, "w", encoding="utf-8") as f:
        json.dump(by_days, f, ensure_ascii=False, indent=2)
    
    print(f"✅ Данные по дням сохранены в {by_days_file}")
    print(f"\n📊 Найдено дней с сообщениями: {len(by_days)}")
    
    # Показываем статистику
    print("\n" + "=" * 60)
    print("СТАТИСТИКА ПАРСИНГА")
    print("=" * 60)
    for date, msgs in sorted(by_days.items()):
        media_count = sum(1 for m in msgs if m.get("media"))
        print(f"  {date}: {len(msgs)} сообщений, {media_count} с медиа")
    
    print("\n📝 Следующие шаги:")
    print("1. Создайте файл data/days_mapping.json со структурой:")
    print('   {"1": ["2023-11-01"], "2": ["2023-11-02"], ...}')
    print("2. Запустите: python scripts/build_lessons.py")
    
    return messages


def group_by_days(messages):
    """Группирует сообщения по дням с сохранением всех данных."""
    days = defaultdict(list)
    
    for msg in messages:
        if msg["text"].strip() or msg.get("media"):
            days[msg["date"]].append({
                "id": msg["id"],
                "datetime": msg.get("datetime", msg["date"]),
                "text": msg["text"].strip(),
                "media": msg.get("media")
            })
    
    return dict(days)


async def main():
    """Основная функция."""
    print("=" * 60)
    print("ПОЛНЫЙ ПАРСИНГ TELEGRAM КАНАЛА")
    print("=" * 60)
    print()
    
    # Настройки
    print("Введите данные для подключения к Telegram:")
    API_ID = input("API ID (получите на https://my.telegram.org/): ").strip()
    API_HASH = input("API Hash: ").strip()
    CHANNEL = input("Username канала или ID (например, @StartNowAI_bot или -1001234567890): ").strip()
    
    if not API_ID or not API_HASH or not CHANNEL:
        print("❌ Все поля обязательны!")
        return
    
    try:
        API_ID = int(API_ID)
    except ValueError:
        print("❌ API ID должен быть числом!")
        return
    
    # Парсим канал
    messages = await parse_channel_full(API_ID, API_HASH, CHANNEL)
    
    if messages:
        print("\n✅ Парсинг завершен успешно!")
    else:
        print("\n❌ Не удалось получить сообщения")


if __name__ == "__main__":
    if not TELETHON_AVAILABLE:
        print("Установите telethon: pip install telethon")
    else:
        asyncio.run(main())

