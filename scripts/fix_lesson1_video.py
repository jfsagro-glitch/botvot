"""
Скрипт для проверки и исправления пропорций видео урока 1.
"""

import json
import sys
import subprocess
from pathlib import Path

# Добавляем корень проекта в PYTHONPATH
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Устанавливаем UTF-8 для вывода в консоль Windows
if sys.platform == 'win32':
    import codecs
    sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')
    sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer, 'strict')

def check_video_properties(video_path: Path, ffprobe_path: Path = None):
    """Проверяет свойства видео."""
    if ffprobe_path is None:
        # Пробуем найти ffprobe
        possible_paths = [
            Path(r"C:\Users\79184.WIN-OOR1JAM5834\Downloads\ffmpeg-2026-01-05-git-2892815c45-essentials_build\bin\ffprobe.exe"),
            Path("ffprobe.exe"),
            Path("ffprobe"),
        ]
        ffprobe_path = None
        for path in possible_paths:
            if path.exists():
                ffprobe_path = path
                break
        
        if not ffprobe_path:
            print("❌ ffprobe не найден!")
            return None
    
    try:
        cmd = [
            str(ffprobe_path),
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height,display_aspect_ratio",
            "-of", "json",
            str(video_path)
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8')
        
        if result.returncode != 0:
            print(f"❌ Ошибка при проверке видео: {result.stderr}")
            return None
        
        data = json.loads(result.stdout)
        streams = data.get("streams", [])
        if streams:
            stream = streams[0]
            width = stream.get("width", 0)
            height = stream.get("height", 0)
            aspect_ratio = stream.get("display_aspect_ratio", "N/A")
            
            print(f"📹 Свойства видео:")
            print(f"   Ширина: {width}px")
            print(f"   Высота: {height}px")
            print(f"   Соотношение сторон: {aspect_ratio}")
            print(f"   Соотношение (ширина/высота): {width/height:.2f}" if height > 0 else "")
            
            return {
                "width": width,
                "height": height,
                "aspect_ratio": aspect_ratio,
                "ratio": width / height if height > 0 else 0
            }
        else:
            print("❌ Не удалось получить информацию о видео")
            return None
            
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        return None

def fix_video_aspect_ratio(input_path: Path, output_path: Path, ffmpeg_path: Path = None, target_aspect=16/9):
    """Исправляет пропорции видео, приводя к целевому соотношению сторон."""
    if ffmpeg_path is None:
        # Пробуем найти ffmpeg
        possible_paths = [
            Path(r"C:\Users\79184.WIN-OOR1JAM5834\Downloads\ffmpeg-2026-01-05-git-2892815c45-essentials_build\bin\ffmpeg.exe"),
            Path("ffmpeg.exe"),
            Path("ffmpeg"),
        ]
        ffmpeg_path = None
        for path in possible_paths:
            if path.exists():
                ffmpeg_path = path
                break
        
        if not ffmpeg_path:
            print("❌ ffmpeg не найден!")
            return False
    
    try:
        # Проверяем текущие пропорции
        props = check_video_properties(input_path)
        if not props:
            return False
        
        width = props["width"]
        height = props["height"]
        current_ratio = props["ratio"]
        
        print(f"\n🔧 Исправление пропорций видео...")
        print(f"   Текущее соотношение: {current_ratio:.2f}")
        print(f"   Целевое соотношение: {target_aspect:.2f}")
        
        # Если видео слишком вертикальное (высота больше ширины или очень узкое)
        # Применяем crop или scale для нормализации
        if current_ratio < 0.7:  # Слишком вертикальное (например, 9:16)
            print(f"   Видео слишком вертикальное, применяем crop/scale...")
            
            # Вычисляем новые размеры с учетом целевого соотношения
            if height * target_aspect <= width:
                # Ширина достаточна, обрезаем по высоте
                new_height = int(width / target_aspect)
                new_width = width
                crop_x = 0
                crop_y = int((height - new_height) / 2)
            else:
                # Высота достаточна, обрезаем по ширине
                new_width = int(height * target_aspect)
                new_height = height
                crop_x = int((width - new_width) / 2)
                crop_y = 0
            
            # Команда ffmpeg с crop и scale
            cmd = [
                str(ffmpeg_path),
                "-i", str(input_path),
                "-vf", f"crop={new_width}:{new_height}:{crop_x}:{crop_y},scale=1920:-2",
                "-c:v", "libx264",
                "-preset", "medium",
                "-crf", "23",
                "-c:a", "copy",  # Копируем аудио без изменений
                "-y",  # Перезаписать выходной файл
                str(output_path)
            ]
        elif current_ratio > 2.5:  # Слишком горизонтальное (широкое)
            print(f"   Видео слишком горизонтальное, применяем scale...")
            cmd = [
                str(ffmpeg_path),
                "-i", str(input_path),
                "-vf", f"scale=1920:-2",
                "-c:v", "libx264",
                "-preset", "medium",
                "-crf", "23",
                "-c:a", "copy",
                "-y",
                str(output_path)
            ]
        else:
            # Пропорции нормальные, просто нормализуем размер
            print(f"   Пропорции нормальные, применяем только scale...")
            cmd = [
                str(ffmpeg_path),
                "-i", str(input_path),
                "-vf", f"scale=1920:-2",
                "-c:v", "libx264",
                "-preset", "medium",
                "-crf", "23",
                "-c:a", "copy",
                "-y",
                str(output_path)
            ]
        
        print(f"   Команда: {' '.join(cmd)}")
        print(f"   Обработка видео...")
        
        result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='ignore')
        
        if result.returncode != 0:
            print(f"❌ Ошибка при обработке видео:")
            print(result.stderr)
            return False
        
        print(f"✅ Видео успешно обработано!")
        
        # Проверяем результат
        print(f"\n📹 Проверка результата:")
        check_video_properties(output_path)
        
        return True
        
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    print("=" * 70)
    print("🔧 Проверка и исправление пропорций видео урока 1")
    print("=" * 70)
    print()
    
    video_path = project_root / "Photo" / "video_pic_optimized" / "001 Корвет.mp4"
    
    if not video_path.exists():
        print(f"❌ Видео не найдено: {video_path}")
        sys.exit(1)
    
    # Проверяем текущие пропорции
    print("📹 Проверка текущих пропорций видео:")
    props = check_video_properties(video_path)
    
    if props:
        print()
        
        # Создаем резервную копию
        backup_path = video_path.with_suffix('.mp4.backup')
        if not backup_path.exists():
            import shutil
            shutil.copy2(video_path, backup_path)
            print(f"💾 Создана резервная копия: {backup_path}")
        else:
            print(f"⏭️  Резервная копия уже существует: {backup_path}")
        
        # Создаем временный файл для результата
        temp_output = video_path.with_suffix('.fixed.mp4')
        
        # Исправляем пропорции
        if fix_video_aspect_ratio(video_path, temp_output):
            # Заменяем оригинальный файл
            video_path.unlink()
            temp_output.rename(video_path)
            print(f"\n✅ Видео успешно исправлено и сохранено: {video_path}")
        else:
            print(f"\n❌ Не удалось исправить видео")
            if temp_output.exists():
                temp_output.unlink()
    
    print()
    print("=" * 70)
