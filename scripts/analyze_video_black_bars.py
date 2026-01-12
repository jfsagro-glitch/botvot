"""
Анализ видео для определения черных полос и правильного кадрирования.
"""

import sys
import subprocess
from pathlib import Path
import json
import re

sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

FFMPEG_PATH = Path(r"C:\Users\79184.WIN-OOR1JAM5834\Downloads\ffmpeg-2026-01-05-git-2892815c45-essentials_build\bin\ffmpeg.exe")
FFPROBE_PATH = Path(r"C:\Users\79184.WIN-OOR1JAM5834\Downloads\ffmpeg-2026-01-05-git-2892815c45-essentials_build\bin\ffprobe.exe")

project_root = Path(__file__).resolve().parent.parent
VIDEO_PATH = project_root / "Photo" / "video_pic_optimized" / "001 Корвет.mp4"

def detect_crop_params(video_path):
    """Определяет параметры обрезки черных полос."""
    print("🔍 Анализирую видео для определения черных полос...")
    
    # Используем ffmpeg для определения черных полос
    # Анализируем первый кадр видео
    cmd = [
        str(FFMPEG_PATH),
        "-i", str(video_path),
        "-vf", "cropdetect=24:16:0",  # Порог 24, квадрат 16x16, режим 0
        "-vframes", "30",  # Анализируем первые 30 кадров
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
        
        # Ищем строки с crop параметрами в stderr
        crop_lines = []
        for line in result.stderr.split('\n'):
            if 'crop=' in line.lower():
                crop_lines.append(line.strip())
                print(f"   {line.strip()}")
        
        if crop_lines:
            # Берем последний найденный crop параметр (самый стабильный)
            last_crop = crop_lines[-1]
            # Извлекаем параметры crop=w:h:x:y
            match = re.search(r'crop=(\d+):(\d+):(\d+):(\d+)', last_crop)
            if match:
                w, h, x, y = map(int, match.groups())
                print(f"\n✅ Найдены параметры обрезки:")
                print(f"   Ширина: {w}px")
                print(f"   Высота: {h}px")
                print(f"   Смещение X: {x}px")
                print(f"   Смещение Y: {y}px")
                return w, h, x, y
        
        print("⚠️ Не удалось автоматически определить параметры обрезки")
        return None
        
    except Exception as e:
        print(f"❌ Ошибка при анализе: {e}")
        return None

def get_video_info(video_path):
    """Получает информацию о видео."""
    cmd = [
        str(FFPROBE_PATH),
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,display_aspect_ratio,duration",
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
                    "aspect_ratio": stream.get("display_aspect_ratio", "16:9"),
                    "duration": stream.get("duration")
                }
        return None
    except Exception as e:
        print(f"❌ Ошибка при получении информации: {e}")
        return None

if __name__ == "__main__":
    print("=" * 60)
    print("🔍 АНАЛИЗ ВИДЕО ДЛЯ ОПРЕДЕЛЕНИЯ ЧЕРНЫХ ПОЛОС")
    print("=" * 60)
    
    if not VIDEO_PATH.exists():
        print(f"❌ Файл не найден: {VIDEO_PATH}")
        sys.exit(1)
    
    print(f"\n📹 Исходное видео:")
    info = get_video_info(VIDEO_PATH)
    if info:
        print(f"   Разрешение: {info['width']}x{info['height']}")
        print(f"   Соотношение: {info['aspect_ratio']}")
        print(f"   Длительность: {info.get('duration', 'N/A')} сек")
    
    print(f"\n🔍 Определяю черные полосы...")
    crop_params = detect_crop_params(VIDEO_PATH)
    
    if crop_params:
        w, h, x, y = crop_params
        print(f"\n📐 Рекомендуемые параметры обрезки: crop={w}:{h}:{x}:{y}")
        print(f"\n💡 После обрезки соотношение сторон будет: {w/h:.2f}:1")
    else:
        print("\n⚠️ Рекомендую вручную определить параметры обрезки")
    
    print("=" * 60)
