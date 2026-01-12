"""
Исправление видео урока 1: обрезка черных полос и растягивание контента по ширине экрана.
"""

import sys
import subprocess
from pathlib import Path
import json
import shutil

sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

FFMPEG_PATH = Path(r"C:\Users\79184.WIN-OOR1JAM5834\Downloads\ffmpeg-2026-01-05-git-2892815c45-essentials_build\bin\ffmpeg.exe")
FFPROBE_PATH = Path(r"C:\Users\79184.WIN-OOR1JAM5834\Downloads\ffmpeg-2026-01-05-git-2892815c45-essentials_build\bin\ffprobe.exe")

project_root = Path(__file__).resolve().parent.parent

# Проверяем оригинальный файл, если он лучше
ORIGINAL_VIDEO = project_root / "Photo" / "video_pic" / "001 Корвет.mp4"
CURRENT_VIDEO = project_root / "Photo" / "video_pic_optimized" / "001 Корвет.mp4"
BACKUP_VIDEO = project_root / "Photo" / "video_pic_optimized" / "001 Корвет_backup_final.mp4"
OUTPUT_VIDEO = project_root / "Photo" / "video_pic_optimized" / "001 Корвет.mp4"

# Целевая ширина для мобильных устройств
TARGET_WIDTH = 1080

def get_video_info(video_path):
    """Получает информацию о видео."""
    cmd = [
        str(FFPROBE_PATH),
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,display_aspect_ratio",
        "-of", "json",
        str(video_path)
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8')
        if result.returncode == 0:
            data = json.loads(result.stdout)
            if "streams" in data and len(data["streams"]) > 0:
                stream = data["streams"][0]
                return {
                    "width": stream.get("width"),
                    "height": stream.get("height"),
                    "aspect_ratio": stream.get("display_aspect_ratio", "16:9")
                }
        return None
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        return None

def detect_black_bars(video_path):
    """Определяет черные полосы и возвращает параметры обрезки."""
    print("🔍 Анализирую черные полосы...")
    
    cmd = [
        str(FFMPEG_PATH),
        "-i", str(video_path),
        "-vf", "cropdetect=24:2:0",  # Более чувствительный порог
        "-t", "3",  # Анализируем первые 3 секунды
        "-f", "null",
        "-"
    ]
    
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='ignore',
            timeout=30
        )
        
        # Ищем стабильные параметры crop в stderr
        crops = []
        for line in result.stderr.split('\n'):
            if 'crop=' in line.lower() and 'w:' in line.lower():
                # Ищем строку вида crop=336:432:372:30
                import re
                match = re.search(r'crop=(\d+):(\d+):(\d+):(\d+)', line)
                if match:
                    w, h, x, y = map(int, match.groups())
                    # Фильтруем невалидные значения
                    if w > 100 and h > 100 and x >= 0 and y >= 0:
                        crops.append((w, h, x, y))
        
        if crops:
            # Берем наиболее часто встречающиеся значения
            # Или берем медианное значение для стабильности
            crops.sort()
            mid = len(crops) // 2
            # Берем значения из середины списка, но ищем самые большие размеры
            # (чтобы не обрезать слишком много)
            crops_by_area = sorted(crops, key=lambda x: x[0] * x[1], reverse=True)
            w, h, x, y = crops_by_area[0]  # Берем самый большой контент
            
            print(f"   ✅ Найдены параметры обрезки: {w}x{h} на позиции ({x}, {y})")
            return w, h, x, y
        
        return None
    except Exception as e:
        print(f"   ⚠️ Ошибка при детекции: {e}")
        return None

def process_video(input_path, output_path, crop_w, crop_h, crop_x, crop_y, target_width):
    """Обрезает черные полосы и растягивает контент по ширине экрана."""
    print(f"\n🔧 Обрабатываю видео...")
    print(f"   1. Обрезаю черные полосы: crop={crop_w}:{crop_h}:{crop_x}:{crop_y}")
    print(f"   2. Растягиваю до ширины {target_width}px с сохранением пропорций")
    
    # Вычисляем целевую высоту для сохранения пропорций обрезанного контента
    aspect_ratio = crop_w / crop_h
    target_height = int(target_width / aspect_ratio)
    
    print(f"   После обрезки соотношение: {aspect_ratio:.2f}:1")
    print(f"   Целевое разрешение: {target_width}x{target_height}")
    
    # Команда: сначала обрезаем черные полосы, потом растягиваем до целевой ширины
    cmd = [
        str(FFMPEG_PATH),
        "-i", str(input_path),
        "-vf", f"crop={crop_w}:{crop_h}:{crop_x}:{crop_y},scale={target_width}:{target_height}",
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "23",
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",
        "-pix_fmt", "yuv420p",
        "-y",
        str(output_path)
    ]
    
    try:
        print(f"   Обработка...")
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=300
        )
        
        if result.returncode == 0:
            print(f"   ✅ Видео обработано успешно!")
            return True
        else:
            print(f"   ❌ Ошибка обработки:")
            print(result.stderr[-500:])  # Последние 500 символов ошибки
            return False
    except subprocess.TimeoutExpired:
        print(f"   ❌ Таймаут при обработке")
        return False
    except Exception as e:
        print(f"   ❌ Исключение: {e}")
        return False

if __name__ == "__main__":
    print("=" * 60)
    print("🎬 ИСПРАВЛЕНИЕ ВИДЕО: ОБРЕЗКА ЧЕРНЫХ ПОЛОС И РАСТЯГИВАНИЕ")
    print("=" * 60)
    
    # Выбираем исходный файл (оригинал или текущий)
    input_video = ORIGINAL_VIDEO if ORIGINAL_VIDEO.exists() else CURRENT_VIDEO
    
    if not input_video.exists():
        print(f"❌ Исходный файл не найден!")
        sys.exit(1)
    
    print(f"\n📹 Исходное видео: {input_video.name}")
    info = get_video_info(input_video)
    if info:
        print(f"   Разрешение: {info['width']}x{info['height']}")
        print(f"   Соотношение: {info['aspect_ratio']}")
    
    # Определяем черные полосы
    crop_params = detect_black_bars(input_video)
    
    if not crop_params:
        print("\n⚠️ Не удалось автоматически определить черные полосы")
        print("   Использую ручные параметры на основе анализа...")
        # Из анализа выше: crop=336:432:372:30, но возьмем чуть больше для безопасности
        crop_params = (350, 440, 365, 25)  # Немного больше для безопасности
    
    crop_w, crop_h, crop_x, crop_y = crop_params
    
    # Создаем backup
    if CURRENT_VIDEO.exists() and not BACKUP_VIDEO.exists():
        shutil.copy2(CURRENT_VIDEO, BACKUP_VIDEO)
        print(f"\n📦 Создан backup: {BACKUP_VIDEO.name}")
    
    # Обрабатываем видео
    if process_video(input_video, OUTPUT_VIDEO, crop_w, crop_h, crop_x, crop_y, TARGET_WIDTH):
        # Проверяем результат
        print(f"\n📹 Проверка результата:")
        result_info = get_video_info(OUTPUT_VIDEO)
        if result_info:
            print(f"   Разрешение: {result_info['width']}x{result_info['height']}")
            print(f"   Соотношение: {result_info['aspect_ratio']}")
            file_size_mb = OUTPUT_VIDEO.stat().st_size / 1024 / 1024
            print(f"   Размер: {file_size_mb:.2f} MB")
            
            if result_info['width'] == TARGET_WIDTH:
                print(f"\n✅ Успешно! Видео готово к загрузке в Telegram")
            else:
                print(f"\n⚠️ Ширина не соответствует целевой ({TARGET_WIDTH}px)")
        
        print("=" * 60)
    else:
        print("\n❌ Ошибка при обработке видео")
        sys.exit(1)
