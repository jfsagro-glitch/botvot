"""
Проверка оригинального видео урока 1
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

ffprobe = Path(r'C:\Users\79184.WIN-OOR1JAM5834\Downloads\ffmpeg-2026-01-05-git-2892815c45-essentials_build\bin\ffprobe.exe')
video = Path('Photo/video_pic/001 Корвет.mp4')

if not video.exists():
    video = Path('C:/Users/79184.WIN-OOR1JAM5834/BOTVOT/Photo/video_pic/001 Корвет.mp4')

if not video.exists():
    print(f"❌ Видео не найдено: {video}")
    exit(1)

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
    exit(1)

data = json.loads(result.stdout)
streams = data.get('streams', [])

if streams:
    stream = streams[0]
    width = stream.get('width', 0)
    height = stream.get('height', 0)
    aspect_ratio = stream.get('display_aspect_ratio', 'N/A')
    ratio = width / height if height > 0 else 0
    
    print(f"📹 Оригинальное видео:")
    print(f"   Ширина: {width}px")
    print(f"   Высота: {height}px")
    print(f"   Соотношение сторон: {aspect_ratio}")
    print(f"   Соотношение (ширина/высота): {ratio:.2f}")
    
    if ratio < 0.8:
        print(f"   ⚠️ Видео слишком вертикальное (вертикальный формат)")
    elif ratio > 1.5:
        print(f"   ✅ Видео горизонтальное (нормальные пропорции)")
    else:
        print(f"   ⚠️ Видео может быть квадратным или близко к квадратному")
else:
    print("❌ Не удалось получить информацию о видео")
