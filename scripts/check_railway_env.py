"""
Скрипт для проверки переменных окружения на Railway.
Запускается при старте для диагностики.
"""

import os
import sys

def check_env_variables():
    """Проверяет все переменные окружения, связанные с ботом."""
    print("=" * 60)
    print("🔍 ПРОВЕРКА ПЕРЕМЕННЫХ ОКРУЖЕНИЯ")
    print("=" * 60)
    
    # Список переменных для проверки
    required_vars = [
        "SALES_BOT_TOKEN",
        "COURSE_BOT_TOKEN"
    ]
    
    optional_vars = [
        "ADMIN_CHAT_ID",
        "GENERAL_GROUP_ID",
        "PREMIUM_GROUP_ID",
        "CURATOR_GROUP_ID",
        "DATABASE_PATH",
        "PORT",
        "PAYMENT_PROVIDER"
    ]
    
    print("\n📋 Обязательные переменные:")
    missing_required = []
    for var in required_vars:
        value = os.environ.get(var)
        if value:
            # Показываем только первые 10 символов и длину для безопасности
            masked = value[:10] + "..." if len(value) > 10 else value
            print(f"   ✅ {var}: установлен (длина: {len(value)}, начало: {masked})")
        else:
            print(f"   ❌ {var}: НЕ УСТАНОВЛЕН")
            missing_required.append(var)
    
    print("\n📋 Опциональные переменные:")
    for var in optional_vars:
        value = os.environ.get(var)
        if value:
            print(f"   ✅ {var}: {value}")
        else:
            print(f"   ⚠️  {var}: не установлен (будет использовано значение по умолчанию)")
    
    print("\n" + "=" * 60)
    print("🔍 Все переменные окружения (начинающиеся с BOT, SALES, COURSE, ADMIN, GROUP, DATABASE, PAYMENT):")
    print("=" * 60)
    
    relevant_keys = [
        k for k in os.environ.keys() 
        if any(prefix in k.upper() for prefix in ['BOT', 'SALES', 'COURSE', 'ADMIN', 'GROUP', 'DATABASE', 'PAYMENT', 'PORT'])
    ]
    
    if relevant_keys:
        for key in sorted(relevant_keys):
            value = os.environ.get(key, "")
            if 'TOKEN' in key.upper() or 'SECRET' in key.upper() or 'KEY' in key.upper():
                # Маскируем секретные значения
                masked = value[:10] + "..." if len(value) > 10 else value
                print(f"   {key}: {masked} (длина: {len(value)})")
            else:
                print(f"   {key}: {value}")
    else:
        print("   ⚠️  Не найдено релевантных переменных окружения")
    
    print("\n" + "=" * 60)
    if missing_required:
        print(f"❌ ОТСУТСТВУЮТ ОБЯЗАТЕЛЬНЫЕ ПЕРЕМЕННЫЕ: {', '.join(missing_required)}")
        print("=" * 60)
        return False
    else:
        print("✅ Все обязательные переменные установлены")
        print("=" * 60)
        return True

if __name__ == "__main__":
    success = check_env_variables()
    sys.exit(0 if success else 1)
