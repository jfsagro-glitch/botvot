"""
Скрипт для изменения разрешения видео урока 1 до оптимального для мобильных устройств.
Ширина: 1080px (стандарт для мобильных), высота: по пропорциям 16:9.
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
        return None
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        return None

def resize_video_for_mobile(input_path: Path, output_path: Path, target_width: int = 1080):
    """Изменяет разрешение видео для мобильных устройств - растягивает по ширине, сохраняя пропорции 16:9."""
    ffmpeg_path = Path(r"C:\Users\79184.WIN-OOR1JAM5834\Downloads\ffmpeg-2026-01-05-git-2892815c45-essentials_build\bin\ffmpeg.exe")
    
    if not ffmpeg_path.exists():
        print("❌ ffmpeg не найден!")
        return False
    
    try:
        # Проверяем текущие пропорции
        props = check_video_properties(input_path)
        if not props:
            return False
        
        current_width = props["width"]
        current_height = props["height"]
        
        print(f"📹 Текущие свойства видео:")
        print(f"   Ширина: {current_width}px")
        print(f"   Высота: {current_height}px")
        print(f"   Соотношение: {props['ratio']:.2f}")
        print()
        
        # Целевое разрешение для мобильных: ширина 1080px, высота вычисляется для пропорций 16:9
        # 16:9 означает, что высота = ширина * 9 / 16
        target_width = 1080
        target_height = int(target_width * 9 / 16)  # 1080 * 9 / 16 = 607.5 ≈ 608
        # Но для четного числа (лучше для кодирования): 608
        target_height = 608
        
        print(f"🎯 Целевое разрешение для мобильных:")
        print(f"   Ширина: {target_width}px (растягивается по ширине экрана)")
        print(f"   Высота: {target_height}px (пропорции 16:9)")
        print(f"   Соотношение: {target_width / target_height:.2f}")
        print()
        
        # Используем scale с автоматическим вычислением высоты при сохранении пропорций
        # scale=1080:-2 автоматически вычисляет высоту, сохраняя пропорции
        # Но мы точно знаем, что хотим 16:9, поэтому используем точные значения
        
        # Если видео уже 16:9 или близко к этому, просто масштабируем
        # Если нет - обрезаем до 16:9 или добавляем черные полосы
        
        # Используем crop для обрезки до 16:9, затем scale до 1080x608
        # Сначала вычисляем обрезку для получения 16:9
        current_ratio = props['ratio']
        target_ratio = 16 / 9  # 1.777...
        
        if abs(current_ratio - target_ratio) < 0.1:
            # Пропорции близки к 16:9, просто масштабируем
            vf = f"scale={target_width}:{target_height}"
            print(f"🔧 Видео уже имеет пропорции 16:9, применяем простое масштабирование")
        elif current_ratio > target_ratio:
            # Видео шире - обрезаем по ширине (crop)
            new_height = current_height
            new_width = int(current_height * target_ratio)
            crop_x = (current_width - new_width) // 2
            crop_y = 0
            vf = f"crop={new_width}:{new_height}:{crop_x}:{crop_y},scale={target_width}:{target_height}"
            print(f"🔧 Видео шире 16:9, обрезаем по ширине, затем масштабируем")
        else:
            # Видео выше - обрезаем по высоте (crop)
            new_width = current_width
            new_height = int(current_width / target_ratio)
            crop_x = 0
            crop_y = (current_height - new_height) // 2
            vf = f"crop={new_width}:{new_height}:{crop_x}:{crop_y},scale={target_width}:{target_height}"
            print(f"🔧 Видео выше 16:9, обрезаем по высоте, затем масштабируем")
        
        print(f"   Фильтр: {vf}")
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
            "-y",
            str(output_path)
        ]
        
        print(f"🔄 Обработка видео...")
        
        result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='ignore')
        
        if result.returncode != 0:
            print(f"❌ Ошибка при обработке видео:")
            print(result.stderr[-500:])
            return False
        
        print(f"✅ Видео успешно обработано!")
        
        # Проверяем результат
        print(f"\n📹 Проверка результата:")
        props_result = check_video_properties(output_path)
        if props_result:
            print(f"   Ширина: {props_result['width']}px")
            print(f"   Высота: {props_result['height']}px")
            print(f"   Соотношение: {props_result['ratio']:.2f}")
            
            if props_result['width'] == target_width and props_result['height'] == target_height:
                print(f"✅ Разрешение корректное: {target_width}x{target_height}")
            else:
                print(f"⚠️  Разрешение отличается от ожидаемого")
        
        return True
        
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    print("=" * 70)
    print("🔧 Изменение разрешения видео урока 1 для мобильных устройств")
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
        backup_path = video_path.with_suffix('.mp4.backup_mobile')
        if not backup_path.exists():
            import shutil
            shutil.copy2(video_path, backup_path)
            print(f"💾 Создана резервная копия: {backup_path}")
        else:
            print(f"⏭️  Резервная копия уже существует: {backup_path}")
        
        # Создаем временный файл для результата
        temp_output = video_path.with_suffix('.mobile.mp4')
        
        # Изменяем разрешение
        if resize_video_for_mobile(video_path, temp_output, target_width=1080):
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
