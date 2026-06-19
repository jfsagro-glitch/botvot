"""
Скрипт для оптимизации медиа файлов (сжатие без потери качества для мобильных).

Оптимизирует изображения и видео из Photo/video_pic для быстрой загрузки,
сохраняя качество для просмотра на мобильных устройствах.
"""

import json
import sys
import asyncio
from pathlib import Path
from typing import Dict, List, Optional
import re
import shutil

# Добавляем корень проекта в PYTHONPATH
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Устанавливаем UTF-8 для вывода в консоль Windows
if sys.platform == 'win32':
    import codecs
    sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')
    sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer, 'strict')

try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False
    print("⚠️  Pillow не установлен. Установите: pip install Pillow")

try:
    import subprocess
    
    # Ищем ffmpeg в нескольких местах
    ffmpeg_paths = [
        shutil.which("ffmpeg"),  # В PATH
        Path(r"C:\Users\79184.WIN-OOR1JAM5834\Downloads\ffmpeg-2026-01-05-git-2892815c45-essentials_build\bin\ffmpeg.exe"),
        Path(__file__).parent.parent / "ffmpeg" / "bin" / "ffmpeg.exe",
    ]
    
    FFMPEG_PATH = None
    for path in ffmpeg_paths:
        if path and Path(path).exists():
            FFMPEG_PATH = str(Path(path).absolute())
            break
    
    FFMPEG_AVAILABLE = FFMPEG_PATH is not None
except:
    FFMPEG_AVAILABLE = False
    FFMPEG_PATH = None

if not FFMPEG_AVAILABLE:
    print("⚠️  ffmpeg не найден. Установите ffmpeg для сжатия видео.")
else:
    print(f"✅ Найден ffmpeg: {FFMPEG_PATH}")


def get_lesson_number_from_filename(filename: str) -> Optional[int]:
    """Извлекает номер урока из имени файла."""
    match = re.match(r'^(\d{3})', filename)
    if match:
        return int(match.group(1))
    match = re.match(r'^(\d+)', filename)
    if match:
        return int(match.group(1))
    return None


def get_media_type(filename: str) -> Optional[str]:
    """Определяет тип медиа по расширению файла."""
    ext = Path(filename).suffix.lower()
    if ext in ['.mp4', '.avi', '.mov', '.mkv', '.webm']:
        return 'video'
    elif ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp']:
        return 'photo'
    return None


def optimize_image(input_path: Path, output_path: Path, max_size_mb: float = 2.0, quality: int = 85) -> bool:
    """
    Оптимизирует изображение для мобильных устройств.
    
    Args:
        input_path: Путь к исходному изображению
        output_path: Путь для сохранения оптимизированного изображения
        max_size_mb: Максимальный размер в МБ
        quality: Качество JPEG (85 - хороший баланс)
    
    Returns:
        True если успешно, False если ошибка
    """
    if not PIL_AVAILABLE:
        return False
    
    try:
        original_size = input_path.stat().st_size / (1024 * 1024)
        
        # Если файл уже маленький, просто копируем
        if original_size <= max_size_mb:
            shutil.copy2(input_path, output_path)
            return True
        
        # Открываем изображение
        img = Image.open(input_path)
        
        # Конвертируем RGBA в RGB для JPEG
        if img.mode in ('RGBA', 'LA', 'P'):
            background = Image.new('RGB', img.size, (255, 255, 255))
            if img.mode == 'P':
                img = img.convert('RGBA')
            background.paste(img, mask=img.split()[-1] if img.mode in ('RGBA', 'LA') else None)
            img = background
        elif img.mode != 'RGB':
            img = img.convert('RGB')
        
        # Если изображение очень большое, уменьшаем размер
        max_dimension = 1920  # Максимум для мобильных
        if max(img.size) > max_dimension:
            ratio = max_dimension / max(img.size)
            new_size = (int(img.size[0] * ratio), int(img.size[1] * ratio))
            img = img.resize(new_size, Image.Resampling.LANCZOS)
        
        # Сохраняем с оптимизацией
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Пробуем разные уровни качества, пока не достигнем нужного размера
        for q in range(quality, 60, -5):
            img.save(output_path, 'JPEG', quality=q, optimize=True)
            new_size = output_path.stat().st_size / (1024 * 1024)
            if new_size <= max_size_mb:
                return True
        
        # Если все еще большой, уменьшаем еще больше
        if output_path.stat().st_size / (1024 * 1024) > max_size_mb:
            ratio = 0.8
            new_size = (int(img.size[0] * ratio), int(img.size[1] * ratio))
            img = img.resize(new_size, Image.Resampling.LANCZOS)
            img.save(output_path, 'JPEG', quality=75, optimize=True)
        
        return True
    except Exception as e:
        print(f"   ❌ Ошибка при оптимизации изображения: {e}")
        return False


def optimize_video(input_path: Path, output_path: Path, max_size_mb: float = 20.0, max_bitrate: str = "2M") -> bool:
    """
    Оптимизирует видео для мобильных устройств с помощью ffmpeg.
    
    Args:
        input_path: Путь к исходному видео
        output_path: Путь для сохранения оптимизированного видео
        max_size_mb: Максимальный размер в МБ
        max_bitrate: Максимальный битрейт (например, "2M" для 2 Мбит/с)
    
    Returns:
        True если успешно, False если ошибка
    """
    if not FFMPEG_AVAILABLE:
        return False
    
    try:
        original_size = input_path.stat().st_size / (1024 * 1024)
        
        # Если файл уже маленький, просто копируем
        if original_size <= max_size_mb:
            shutil.copy2(input_path, output_path)
            return True
        
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Команда ffmpeg для оптимизации видео
        # Используем H.264 кодек, оптимизированный для мобильных
        # Сохраняем пропорции и вписываем в окно (максимум 1920x1080 для мобильных)
        ffmpeg_cmd = FFMPEG_PATH if FFMPEG_PATH else 'ffmpeg'
        cmd = [
            ffmpeg_cmd,
            '-i', str(input_path),
            '-c:v', 'libx264',
            '-preset', 'medium',  # Баланс между скоростью и размером
            '-crf', '23',  # Качество (18-28, 23 - хороший баланс)
            '-maxrate', max_bitrate,
            '-bufsize', f'{int(max_bitrate[:-1]) * 2}M',
            '-vf', 'scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2',  # Вписываем в 1920x1080 с сохранением пропорций
            '-c:a', 'aac',
            '-b:a', '128k',  # Аудио битрейт
            '-movflags', '+faststart',  # Оптимизация для стриминга
            '-y',  # Перезаписать если существует
            str(output_path)
        ]
        
        # Запускаем ffmpeg
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600  # 10 минут максимум
        )
        
        if result.returncode == 0 and output_path.exists():
            new_size = output_path.stat().st_size / (1024 * 1024)
            if new_size <= max_size_mb * 1.2:  # Допускаем небольшое превышение
                return True
            else:
                # Если все еще большой, пробуем более агрессивное сжатие
                cmd_more_aggressive = [
                    ffmpeg_cmd,
                    '-i', str(input_path),
                    '-c:v', 'libx264',
                    '-preset', 'fast',
                    '-crf', '28',  # Более высокое сжатие
                    '-maxrate', '1.5M',
                    '-bufsize', '3M',
                    '-vf', 'scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2',  # Вписываем в 1280x720 с сохранением пропорций
                    '-c:a', 'aac',
                    '-b:a', '96k',
                    '-movflags', '+faststart',
                    '-y',
                    str(output_path)
                ]
                result = subprocess.run(
                    cmd_more_aggressive,
                    capture_output=True,
                    text=True,
                    timeout=600
                )
                return result.returncode == 0 and output_path.exists()
        
        return False
    except subprocess.TimeoutExpired:
        print(f"   ❌ Таймаут при оптимизации видео")
        return False
    except Exception as e:
        print(f"   ❌ Ошибка при оптимизации видео: {e}")
        return False


def process_media_files(media_dir: Path, optimized_dir: Path) -> Dict[int, List[Dict[str, str]]]:
    """
    Обрабатывает все медиа файлы и оптимизирует их.
    
    Returns:
        Словарь с информацией об оптимизированных файлах
    """
    media_by_lesson: Dict[int, List[Dict[str, str]]] = {}
    
    if not media_dir.exists():
        print(f"❌ Директория {media_dir} не существует!")
        return media_by_lesson
    
    print(f"📂 Сканирую директорию: {media_dir}")
    print()
    
    # Получаем список всех файлов
    all_files = [f for f in media_dir.iterdir() if f.is_file()]
    total_files = len(all_files)
    processed = 0
    optimized = 0
    skipped = 0
    
    for file_path in all_files:
        filename = file_path.name
        lesson_num = get_lesson_number_from_filename(filename)
        media_type = get_media_type(filename)
        
        if lesson_num is None:
            print(f"⚠️  Пропускаю файл (не удалось определить номер урока): {filename}")
            continue
        
        if media_type is None:
            print(f"⚠️  Пропускаю файл (неизвестный тип): {filename}")
            continue
        
        processed += 1
        original_size = file_path.stat().st_size / (1024 * 1024)
        
        print(f"[{processed}/{total_files}] 🔧 Обрабатываю {media_type} для урока {lesson_num}: {filename}")
        print(f"   📊 Исходный размер: {original_size:.2f} МБ")
        
        # Создаем путь для оптимизированного файла
        optimized_file = optimized_dir / filename
        
        success = False
        if media_type == 'photo':
            if PIL_AVAILABLE:
                success = optimize_image(file_path, optimized_file, max_size_mb=2.0, quality=85)
            else:
                print(f"   ⚠️  Pillow не установлен, пропускаю")
                skipped += 1
                continue
        elif media_type == 'video':
            if FFMPEG_AVAILABLE:
                success = optimize_video(file_path, optimized_file, max_size_mb=20.0, max_bitrate="2M")
            else:
                print(f"   ⚠️  ffmpeg не найден, пропускаю")
                skipped += 1
                continue
        
        if success and optimized_file.exists():
            new_size = optimized_file.stat().st_size / (1024 * 1024)
            reduction = ((original_size - new_size) / original_size) * 100 if original_size > 0 else 0
            
            if lesson_num not in media_by_lesson:
                media_by_lesson[lesson_num] = []
            
            # Сохраняем путь к оптимизированному файлу
            relative_path = f"Photo/video_pic_optimized/{filename}"
            media_by_lesson[lesson_num].append({
                "type": media_type,
                "path": relative_path,
                "original_path": f"Photo/video_pic/{filename}"
            })
            
            optimized += 1
            print(f"   ✅ Оптимизировано: {new_size:.2f} МБ (уменьшение на {reduction:.1f}%)")
        else:
            print(f"   ❌ Не удалось оптимизировать")
    
    print()
    print(f"📊 Итого: обработано {processed}, оптимизировано {optimized}, пропущено {skipped}")
    return media_by_lesson


def update_lessons_with_optimized_paths(lessons_file: Path, media_by_lesson: Dict[int, List[Dict[str, str]]]):
    """Обновляет lessons.json с путями к оптимизированным медиа файлам."""
    # Читаем текущий lessons.json
    with open(lessons_file, 'r', encoding='utf-8') as f:
        lessons = json.load(f)
    
    updated_count = 0
    
    # Обновляем уроки
    for lesson_num, media_list in media_by_lesson.items():
        lesson_key = str(lesson_num)
        
        if lesson_key not in lessons:
            print(f"⚠️  Урок {lesson_num} не найден в lessons.json, пропускаю...")
            continue
        
        # Обновляем пути на оптимизированные
        lessons[lesson_key]["media"] = media_list
        updated_count += 1
        print(f"✅ Обновлен урок {lesson_num} с оптимизированными путями")
    
    # Сохраняем обновленный lessons.json
    if updated_count > 0:
        # Создаем резервную копию
        backup_file = lessons_file.with_suffix('.json.backup_optimized')
        with open(backup_file, 'w', encoding='utf-8') as f:
            json.dump(lessons, f, ensure_ascii=False, indent=2)
        print(f"💾 Создана резервная копия: {backup_file}")
        
        # Сохраняем обновленный файл
        with open(lessons_file, 'w', encoding='utf-8') as f:
            json.dump(lessons, f, ensure_ascii=False, indent=2)
        
        print(f"✅ Обновлено {updated_count} уроков в {lessons_file}")
    else:
        print("ℹ️  Нет медиа для обновления")


def main():
    """Основная функция."""
    # Определяем пути
    media_dir = project_root / "Photo" / "video_pic"
    optimized_dir = project_root / "Photo" / "video_pic_optimized"
    lessons_file = project_root / "data" / "lessons.json"
    
    print("=" * 70)
    print("🔧 Оптимизация медиа файлов для быстрой загрузки")
    print("=" * 70)
    print()
    
    # Проверяем существование файлов
    if not lessons_file.exists():
        print(f"❌ Файл {lessons_file} не найден!")
        return
    
    if not media_dir.exists():
        print(f"❌ Директория {media_dir} не существует!")
        return
    
    # Создаем директорию для оптимизированных файлов
    optimized_dir.mkdir(parents=True, exist_ok=True)
    
    print(f"📂 Исходная директория: {media_dir}")
    print(f"📂 Директория для оптимизированных: {optimized_dir}")
    print()
    
    # Проверяем зависимости
    if not PIL_AVAILABLE and not FFMPEG_AVAILABLE:
        print("❌ Не установлены необходимые инструменты!")
        print("   Установите: pip install Pillow")
        print("   И установите ffmpeg: https://ffmpeg.org/download.html")
        return
    
    # Обрабатываем медиа файлы
    print("🔧 Шаг 1: Оптимизация медиа файлов...")
    print()
    media_by_lesson = process_media_files(media_dir, optimized_dir)
    
    if not media_by_lesson:
        print("❌ Не найдено медиа файлов для оптимизации!")
        return
    
    print()
    print(f"📊 Оптимизировано медиа для {len(media_by_lesson)} уроков")
    print()
    
    # Обновляем lessons.json
    print("📝 Шаг 2: Обновление lessons.json с путями к оптимизированным файлам...")
    print()
    update_lessons_with_optimized_paths(lessons_file, media_by_lesson)
    
    print()
    print("=" * 70)
    print("✅ Готово! Медиа файлы оптимизированы.")
    print("=" * 70)
    print()
    print("💡 Теперь можно загрузить оптимизированные файлы в Telegram")
    print("   для получения file_id (они будут загружаться быстрее).")
    print()
    print("📂 Оптимизированные файлы находятся в: Photo/video_pic_optimized/")


if __name__ == "__main__":
    main()
