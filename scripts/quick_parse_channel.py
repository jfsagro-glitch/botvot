"""
Быстрый парсинг канала с автоматическим созданием уроков.

Использование:
python scripts/quick_parse_channel.py
"""

import asyncio
import json
from pathlib import Path
import sys

try:
    from telethon import TelegramClient
    from telethon.tl.functions.messages import GetHistoryRequest
    TELETHON_AVAILABLE = True
except ImportError:
    TELETHON_AVAILABLE = False
    print("❌ Telethon не установлен. Установите: pip install telethon")
    sys.exit(1)


async def quick_parse():
    """Быстрый парсинг с использованием сохраненной сессии."""
    print("=" * 60)
    print("БЫСТРЫЙ ПАРСИНГ КАНАЛА")
    print("=" * 60)
    print()
    
    # ID канала из ссылки
    CHANNEL_ID = "-1003400082074"
    
    # Проверяем наличие сессии
    if not Path("session.session").exists():
        print("❌ Сессия не найдена. Нужно сначала запустить полный парсинг.")
        print("   Запустите: python scripts/parse_and_update_lessons.py")
        return
    
    print("Введите API данные (или нажмите Enter для использования сохраненной сессии):")
    api_id_input = input("API ID (Enter для пропуска): ").strip()
    api_hash_input = input("API Hash (Enter для пропуска): ").strip()
    
    if api_id_input and api_hash_input:
        try:
            api_id = int(api_id_input)
            api_hash = api_hash_input
        except ValueError:
            print("❌ Неверный API ID")
            return
    else:
        # Пытаемся использовать сохраненную сессию
        print("Используется сохраненная сессия...")
        # Нужно будет получить из .env или запросить
        print("❌ Нужны API ключи для первой авторизации")
        return
    
    client = TelegramClient("session", api_id, api_hash)
    
    await client.start()
    print("✅ Подключено к Telegram")
    
    try:
        channel = await client.get_entity(int(CHANNEL_ID))
        print(f"✅ Канал найден: {getattr(channel, 'title', 'N/A')}")
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        await client.disconnect()
        return
    
    # Парсим сообщения
    print("\n📥 Парсинг сообщений...")
    offset_id = 0
    limit = 100
    messages = []
    media_dir = Path("media")
    media_dir.mkdir(exist_ok=True)
    
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
                text = ""
                if hasattr(msg, 'raw_text') and msg.raw_text:
                    text = msg.raw_text
                elif hasattr(msg, 'message') and msg.message:
                    text = msg.message
                
                media_info = None
                if msg.photo or msg.video or msg.document:
                    try:
                        media_filename = f"msg_{msg.id}"
                        if msg.photo:
                            media_path = await client.download_media(msg, file=f"{media_dir}/{media_filename}.jpg")
                            media_type = "photo"
                        elif msg.video:
                            media_path = await client.download_media(msg, file=f"{media_dir}/{media_filename}.mp4")
                            media_type = "video"
                        else:
                            media_path = await client.download_media(msg, file=f"{media_dir}/{media_filename}")
                            media_type = "document"
                        
                        if media_path:
                            media_info = {
                                "type": media_type,
                                "file_id": msg.id,
                                "path": str(media_path)
                            }
                    except:
                        pass
                
                messages.append({
                    "id": msg.id,
                    "date": msg.date.astimezone().strftime("%Y-%m-%d"),
                    "text": text,
                    "media": media_info
                })
            
            offset_id = history.messages[-1].id
            print(f"   Получено {len(messages)} сообщений...")
            
            if len(history.messages) < limit:
                break
        except Exception as e:
            print(f"❌ Ошибка: {e}")
            break
    
    await client.disconnect()
    
    print(f"\n✅ Получено {len(messages)} сообщений")
    
    # Группируем по дням
    from collections import defaultdict
    by_days = defaultdict(list)
    
    for msg in messages:
        if msg["text"].strip() or msg.get("media"):
            by_days[msg["date"]].append({
                "text": msg["text"].strip(),
                "media": msg.get("media")
            })
    
    # Создаем уроки
    lessons = {}
    sorted_dates = sorted(by_days.keys())[:30]  # Берем первые 30 дней
    
    for day_num, date in enumerate(sorted_dates, start=1):
        msgs = by_days[date]
        texts = [m["text"] for m in msgs if m["text"]]
        media_files = [m["media"] for m in msgs if m.get("media")]
        
        combined_text = "\n\n".join(texts)
        
        # Извлекаем задание
        task = ""
        if "🗝" in combined_text:
            parts = combined_text.split("🗝")
            if len(parts) > 1:
                task = "🗝" + parts[1].split("\n\n")[0]
        
        lessons[str(day_num)] = {
            "title": f"День {day_num}",
            "text": combined_text,
            "media": media_files[:5],
            "task": task,
            "task_basic": task,
            "task_feedback": task + "\n\n💡 Для тарифа с обратной связью: Опишите подробнее.",
            "buttons": ["submit_task", "ask_question", "discussion"],
            "silent": False
        }
    
    # Сохраняем
    lessons_file = Path("data/lessons.json")
    with open(lessons_file, "w", encoding="utf-8") as f:
        json.dump(lessons, f, ensure_ascii=False, indent=2)
    
    print(f"\n✅ Создано {len(lessons)} уроков в {lessons_file}")


if __name__ == "__main__":
    if TELETHON_AVAILABLE:
        asyncio.run(quick_parse())
    else:
        print("Установите telethon: pip install telethon")

