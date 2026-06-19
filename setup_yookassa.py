"""
Скрипт для настройки YooKassa оплаты.

Этот скрипт поможет вам настроить YooKassa для бота.
"""

import os
from pathlib import Path

def setup_yookassa():
    """Настройка YooKassa."""
    print("=" * 60)
    print("НАСТРОЙКА YOOKASSA ОПЛАТЫ")
    print("=" * 60)
    print()
    print("Для настройки YooKassa вам нужно:")
    print("1. Зайти в личный кабинет: https://yookassa.ru/my/i/aSR_d_SeRsnM/l")
    print("2. Перейти в раздел 'Настройки' -> 'API'")
    print("3. Скопировать Shop ID и Secret Key")
    print()
    
    # Проверяем наличие .env файла
    env_path = Path(".env")
    if not env_path.exists():
        print("❌ Файл .env не найден!")
        print("Создайте файл .env в корне проекта.")
        return
    
    print("Введите данные YooKassa:")
    print()
    
    shop_id = input("Shop ID: ").strip()
    secret_key = input("Secret Key: ").strip()
    
    if not shop_id or not secret_key:
        print("❌ Shop ID и Secret Key обязательны!")
        return
    
    # Читаем текущий .env
    env_content = env_path.read_text(encoding='utf-8')
    
    # Обновляем или добавляем настройки YooKassa
    lines = env_content.split('\n')
    updated_lines = []
    yookassa_found = False
    
    for line in lines:
        if line.startswith('PAYMENT_PROVIDER='):
            updated_lines.append('PAYMENT_PROVIDER=yookassa')
        elif line.startswith('YOOKASSA_SHOP_ID='):
            updated_lines.append(f'YOOKASSA_SHOP_ID={shop_id}')
            yookassa_found = True
        elif line.startswith('YOOKASSA_SECRET_KEY='):
            updated_lines.append(f'YOOKASSA_SECRET_KEY={secret_key}')
        elif line.startswith('YOOKASSA_RETURN_URL='):
            updated_lines.append('YOOKASSA_RETURN_URL=https://t.me/StartNowQ_bot')
        else:
            updated_lines.append(line)
    
    # Если YooKassa настройки не найдены, добавляем их
    if not yookassa_found:
        updated_lines.append('')
        updated_lines.append('# YooKassa Settings')
        updated_lines.append(f'YOOKASSA_SHOP_ID={shop_id}')
        updated_lines.append(f'YOOKASSA_SECRET_KEY={secret_key}')
        updated_lines.append('YOOKASSA_RETURN_URL=https://t.me/StartNowQ_bot')
    
    # Записываем обновленный .env
    env_path.write_text('\n'.join(updated_lines), encoding='utf-8')
    
    print()
    print("✅ Настройки YooKassa сохранены в .env файл!")
    print()
    print("Следующие шаги:")
    print("1. Установите библиотеку: pip install yookassa")
    print("2. Перезапустите бота")
    print()

if __name__ == "__main__":
    setup_yookassa()

