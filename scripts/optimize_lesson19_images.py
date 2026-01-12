"""
Скрипт для оптимизации картинок уровней урока 19.
"""

import sys
import json
from pathlib import Path
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
    sys.exit(1)

def optimize_image(input_path: Path, output_path: Path, max_size_mb: float = 1.5, quality: int = 85) -> bool:
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

def main():
    """Основная функция."""
    # Определяем пути
    source_dir = project_root / "Photo" / "video_pic" / "019 Эмоциональные_уровни_Ocean_of_emotion"
    optimized_dir = project_root / "Photo" / "video_pic_optimized" / "019 Эмоциональные_уровни_Ocean_of_emotion"
    
    print("=" * 70)
    print("🔧 Оптимизация картинок уровней урока 19")
    print("=" * 70)
    print()
    
    if not source_dir.exists():
        print(f"❌ Директория {source_dir} не существует!")
        return
    
    # Создаем директорию для оптимизированных файлов
    optimized_dir.mkdir(parents=True, exist_ok=True)
    
    print(f"📂 Исходная директория: {source_dir}")
    print(f"📂 Директория для оптимизированных: {optimized_dir}")
    print()
    
    # Получаем список всех файлов
    all_files = [f for f in source_dir.iterdir() if f.is_file() and f.suffix.lower() in ['.jpg', '.jpeg', '.png']]
    total_files = len(all_files)
    
    if total_files == 0:
        print("❌ Не найдено изображений для оптимизации!")
        return
    
    print(f"📊 Найдено {total_files} изображений")
    print()
    
    optimized_count = 0
    skipped_count = 0
    
    for i, file_path in enumerate(all_files, 1):
        filename = file_path.name
        original_size = file_path.stat().st_size / (1024 * 1024)
        
        print(f"[{i}/{total_files}] 🔧 Обрабатываю: {filename}")
        print(f"   📊 Исходный размер: {original_size:.2f} МБ")
        
        # Создаем путь для оптимизированного файла
        optimized_file = optimized_dir / filename
        
        if optimize_image(file_path, optimized_file, max_size_mb=1.5, quality=85):
            new_size = optimized_file.stat().st_size / (1024 * 1024)
            reduction = ((original_size - new_size) / original_size) * 100 if original_size > 0 else 0
            optimized_count += 1
            print(f"   ✅ Оптимизировано: {new_size:.2f} МБ (уменьшение на {reduction:.1f}%)")
        else:
            skipped_count += 1
            print(f"   ❌ Не удалось оптимизировать")
        print()
    
    print("=" * 70)
    print(f"✅ Готово! Оптимизировано {optimized_count} из {total_files} изображений")
    print(f"📂 Оптимизированные файлы находятся в: {optimized_dir}")
    print("=" * 70)

if __name__ == "__main__":
    main()
