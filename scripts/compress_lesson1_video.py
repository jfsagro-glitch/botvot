"""
Скрипт для дополнительного сжатия видео урока 1 до меньшего размера.
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

def compress_video(input_path: Path, output_path: Path, target_size_mb: float = 2.0):
    """Сжимает видео до целевого размера с сохранением разрешения 960x600."""
    ffmpeg_path = Path(r"C:\Users\79184.WIN-OOR1JAM5834\Downloads\ffmpeg-2026-01-05-git-2892815c45-essentials_build\bin\ffmpeg.exe")
    
    if not ffmpeg_path.exists():
        print("❌ ffmpeg не найден!")
        return False
    
    try:
        # Проверяем текущий размер
        current_size_mb = input_path.stat().st_size / (1024 * 1024)
        print(f"📊 Текущий размер: {current_size_mb:.2f} MB")
        print(f"🎯 Целевой размер: {target_size_mb:.2f} MB")
        print()
        
        # Вычисляем битрейт для целевого размера (примерно)
        # Примерная формула: битрейт = (размер_в_МБ * 8) / (длительность_в_секундах)
        # Используем двухпроходное кодирование для лучшего качества
        
        # Получаем длительность видео
        cmd_duration = [
            str(ffmpeg_path),
            "-i", str(input_path),
            "-hide_banner"
        ]
        result = subprocess.run(cmd_duration, capture_output=True, text=True, encoding='utf-8', errors='ignore')
        
        # Пробуем извлечь длительность (примерно)
        duration_estimate = 30  # секунд (примерная оценка)
        if "Duration" in result.stderr:
            import re
            duration_match = re.search(r'Duration: (\d+):(\d+):(\d+\.\d+)', result.stderr)
            if duration_match:
                hours, minutes, seconds = duration_match.groups()
                duration_estimate = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
        
        print(f"⏱️  Примерная длительность: {duration_estimate:.1f} секунд")
        
        # Вычисляем целевой битрейт (с запасом для аудио)
        target_bitrate_kbps = int((target_size_mb * 8 * 1024) / duration_estimate * 0.9)  # 90% для видео, 10% для аудио
        audio_bitrate = "128k"
        
        print(f"🎯 Целевой битрейт видео: {target_bitrate_kbps} kbps")
        print()
        
        # Команда для сжатия видео
        # Сохраняем текущее разрешение (1080x608 для мобильных) при сжатии
        # Проверяем текущее разрешение
        import json
        cmd_check = [
            str(ffmpeg_path),
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "json",
            str(input_path)
        ]
        result_check = subprocess.run(cmd_check, capture_output=True, text=True, encoding='utf-8', errors='ignore')
        current_width = 1080
        current_height = 608
        if result_check.returncode == 0:
            try:
                data = json.loads(result_check.stdout)
                if data.get("streams"):
                    current_width = data["streams"][0].get("width", 1080)
                    current_height = data["streams"][0].get("height", 608)
            except:
                pass
        
        # Используем текущее разрешение для фильтра
        cmd = [
            str(ffmpeg_path),
            "-i", str(input_path),
            "-vf", f"scale={current_width}:{current_height}",  # Сохраняем текущее разрешение
            "-c:v", "libx264",
            "-preset", "slow",  # Медленнее, но лучше качество
            "-b:v", f"{target_bitrate_kbps}k",
            "-maxrate", f"{target_bitrate_kbps * 1.2}k",  # Максимальный битрейт (на 20% больше)
            "-bufsize", f"{target_bitrate_kbps * 2}k",  # Размер буфера
            "-crf", "28",  # Дополнительное сжатие (выше = меньше размер)
            "-c:a", "aac",
            "-b:a", audio_bitrate,
            "-movflags", "+faststart",  # Для быстрого начала воспроизведения
            "-y",
            str(output_path)
        ]
        
        print(f"🔄 Сжатие видео...")
        print(f"   Это может занять несколько минут...")
        
        result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='ignore')
        
        if result.returncode != 0:
            print(f"❌ Ошибка при сжатии видео:")
            print(result.stderr[-500:])  # Последние 500 символов ошибки
            return False
        
        # Проверяем размер результата
        if output_path.exists():
            new_size_mb = output_path.stat().st_size / (1024 * 1024)
            print(f"✅ Видео успешно сжато!")
            print(f"📊 Новый размер: {new_size_mb:.2f} MB")
            print(f"📉 Уменьшение: {(1 - new_size_mb / current_size_mb) * 100:.1f}%")
        else:
            print(f"❌ Выходной файл не создан")
            return False
        
        return True
        
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    print("=" * 70)
    print("🔧 Дополнительное сжатие видео урока 1")
    print("=" * 70)
    print()
    
    video_path = project_root / "Photo" / "video_pic_optimized" / "001 Корвет.mp4"
    
    if not video_path.exists():
        print(f"❌ Видео не найдено: {video_path}")
        sys.exit(1)
    
    # Создаем временный файл для результата
    temp_output = video_path.with_suffix('.compressed.mp4')
    
    # Сжимаем видео до примерно 2 MB
    if compress_video(video_path, temp_output, target_size_mb=2.0):
        # Заменяем оригинальный файл
        backup_path = video_path.with_suffix('.mp4.backup_before_compress')
        if not backup_path.exists():
            import shutil
            shutil.copy2(video_path, backup_path)
            print(f"\n💾 Создана резервная копия: {backup_path}")
        
        video_path.unlink()
        temp_output.rename(video_path)
        print(f"\n✅ Видео успешно сжато и сохранено: {video_path}")
    else:
        print(f"\n❌ Не удалось сжать видео")
        if temp_output.exists():
            temp_output.unlink()
    
    print()
    print("=" * 70)
