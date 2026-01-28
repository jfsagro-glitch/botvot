"""
Скрипт для обработки видео урока 1 под формат Telegram.

Обрабатывает видео:
- Конвертирует в горизонтальный формат (если нужно)
- Оптимизирует разрешение для Telegram (720px ширина)
- Сжимает до оптимального размера
- Сохраняет обработанное видео
"""

import asyncio
import subprocess
import logging
from pathlib import Path
import sys

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Ширина для Telegram (мобильные устройства)
TELEGRAM_VIDEO_WIDTH = 720
MAX_VIDEO_SIZE_MB = 45.0  # Лимит Telegram - 50 МБ, оставляем запас


async def get_video_info(video_path: Path) -> dict:
    """Получает информацию о видео через ffprobe."""
    try:
        cmd = [
            "ffprobe",
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height,duration,codec_name",
            "-of", "json",
            str(video_path)
        ]
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30
        )
        
        if result.returncode != 0:
            logger.error(f"❌ ffprobe failed: {result.stderr}")
            return None
        
        import json
        data = json.loads(result.stdout)
        stream = data.get("streams", [{}])[0]
        
        return {
            "width": int(stream.get("width", 0)),
            "height": int(stream.get("height", 0)),
            "duration": float(stream.get("duration", 0)),
            "codec": stream.get("codec_name", "unknown")
        }
    except Exception as e:
        logger.error(f"❌ Error getting video info: {e}", exc_info=True)
        return None


async def process_video_for_telegram(input_path: Path, output_path: Path) -> bool:
    """
    Обрабатывает видео для Telegram:
    - Конвертирует в горизонтальный формат (если вертикальное)
    - Оптимизирует разрешение (ширина 720px)
    - Сжимает до оптимального размера
    """
    logger.info(f"📹 Начинаю обработку видео: {input_path.name}")
    
    # Получаем информацию о видео
    info = await get_video_info(input_path)
    if not info:
        logger.warning("⚠️ Не удалось получить информацию о видео, использую стандартные параметры")
        # Используем стандартную обработку без информации о видео
        video_filter = f"scale={TELEGRAM_VIDEO_WIDTH}:-2"
    else:
        width = info["width"]
        height = info["height"]
        logger.info(f"   📐 Исходное разрешение: {width}x{height}")
        
        # Определяем, нужно ли поворачивать (вертикальное -> горизонтальное)
        is_vertical = height > width
        target_width = TELEGRAM_VIDEO_WIDTH
        
        if is_vertical:
            logger.info("   🔄 Видео вертикальное, конвертирую в горизонтальное...")
            # Поворачиваем на 90 градусов (transpose=1) и масштабируем
            video_filter = f"transpose=1,scale={target_width}:-2"
        else:
            # Горизонтальное - просто масштабируем по ширине
            video_filter = f"scale={target_width}:-2"
        
        logger.info(f"   🎯 Целевое разрешение: {target_width}px ширина (высота пропорционально)")
    
    # Создаем директорию для выходного файла
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Команда FFmpeg для обработки
    cmd = [
        "ffmpeg",
        "-i", str(input_path),
        "-vf", video_filter,
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "23",  # Хорошее качество (18-28, меньше = лучше качество)
        "-maxrate", "2000k",
        "-bufsize", "4000k",
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",  # Оптимизация для стриминга
        "-y",  # Перезаписать существующий файл
        str(output_path)
    ]
    
    logger.info(f"   🔄 Запускаю обработку...")
    logger.info(f"   📝 Команда: {' '.join(cmd)}")
    
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        stdout, stderr = await process.communicate()
        
        if process.returncode != 0:
            logger.error(f"❌ FFmpeg failed: {stderr.decode('utf-8', errors='ignore')}")
            return False
        
        # Проверяем размер выходного файла
        if output_path.exists():
            size_mb = output_path.stat().st_size / (1024 * 1024)
            logger.info(f"   ✅ Видео обработано успешно!")
            logger.info(f"   📦 Размер: {size_mb:.2f} MB")
            
            if size_mb > MAX_VIDEO_SIZE_MB:
                logger.warning(f"   ⚠️ Видео все еще большое ({size_mb:.2f} MB), применяю дополнительное сжатие...")
                return await compress_video_aggressive(output_path, output_path)
            
            return True
        else:
            logger.error("❌ Выходной файл не создан")
            return False
            
    except Exception as e:
        logger.error(f"❌ Ошибка при обработке видео: {e}", exc_info=True)
        return False


async def compress_video_aggressive(input_path: Path, output_path: Path) -> bool:
    """Дополнительное агрессивное сжатие видео."""
    logger.info("   🔄 Применяю агрессивное сжатие...")
    
    cmd = [
        "ffmpeg",
        "-i", str(input_path),
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "28",  # Более высокое значение = меньше размер
        "-maxrate", "1500k",
        "-bufsize", "3000k",
        "-vf", f"scale={TELEGRAM_VIDEO_WIDTH}:-2",
        "-c:a", "aac",
        "-b:a", "96k",
        "-movflags", "+faststart",
        "-y",
        str(output_path)
    ]
    
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        stdout, stderr = await process.communicate()
        
        if process.returncode == 0 and output_path.exists():
            size_mb = output_path.stat().st_size / (1024 * 1024)
            logger.info(f"   ✅ Агрессивное сжатие завершено: {size_mb:.2f} MB")
            return True
        else:
            logger.error(f"❌ Агрессивное сжатие не удалось: {stderr.decode('utf-8', errors='ignore')}")
            return False
    except Exception as e:
        logger.error(f"❌ Ошибка при агрессивном сжатии: {e}", exc_info=True)
        return False


async def main():
    """Основная функция."""
    # Пути к файлам
    project_root = Path(__file__).parent.parent
    input_video = project_root / "VIDEO" / "1" / "document_5461089794907998389.mp4"
    output_video = project_root / "VIDEO" / "1" / "lesson1_telegram_optimized.mp4"
    
    logger.info("=" * 60)
    logger.info("🎬 Обработка видео урока 1 для Telegram")
    logger.info("=" * 60)
    
    # Проверяем наличие исходного файла
    if not input_video.exists():
        logger.error(f"❌ Исходное видео не найдено: {input_video}")
        return 1
    
    logger.info(f"📁 Исходный файл: {input_video}")
    logger.info(f"📁 Выходной файл: {output_video}")
    
    # Проверяем наличие FFmpeg
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True, timeout=5)
        logger.info("✅ FFmpeg найден")
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        logger.error("❌ FFmpeg не найден. Установите FFmpeg для обработки видео.")
        return 1
    
    # Обрабатываем видео
    success = await process_video_for_telegram(input_video, output_video)
    
    if success:
        logger.info("=" * 60)
        logger.info("✅ Видео успешно обработано!")
        logger.info(f"📁 Обработанное видео: {output_video}")
        logger.info("=" * 60)
        logger.info("💡 Следующий шаг: обновите путь в data/lessons.json")
        logger.info(f"   Замените путь на: VIDEO/1/lesson1_telegram_optimized.mp4")
        return 0
    else:
        logger.error("=" * 60)
        logger.error("❌ Обработка видео не удалась")
        logger.error("=" * 60)
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
