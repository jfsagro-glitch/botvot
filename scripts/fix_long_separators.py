"""
Скрипт для сокращения длинных линий-разделителей для мобильных устройств.
"""

import re
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

# Оптимальная длина для мобильных устройств Telegram
MOBILE_SEPARATOR_LENGTH = 14
MOBILE_SEPARATOR = "━" * MOBILE_SEPARATOR_LENGTH

def fix_long_separators_in_file(file_path: Path) -> int:
    """Исправляет длинные разделители в файле."""
    if not file_path.exists():
        print(f"⚠️  Файл не найден: {file_path}")
        return 0
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        original_content = content
        replacements = 0
        
        # Паттерн для поиска длинных линий (больше 16 символов)
        # Ищем линии с символами ━, -, или _
        patterns = [
            (r'━{17,}', MOBILE_SEPARATOR),  # Линии из ━ (больше 16)
            (r'-{17,}', '-' * MOBILE_SEPARATOR_LENGTH),  # Линии из - (больше 16)
            (r'_{17,}', '_' * MOBILE_SEPARATOR_LENGTH),  # Линии из _ (больше 16)
        ]
        
        for pattern, replacement in patterns:
            matches = re.findall(pattern, content)
            if matches:
                for match in matches:
                    content = content.replace(match, replacement)
                    replacements += len([m for m in re.findall(pattern, match)])
        
        # Также заменяем конкретные длинные линии, которые мы знаем
        long_separators = [
            ("━━━━━━━━━━━━━━━━━━━━", MOBILE_SEPARATOR),  # 22 символа
            ("━━━━━━━━━━━━━━━━━━━━━", MOBILE_SEPARATOR),  # 23 символа
        ]
        
        for old, new in long_separators:
            if old in content:
                count = content.count(old)
                content = content.replace(old, new)
                replacements += count
        
        if content != original_content:
            # Создаем резервную копию
            backup_path = file_path.with_suffix(file_path.suffix + '.backup_sep')
            if not backup_path.exists():
                with open(backup_path, 'w', encoding='utf-8') as f:
                    f.write(original_content)
                print(f"💾 Создана резервная копия: {backup_path}")
            
            # Сохраняем исправленный файл
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(content)
            
            print(f"✅ Исправлено {replacements} длинных линий в {file_path.name}")
            return replacements
        else:
            print(f"⏭️  Нет длинных линий в {file_path.name}")
            return 0
            
    except Exception as e:
        print(f"❌ Ошибка при обработке {file_path}: {e}")
        return 0

def main():
    """Основная функция."""
    print("=" * 70)
    print("🔧 Исправление длинных линий-разделителей для мобильных")
    print("=" * 70)
    print()
    
    # Файлы для проверки
    files_to_check = [
        project_root / "bots" / "course_bot.py",
        project_root / "bots" / "sales_bot.py",
        project_root / "utils" / "premium_ui.py",
        project_root / "utils" / "telegram_helpers.py",
    ]
    
    total_replacements = 0
    
    for file_path in files_to_check:
        replacements = fix_long_separators_in_file(file_path)
        total_replacements += replacements
    
    # Также проверяем все Python файлы в bots и utils
    for directory in ["bots", "utils"]:
        dir_path = project_root / directory
        if dir_path.exists():
            for py_file in dir_path.glob("*.py"):
                if py_file not in files_to_check:
                    replacements = fix_long_separators_in_file(py_file)
                    total_replacements += replacements
    
    print()
    print("=" * 70)
    print(f"✅ Готово! Всего исправлено {total_replacements} длинных линий")
    print(f"📏 Новая длина разделителя: {MOBILE_SEPARATOR_LENGTH} символов")
    print("=" * 70)

if __name__ == "__main__":
    main()
