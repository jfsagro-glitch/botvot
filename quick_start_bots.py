"""Быстрый запуск ботов"""
import subprocess
import sys
import time

print("=" * 50)
print("  ЗАПУСК БОТОВ TELEGRAM COURSE PLATFORM")
print("=" * 50)
print()

# Запуск Sales Bot
print("[1/2] Запуск Sales Bot (StartNowQ_bot)...")
subprocess.Popen([
    sys.executable, "-m", "bots.sales_bot"
], creationflags=subprocess.CREATE_NEW_CONSOLE)

time.sleep(3)

# Запуск Course Bot
print("[2/2] Запуск Course Bot (StartNowAI_bot)...")
subprocess.Popen([
    sys.executable, "-m", "bots.course_bot"
], creationflags=subprocess.CREATE_NEW_CONSOLE)

print()
print("✅ Боты запущены в отдельных окнах!")
print()
print("Проверьте открытые окна командной строки.")
print("Протестируйте: t.me/StartNowQ_bot")
print()
input("Нажмите Enter для выхода...")

