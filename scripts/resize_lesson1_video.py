"""
Скрипт для изменения разрешения видео урока 1 до 960x600 с сохранением пропорций.
"""

import subprocess
import sys
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
        import json
        cmd = [
            str(ffprobe_path),
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height,display_aspect_ratio",
            "-of", "json",
            str(video_path)
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='ignore')
        
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
            ratio = width / height if height > 0 else 0
            
            return {
                "width": width,
                "height": height,
                "aspect_ratio": aspect_ratio,
                "ratio": ratio
            }
        else:
            print("❌ Не удалось получить информацию о видео")
            return None
            
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        return None

def resize_video_to_960x600(input_path: Path, output_path: Path, ffmpeg_path: Path = None):
    """Изменяет разрешение видео до 960x600 с сохранением пропорций."""
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
        target_width = 960
        target_height = 600
        target_ratio = target_width / target_height  # 1.6
        
        print(f"📹 Текущие свойства видео:")
        print(f"   Ширина: {width}px")
        print(f"   Высота: {height}px")
        print(f"   Соотношение: {current_ratio:.2f}")
        print()
        print(f"🎯 Целевое разрешение:")
        print(f"   Ширина: {target_width}px")
        print(f"   Высота: {target_height}px")
        print(f"   Соотношение: {target_ratio:.2f}")
        print()
        
        # Вычисляем новые размеры с сохранением пропорций
        # Вписываем видео в рамку 960x600 с сохранением пропорций (letterboxing или pillarboxing)
        if current_ratio > target_ratio:
            # Видео шире, чем целевое соотношение - добавляем черные полосы сверху/снизу (letterboxing)
            new_width = target_width
            new_height = int(target_width / current_ratio)
            pad_top = (target_height - new_height) // 2
            pad_bottom = target_height - new_height - pad_top
            scale_filter = f"scale={new_width}:{new_height}"
            pad_filter = f"pad={target_width}:{target_height}:0:{pad_top}:black"
            vf = f"{scale_filter},{pad_filter}"
        else:
            # Видео выше, чем целевое соотношение - добавляем черные полосы слева/справа (pillarboxing)
            new_width = int(target_height * current_ratio)
            new_height = target_height
            pad_left = (target_width - new_width) // 2
            pad_right = target_width - new_width - pad_left
            scale_filter = f"scale={new_width}:{new_height}"
            pad_filter = f"pad={target_width}:{target_height}:{pad_left}:0:black"
            vf = f"{scale_filter},{pad_filter}"
        
        print(f"🔧 Применяемые фильтры:")
        print(f"   Scale: {scale_filter}")
        print(f"   Pad: {pad_filter}")
        print()
        
        # Команда ffmpeg
        cmd = [
            str(ffmpeg_path),
            "-i", str(input_path),
            "-vf", vf,
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "23",
            "-c:a", "copy",  # Копируем аудио без изменений
            "-y",  # Перезаписать выходной файл
            str(output_path)
        ]
        
        print(f"🔄 Обработка видео...")
        print(f"   Команда: {' '.join(cmd)}")
        
        result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='ignore')
        
        if result.returncode != 0:
            print(f"❌ Ошибка при обработке видео:")
            print(result.stderr)
            return False
        
        print(f"✅ Видео успешно обработано!")
        
        # Проверяем результат
        print(f"\n📹 Проверка результата:")
        props_result = check_video_properties(output_path)
        if props_result:
            print(f"   Ширина: {props_result['width']}px")
            print(f"   Высота: {props_result['height']}px")
            print(f"   Соотношение: {props_result['ratio']:.2f}")
        
        return True
        
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    print("=" * 70)
    print("🔧 Изменение разрешения видео урока 1 до 960x600")
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
        print(f"   Ширина: {props['width']}px")
        print(f"   Высота: {props['height']}px")
        print(f"   Соотношение: {props['ratio']:.2f}")
        print()
        
        # Создаем резервную копию
        backup_path = video_path.with_suffix('.mp4.backup_960x600')
        if not backup_path.exists():
            import shutil
            shutil.copy2(video_path, backup_path)
            print(f"💾 Создана резервная копия: {backup_path}")
        else:
            print(f"⏭️  Резервная копия уже существует: {backup_path}")
        
        # Создаем временный файл для результата
        temp_output = video_path.with_suffix('.960x600.mp4')
        
        # Изменяем разрешение
        if resize_video_to_960x600(video_path, temp_output):
            # Заменяем оригинальный файл
            video_path.unlink()
            temp_output.rename(video_path)
            print(f"\n✅ Видео успешно обработано и сохранено: {video_path}")
        else:
            print(f"\n❌ Не удалось обработать видео")
            if temp_output.exists():
                temp_output.unlink()
    
    print()
    print("=" * 70)
