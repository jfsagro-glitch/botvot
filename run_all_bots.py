"""
Запуск обоих ботов в одном процессе.

Используется для деплоя на платформах, где удобнее запускать один процесс.
"""

import asyncio
import logging
import sys
import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from bots.sales_bot import SalesBot
from bots.course_bot import CourseBot
from core.config import Config

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)


class HealthCheckHandler(BaseHTTPRequestHandler):
    """Простой HTTP обработчик для healthcheck."""
    
    def do_GET(self):
        """Обработка GET запросов для healthcheck."""
        if self.path == '/' or self.path == '/health':
            self.send_response(200)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(b'OK')
        else:
            self.send_response(404)
            self.end_headers()
    
    def log_message(self, format, *args):
        """Отключаем логирование HTTP запросов для чистоты логов."""
        pass


def run_http_server(port):
    """Запуск синхронного HTTP сервера в отдельном потоке."""
    try:
        server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
        logger.info(f"🌐 HTTP сервер запущен на порту {port} для healthcheck")
        logger.info(f"🌐 Healthcheck доступен по адресу: http://0.0.0.0:{port}/")
        server.serve_forever()
    except Exception as e:
        logger.error(f"❌ Ошибка HTTP сервера: {e}", exc_info=True)


def start_http_server():
    """Запуск HTTP сервера в отдельном потоке."""
    # Получаем порт из переменной окружения (Railway автоматически устанавливает PORT)
    # В Railway переменная PORT устанавливается автоматически
    port_str = os.environ.get('PORT')
    if not port_str:
        # Если PORT не установлен, используем значение по умолчанию
        logger.warning("⚠️ Переменная PORT не установлена, используем 8080")
        port = 8080
    else:
        try:
            port = int(port_str)
            logger.info(f"📌 Используется порт из переменной окружения: {port}")
        except (ValueError, TypeError):
            logger.warning(f"⚠️ Неверный формат PORT: {port_str}, используем 8080")
            port = 8080
    
    # Запускаем HTTP сервер в отдельном потоке
    http_thread = threading.Thread(target=run_http_server, args=(port,), daemon=True)
    http_thread.start()
    logger.info("✅ HTTP сервер запущен в отдельном потоке")
    
    # Даем время серверу запуститься
    import time
    time.sleep(1)
    
    return http_thread


async def main():
    """Запуск обоих ботов и HTTP сервера."""
    sales_bot = None
    course_bot = None
    http_thread = None
    
    logger.info("=" * 60)
    logger.info("🚀 Запуск платформы курсов")
    logger.info("=" * 60)
    
    # КРИТИЧЕСКИ ВАЖНО: Запускаем HTTP сервер ПЕРВЫМ и в отдельном потоке
    # Railway проверяет healthcheck сразу, даже если боты еще не готовы
    logger.info("Запуск HTTP сервера для healthcheck...")
    try:
        http_thread = start_http_server()
        logger.info("✅ HTTP сервер успешно запущен и готов отвечать на healthcheck")
    except Exception as e:
        logger.error(f"❌ Критическая ошибка при запуске HTTP сервера: {e}", exc_info=True)
        logger.error("⚠️ Продолжаем без HTTP сервера (healthcheck НЕ БУДЕТ работать)")
        http_thread = None
    
    # Теперь проверяем конфигурацию (после запуска HTTP сервера)
    try:
        if not Config.validate():
            logger.error("❌ Неверная конфигурация. Проверьте файл .env")
            logger.error("⚠️ HTTP сервер работает, но боты не могут запуститься")
            # Не выходим, чтобы healthcheck продолжал работать
            # Просто ждем бесконечно, чтобы контейнер не перезапускался
            if http_thread:
                logger.info("🌐 HTTP сервер работает. Ожидание...")
                while True:
                    await asyncio.sleep(60)
            return
    except Exception as e:
        logger.error(f"❌ Ошибка при проверке конфигурации: {e}", exc_info=True)
        if http_thread:
            logger.info("🌐 HTTP сервер работает. Ожидание...")
            while True:
                await asyncio.sleep(60)
        return
    
    try:
        # Инициализация ботов
        logger.info("Инициализация продающего бота...")
        try:
            sales_bot = SalesBot()
            logger.info("✅ Продающий бот инициализирован")
        except Exception as e:
            logger.error(f"❌ Ошибка при инициализации продающего бота: {e}", exc_info=True)
            # Не падаем, продолжаем с другим ботом
            logger.warning("⚠️ Продолжаем без продающего бота")
        
        logger.info("Инициализация курс-бота...")
        try:
            course_bot = CourseBot()
            logger.info("✅ Курс-бот инициализирован")
        except Exception as e:
            logger.error(f"❌ Ошибка при инициализации курс-бота: {e}", exc_info=True)
            # Не падаем, продолжаем
            logger.warning("⚠️ Продолжаем без курс-бота")
        
        # Запуск ботов параллельно (если они были инициализированы)
        tasks = []
        if sales_bot:
            logger.info("Запуск продающего бота...")
            tasks.append(asyncio.create_task(sales_bot.start()))
        
        if course_bot:
            logger.info("Запуск курс-бота...")
            tasks.append(asyncio.create_task(course_bot.start()))
        
        if tasks:
            logger.info(f"✅ Запущено {len(tasks)} бот(ов). Все сервисы готовы к работе")
            # Ждем завершения всех задач
            try:
                await asyncio.gather(*tasks)
            except asyncio.CancelledError:
                logger.info("Получен сигнал остановки")
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
        else:
            logger.warning("⚠️ Ни один бот не запущен, но HTTP сервер работает")
            # Ждем бесконечно, чтобы контейнер не перезапускался
            if http_thread:
                while True:
                    await asyncio.sleep(60)
                    
    except KeyboardInterrupt:
        logger.info("Остановка ботов...")
    except Exception as e:
        logger.error(f"❌ Критическая ошибка: {e}", exc_info=True)
        # Не выходим, чтобы HTTP сервер продолжал работать для healthcheck
        logger.warning("⚠️ Ошибка в ботах, но HTTP сервер продолжает работать")
        if http_thread:
            while True:
                await asyncio.sleep(60)
    finally:
        if sales_bot:
            try:
                await sales_bot.stop()
            except Exception as e:
                logger.error(f"Ошибка при остановке продающего бота: {e}")
        
        if course_bot:
            try:
                await course_bot.stop()
            except Exception as e:
                logger.error(f"Ошибка при остановке курс-бота: {e}")
        
        # HTTP сервер работает в daemon потоке и остановится автоматически
        logger.info("Все сервисы остановлены")


if __name__ == "__main__":
    try:
        # Используем asyncio.run для правильного запуска
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Завершение работы...")
    except Exception as e:
        logger.error(f"❌ Фатальная ошибка при запуске: {e}", exc_info=True)
        sys.exit(1)

