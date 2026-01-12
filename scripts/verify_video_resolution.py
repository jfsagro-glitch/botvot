"""
Проверка финального разрешения видео урока 1.
"""

import subprocess
import json
import sys
from pathlib import Path

# Устанавливаем UTF-8 для вывода в консоль Windows
if sys.platform == 'win32':
    import codecs
    sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')
    sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer, 'strict')

project_root = Path(__file__).parent.parent
ffprobe = Path(r"C:\Users\79184.WIN-OOR1JAM5834\Downloads\ffmpeg-2026-01-05-git-2892815c45-essentials_build\bin\ffprobe.exe")
video = project_root / "Photo/video_pic_optimized/001 Корвет.mp4"

if not video.exists():
    print(f"❌ Видео не найдено: {video}")
    sys.exit(1)

cmd = [
    str(ffprobe),
    '-v', 'error',
    '-select_streams', 'v:0',
    '-show_entries', 'stream=width,height,display_aspect_ratio',
    '-of', 'json',
    str(video)
]

result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='ignore')

if result.returncode != 0:
    print(f"❌ Ошибка: {result.stderr}")
    sys.exit(1)

data = json.loads(result.stdout)
streams = data.get('streams', [])

if streams:
    stream = streams[0]
    width = stream.get('width', 0)
    height = stream.get('height', 0)
    aspect_ratio = stream.get('display_aspect_ratio', 'N/A')
    ratio = width / height if height > 0 else 0
    
    print("=" * 70)
    print("📹 Проверка финального разрешения видео урока 1")
    print("=" * 70)
    print()
    print(f"Ширина: {width}px")
    print(f"Высота: {height}px")
    print(f"Соотношение сторон: {aspect_ratio}")
    print(f"Соотношение (ширина/высота): {ratio:.2f}")
    print()
    
    if width == 960 and height == 600:
        print("✅ Разрешение корректное: 960x600")
    else:
        print(f"⚠️  Разрешение отличается от ожидаемого (960x600)")
    
    if ratio == 1.6:
        print("✅ Пропорции корректные: 1.6:1")
    else:
        print(f"⚠️  Пропорции: {ratio:.2f}:1")
    
    print()
    print("=" * 70)
