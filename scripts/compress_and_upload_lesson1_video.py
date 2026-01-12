"""
Сжатие и загрузка исправленного видео урока 1 в Telegram.
"""

import sys
import asyncio
from pathlib import Path
import json
import subprocess

sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

project_root = Path(__file__).resolve().parent.parent
sys.path.append(str(project_root))

FFMPEG_PATH = Path(r"C:\Users\79184.WIN-OOR1JAM5834\Downloads\ffmpeg-2026-01-05-git-2892815c45-essentials_build\bin\ffmpeg.exe")

from core.config import Config
from aiogram import Bot
from aiogram.types import FSInputFile
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

def compress_video(input_path: Path, output_path: Path, target_size_mb: float = 8.0):
    """Сжимает видео до целевого размера."""
    file_size_mb = input_path.stat().st_size / 1024 / 1024
    
    if file_size_mb <= target_size_mb:
        print(f"✅ Размер уже оптимален: {file_size_mb:.2f} MB")
        return True
    
    print(f"📊 Текущий размер: {file_size_mb:.2f} MB")
    print(f"🎯 Целевой размер: {target_size_mb:.2f} MB")
    print(f"🔄 Сжимаю...")
    
    # Используем двухпроходное кодирование для лучшего контроля размера
    # Первый проход - анализ
    cmd_pass1 = [
        str(FFMPEG_PATH),
        "-i", str(input_path),
        "-vf", "scale=1080:-2",  # Сохраняем пропорции
        "-c:v", "libx264",
        "-preset", "slow",
        "-b:v", "1500k",  # Битрейт ~1.5 Mbps
        "-maxrate", "2000k",
        "-bufsize", "3000k",
        "-pass", "1",
        "-passlogfile", str(output_path.parent / "ffmpeg2pass"),
        "-f", "null",
        "-y",
        "NUL"  # Windows null device
    ]
    
    # Второй проход - кодирование
    cmd_pass2 = [
        str(FFMPEG_PATH),
        "-i", str(input_path),
        "-vf", "scale=1080:-2",
        "-c:v", "libx264",
        "-preset", "slow",
        "-b:v", "1500k",
        "-maxrate", "2000k",
        "-bufsize", "3000k",
        "-pass", "2",
        "-passlogfile", str(output_path.parent / "ffmpeg2pass"),
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",
        "-pix_fmt", "yuv420p",
        "-y",
        str(output_path)
    ]
    
    try:
        # Первый проход
        print("   Проход 1/2...")
        subprocess.run(cmd_pass1, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=300)
        
        # Второй проход
        print("   Проход 2/2...")
        result = subprocess.run(cmd_pass2, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=600)
        
        # Удаляем временные файлы
        log_file = output_path.parent / "ffmpeg2pass-0.log"
        if log_file.exists():
            log_file.unlink()
        
        if result.returncode == 0:
            new_size_mb = output_path.stat().st_size / 1024 / 1024
            print(f"✅ Сжато до: {new_size_mb:.2f} MB")
            return True
        else:
            print(f"⚠️ Ошибка при сжатии, но файл может быть создан")
            if output_path.exists():
                return True
            return False
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        return False

async def upload_video_with_retry(bot: Bot, chat_id: int, video_path: Path, 
                                  caption: str = None, max_retries: int = 3):
    """Загружает видео с повторными попытками."""
    for attempt in range(max_retries):
        try:
            print(f"📤 Попытка {attempt + 1}/{max_retries}...")
            video_file = FSInputFile(video_path)
            
            request_timeout = 300 if attempt == 0 else 600
            
            message = await bot.send_video(
                chat_id=chat_id,
                video=video_file,
                caption=caption,
                width=1080,
                height=1924,
                supports_streaming=True,
                request_timeout=request_timeout
            )
            
            if message.video:
                return message.video.file_id
            else:
                raise ValueError("Не удалось получить video из сообщения")
                
        except Exception as e:
            if attempt < max_retries - 1:
                delay = (attempt + 1) * 10
                print(f"⚠️ Ошибка (попытка {attempt + 1}): {str(e)[:100]}")
                print(f"🔄 Повтор через {delay} секунд...")
                await asyncio.sleep(delay)
            else:
                print(f"❌ Не удалось загрузить после {max_retries} попыток: {e}")
                raise

async def main():
    """Основная функция."""
    print("=" * 60)
    print("📤 СЖАТИЕ И ЗАГРУЗКА ИСПРАВЛЕННОГО ВИДЕО")
    print("=" * 60)
    
    video_path = project_root / "Photo" / "video_pic_optimized" / "001 Корвет.mp4"
    compressed_path = project_root / "Photo" / "video_pic_optimized" / "001 Корвет_compressed.mp4"
    
    if not video_path.exists():
        print(f"❌ Файл не найден: {video_path}")
        return False
    
    # Сжимаем видео
    print(f"\n🎬 Сжатие видео...")
    if not compress_video(video_path, compressed_path, target_size_mb=8.0):
        print("⚠️ Продолжаю с исходным файлом...")
        compressed_path = video_path
    
    file_size_mb = compressed_path.stat().st_size / 1024 / 1024
    print(f"\n📊 Итоговый размер: {file_size_mb:.2f} MB")
    
    # Инициализируем бота
    bot = Bot(
        token=Config.COURSE_BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    
    admin_chat_id = Config.ADMIN_CHAT_ID
    
    print(f"\n📤 Загрузка в Telegram...")
    print(f"   Чат: {admin_chat_id}")
    
    try:
        # Загружаем видео
        file_id = await upload_video_with_retry(
            bot,
            admin_chat_id,
            compressed_path,
            caption="🎬 Видео урока 1 (обрезка черных полос, растянуто по ширине)"
        )
        
        print(f"\n✅ Видео загружено!")
        print(f"📋 file_id: {file_id}")
        
        # Обновляем lessons.json
        lessons_json_path = project_root / "data" / "lessons.json"
        print(f"\n📝 Обновляю lessons.json...")
        
        with open(lessons_json_path, 'r', encoding='utf-8') as f:
            lessons_data = json.load(f)
        
        if "1" in lessons_data:
            media_list = lessons_data["1"].get("media", [])
            updated = False
            for media_item in media_list:
                if media_item.get("type") == "video" and "001 Корвет" in media_item.get("path", ""):
                    media_item["file_id"] = file_id
                    print(f"✅ Обновлен file_id")
                    updated = True
                    break
            
            if updated:
                with open(lessons_json_path, 'w', encoding='utf-8') as f:
                    json.dump(lessons_data, f, ensure_ascii=False, indent=2)
                print(f"✅ lessons.json сохранен")
        
        # Заменяем исходный файл сжатым
        if compressed_path != video_path:
            video_path.unlink()
            compressed_path.rename(video_path)
            print(f"✅ Файл заменен сжатой версией")
        
        print("\n" + "=" * 60)
        print("✅ ГОТОВО!")
        print("=" * 60)
        return True
        
    except Exception as e:
        print(f"\n❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        await bot.session.close()

if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
