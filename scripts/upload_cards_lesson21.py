"""
Скрипт для загрузки карточек урока 21 в Telegram и получения file_id
"""
import sys
import json
import asyncio
from pathlib import Path
from aiogram import Bot
from aiogram.types import FSInputFile

# Добавляем корень проекта в путь
sys.path.append(str(Path(__file__).resolve().parent.parent))

from core.config import Config

# Настройка кодировки для Windows
if sys.platform == 'win32':
    import codecs
    sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')
    sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer, 'strict')

async def upload_cards():
    """Загружает карточки урока 21 в Telegram и получает file_id"""
    bot = Bot(token=Config.COURSE_BOT_TOKEN)
    admin_chat_id = Config.ADMIN_CHAT_ID
    
    # Путь к директории с карточками
    cards_dir = Path(r"C:\Users\79184.WIN-OOR1JAM5834\BOTVOT\Photo\video_pic\021 Карточки")
    
    # Получаем все файлы карточек
    card_files = sorted(cards_dir.glob("*.jpg"))
    
    print(f"Найдено {len(card_files)} карточек")
    
    cards_data = []
    
    for i, card_file in enumerate(card_files, 1):
        print(f"\n[{i}/{len(card_files)}] Загружаю {card_file.name}...")
        
        try:
            # Отправляем фото в админ-чат
            photo_file = FSInputFile(card_file)
            sent_message = await bot.send_photo(
                chat_id=admin_chat_id,
                photo=photo_file,
                caption=f"Карточка {i} из урока 21"
            )
            
            # Получаем file_id
            file_id = sent_message.photo[-1].file_id  # Берем самое большое фото
            
            cards_data.append({
                "number": i,
                "filename": card_file.name,
                "path": f"Photo/video_pic/021 Карточки/{card_file.name}",
                "file_id": file_id
            })
            
            print(f"✅ Загружено: {card_file.name}")
            print(f"   file_id: {file_id}")
            
            # Небольшая пауза между загрузками
            await asyncio.sleep(0.5)
            
        except Exception as e:
            print(f"❌ Ошибка при загрузке {card_file.name}: {e}")
            cards_data.append({
                "number": i,
                "filename": card_file.name,
                "path": f"Photo/video_pic/021 Карточки/{card_file.name}",
                "file_id": None
            })
    
    # Сохраняем данные в JSON файл
    output_file = Path(__file__).parent.parent / "data" / "lesson21_cards.json"
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(cards_data, f, ensure_ascii=False, indent=2)
    
    print(f"\n✅ Данные сохранены в {output_file}")
    print(f"\nВсего карточек: {len(cards_data)}")
    print(f"Успешно загружено: {sum(1 for c in cards_data if c['file_id'])}")
    
    await bot.session.close()

if __name__ == "__main__":
    asyncio.run(upload_cards())
