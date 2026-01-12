"""
Проверка разбиения финального сообщения урока 30.
"""

import json
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

lessons_file = project_root / "data" / "lessons.json"

with open(lessons_file, 'r', encoding='utf-8') as f:
    lessons = json.load(f)

lesson_30 = lessons.get("30", {})
follow_up_text = lesson_30.get("follow_up_text", "")

print("=" * 70)
print("Проверка разбиения финального сообщения урока 30")
print("=" * 70)
print()

total_length = len(follow_up_text)
print(f"Общая длина текста: {total_length} символов")
print(f"Лимит Telegram для caption: 1024 символа")
print()

# Текущее разбиение (прямо по 1024 символам)
split_at = 1024
current_first = follow_up_text[:split_at]
current_second = follow_up_text[split_at:]

print(f"Текущее разбиение:")
print(f"  Первая часть (первые {split_at} символов):")
print(f"    ...{current_first[-30:]}")
print()
print(f"  Вторая часть (остальные {len(current_second)} символов):")
print(f"    {current_second[:50]}")
print()

# Ищем слово "Отснятый"
word = "Отснятый"
word_index = follow_up_text.find(word)
if word_index != -1:
    print(f"Найдено слово '{word}' на позиции {word_index}")
    print(f"  Контекст: ...{follow_up_text[word_index-20:word_index+30]}...")
    print()
    
    # Проверяем, где попадает это слово
    if word_index < split_at:
        print(f"⚠️  Слово '{word}' полностью в первой части (caption)")
    elif word_index >= split_at and word_index < split_at + len(word):
        # Начало слова попадает на границу разбиения
        if word_index == split_at:
            print(f"✅ Слово '{word}' начинается точно на границе разбиения - первая буква в первом блоке")
        else:
            print(f"⚠️  Слово '{word}' пересекает границу разбиения")
            chars_before = split_at - word_index
            if chars_before > 0:
                print(f"    Первые {chars_before} символов слова в первом блоке, остальные во втором")
    else:
        print(f"✅ Слово '{word}' полностью во второй части (остальной текст)")
    
    # Ищем оптимальную точку разбиения, чтобы не делить слова
    # Ищем последний пробел или перенос строки перед 1024-м символом
    optimal_split = split_at
    
    # Если слово "Отснятый" начинается после 1024, но его начало близко к границе
    # Сдвигаем разбиение так, чтобы все слово попало во второй блок
    if word_index >= split_at - 10 and word_index < split_at:
        # Слово начинается незадолго до границы - сдвигаем границу перед началом слова
        # Ищем последний пробел перед началом слова
        optimal_split = follow_up_text.rfind(' ', 0, word_index)
        if optimal_split == -1:
            optimal_split = word_index
        print()
        print(f"🔄 Оптимальная точка разбиения (перед словом '{word}'): {optimal_split}")
    elif word_index >= split_at and word_index < split_at + 20:
        # Слово начинается вскоре после границы - можно сдвинуть границу назад
        # Ищем последний пробел перед оптимальной точкой (немного раньше, чтобы точно не разделить слово)
        optimal_split = follow_up_text.rfind(' ', 0, word_index - 5)
        if optimal_split == -1 or optimal_split < split_at - 100:
            # Если не нашли подходящий пробел, ищем просто перед началом слова
            optimal_split = word_index
        print()
        print(f"🔄 Оптимальная точка разбиения (перед словом '{word}'): {optimal_split}")
    else:
        # Ищем последний пробел или перенос строки перед 1024-м символом
        # Но не раньше, чем за 50 символов от 1024
        search_start = max(0, split_at - 50)
        optimal_split = follow_up_text.rfind('\n', search_start, split_at)
        if optimal_split == -1:
            optimal_split = follow_up_text.rfind(' ', search_start, split_at)
        if optimal_split == -1 or optimal_split < split_at - 100:
            optimal_split = split_at
        
        print()
        print(f"🔄 Оптимальная точка разбиения (последний перенос строки или пробел): {optimal_split}")
    
    print()
    print(f"Новое разбиение:")
    new_first = follow_up_text[:optimal_split].rstrip()
    new_second = follow_up_text[optimal_split:].lstrip()
    
    print(f"  Первая часть ({len(new_first)} символов):")
    print(f"    ...{new_first[-30:]}")
    print()
    print(f"  Вторая часть ({len(new_second)} символов):")
    print(f"    {new_second[:50]}")
    print()
    
    # Проверяем, что слово "Отснятый" теперь полностью во второй части
    word_index_new = new_second.find(word)
    if word_index_new != -1:
        print(f"✅ Слово '{word}' теперь полностью во второй части на позиции {word_index_new}")
        print(f"   Контекст: ...{new_second[word_index_new-10:word_index_new+30]}...")
    else:
        print(f"⚠️  Слово '{word}' не найдено во второй части")
else:
    print(f"❌ Слово '{word}' не найдено в тексте")

print()
print("=" * 70)
