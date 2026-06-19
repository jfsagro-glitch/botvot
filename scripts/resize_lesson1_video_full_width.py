"""
Скрипт для перекодирования видео урока 1 так, чтобы оно растягивалось по ширине экрана мобильного устройства.
Видео будет иметь ширину 1080px (стандартная ширина экрана мобильного устройства) и автоматически рассчитанную высоту.
"""

import sys
import subprocess
from pathlib import Path
import json

# Настройка кодировки для Windows
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

# Добавляем корень проекта в путь
project_root = Path(__file__).resolve().parent.parent
sys.path.append(str(project_root))

# Путь к ffmpeg
FFMPEG_PATH = Path(r"C:\Users\79184.WIN-OOR1JAM5834\Downloads\ffmpeg-2026-01-05-git-2892815c45-essentials_build\bin\ffmpeg.exe")
FFPROBE_PATH = Path(r"C:\Users\79184.WIN-OOR1JAM5834\Downloads\ffmpeg-2026-01-05-git-2892815c45-essentials_build\bin\ffprobe.exe")

# Путь к исходному видео
INPUT_VIDEO = project_root / "Photo" / "video_pic_optimized" / "001 Корвет.mp4"
OUTPUT_VIDEO = project_root / "Photo" / "video_pic_optimized" / "001 Корвет_fullwidth.mp4"

# Целевая ширина для мобильных устройств
TARGET_WIDTH = 1080

def get_video_info(video_path):
    """Получает информацию о видео."""
    try:
        cmd = [
            str(FFPROBE_PATH),
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height,display_aspect_ratio,duration",
            "-of", "json",
            str(video_path)
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8')
        if result.returncode == 0:
            data = json.loads(result.stdout)
            if "streams" in data and len(data["streams"]) > 0:
                stream = data["streams"][0]
                return {
                    "width": stream.get("width"),
                    "height": stream.get("height"),
                    "aspect_ratio": stream.get("display_aspect_ratio", "16:9"),
                    "duration": stream.get("duration")
                }
        print(f"❌ Ошибка при получении информации о видео: {result.stderr}")
        return None
    except Exception as e:
        print(f"❌ Исключение при получении информации о видео: {e}")
        return None

def resize_video_full_width(input_path, output_path, target_width=1080):
    """Перекодирует видео так, чтобы оно растягивалось по ширине экрана."""
    if not input_path.exists():
        print(f"❌ Файл не найден: {input_path}")
        return False
    
    # Получаем информацию о текущем видео
    info = get_video_info(input_path)
    if not info:
        print("❌ Не удалось получить информацию о видео")
        return False
    
    print(f"📹 Текущее разрешение: {info['width']}x{info['height']}")
    print(f"📐 Соотношение сторон: {info['aspect_ratio']}")
    
    # Вычисляем высоту для сохранения пропорций
    # Для мобильных устройств обычно используется соотношение 16:9
    target_height = int(target_width * 9 / 16)
    
    print(f"🎯 Целевое разрешение: {target_width}x{target_height}")
    
    # Команда ffmpeg для перекодирования
    # Используем scale для растягивания по ширине, сохраняя пропорции
    # force_original_aspect_ratio=increase обеспечит, что видео займет всю ширину экрана
    cmd = [
        str(FFMPEG_PATH),
        "-i", str(input_path),
        "-vf", f"scale={target_width}:{target_height}:force_original_aspect_ratio=increase,crop={target_width}:{target_height}",
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "23",
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",
        "-pix_fmt", "yuv420p",  # Важно для совместимости
        "-y",  # Перезаписать выходной файл
        str(output_path)
    ]
    
    print(f"\n🔄 Начинаю перекодирование...")
    print(f"📥 Входной файл: {input_path}")
    print(f"📤 Выходной файл: {output_path}")
    
    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding='utf-8',
            errors='replace'
        )
        
        # Выводим прогресс
        while True:
            output = process.stderr.readline()
            if output == '' and process.poll() is not None:
                break
            if output and ('frame=' in output or 'time=' in output):
                print(output.strip(), end='\r')
        
        returncode = process.poll()
        
        if returncode == 0:
            print(f"\n✅ Видео успешно перекодировано!")
            
            # Проверяем результат
            output_info = get_video_info(output_path)
            if output_info:
                print(f"📹 Результирующее разрешение: {output_info['width']}x{output_info['height']}")
                print(f"📊 Размер файла: {output_path.stat().st_size / 1024 / 1024:.2f} MB")
            
            return True
        else:
            stderr_output = process.stderr.read()
            print(f"\n❌ Ошибка при перекодировании: {stderr_output}")
            return False
            
    except Exception as e:
        print(f"❌ Исключение при перекодировании: {e}")
        return False

if __name__ == "__main__":
    print("=" * 60)
    print("🎬 ПЕРЕКОДИРОВАНИЕ ВИДЕО УРОКА 1 ДЛЯ ПОЛНОЙ ШИРИНЫ ЭКРАНА")
    print("=" * 60)
    
    if not FFMPEG_PATH.exists():
        print(f"❌ ffmpeg не найден по пути: {FFMPEG_PATH}")
        sys.exit(1)
    
    if not FFPROBE_PATH.exists():
        print(f"❌ ffprobe не найден по пути: {FFPROBE_PATH}")
        sys.exit(1)
    
    success = resize_video_full_width(INPUT_VIDEO, OUTPUT_VIDEO, TARGET_WIDTH)
    
    if success:
        print("\n✅ Готово! Видео перекодировано для полной ширины экрана.")
        print(f"📁 Файл: {OUTPUT_VIDEO}")
    else:
        print("\n❌ Ошибка при перекодировании видео.")
        sys.exit(1)
