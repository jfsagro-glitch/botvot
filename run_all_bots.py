"""
Запуск обоих ботов в одном процессе.

Используется для деплоя на платформах, где удобнее запускать один процесс.
"""

import asyncio
import logging
import sys
import os
from pathlib import Path
from typing import Optional, Any

from aiohttp import web
import aiosqlite
from bots.sales_bot import SalesBot
from bots.course_bot import CourseBot
from bots.admin_bot import AdminBot
from core.config import Config
from services.payment_service import PaymentService
from core.models import Tariff

# Настройка логирования (включаем DEBUG для детальной диагностики)
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logging.getLogger('asyncio').setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


def _get_port() -> int:
    port_str = os.environ.get("PORT", "").strip()
    if not port_str:
        logger.warning("⚠️ Переменная PORT не установлена, используем 8080")
        return 8080
    try:
        port = int(port_str)
        logger.info(f"📌 Используется порт из переменной окружения: {port}")
        return port
    except (ValueError, TypeError):
        logger.warning(f"⚠️ Неверный формат PORT: {port_str}, используем 8080")
        return 8080


async def _handle_health(_: web.Request) -> web.Response:
    return web.Response(text="OK")



async def _handle_koob_landing(_: web.Request) -> web.Response:
    """KOOB partner landing page."""
    landing_path = Path(__file__).parent / "static" / "koob.html"
    try:
        html = landing_path.read_text(encoding="utf-8")
        return web.Response(text=html, content_type="text/html", charset="utf-8")
    except FileNotFoundError:
        return web.Response(status=404, text="Not found")


async def _handle_version(_: web.Request) -> web.Response:
    """
    Small debug endpoint to confirm what revision/config is actually running in Railway.
    Does NOT expose secrets.
    """
    possible_keys = [
        "RAILWAY_GIT_COMMIT_SHA",
        "RAILWAY_GIT_BRANCH",
        "RAILWAY_DEPLOYMENT_ID",
        "GIT_COMMIT",
        "COMMIT_SHA",
    ]
    meta = {k: os.environ.get(k) for k in possible_keys if os.environ.get(k)}

    # Runtime refs (optional)
    app = _.app if hasattr(_, "app") else None
    sales_bot = app.get("sales_bot") if app else None
    course_bot = app.get("course_bot") if app else None

    # DB diagnostics (helps debug "no lessons/reminders" quickly)
    db_path = (os.environ.get("DATABASE_PATH") or Config.DATABASE_PATH or "").strip()
    db_info: dict = {"path": db_path}
    try:
        p = Path(db_path) if db_path else None
        if p:
            db_info.update({
                "exists": p.exists(),
                "size_bytes": p.stat().st_size if p.exists() else None,
                "mtime": p.stat().st_mtime if p.exists() else None,
            })
        if p and p.exists():
            async with aiosqlite.connect(str(p)) as conn:
                # counts are safe to expose
                async with conn.execute("SELECT COUNT(*) FROM users") as cur:
                    db_info["users_total"] = (await cur.fetchone())[0]
                async with conn.execute("SELECT COUNT(*) FROM users WHERE tariff IS NOT NULL") as cur:
                    db_info["users_with_access"] = (await cur.fetchone())[0]
                async with conn.execute("SELECT COUNT(*) FROM users WHERE mentor_reminders > 0 AND tariff IS NOT NULL") as cur:
                    db_info["mentor_enabled"] = (await cur.fetchone())[0]
                async with conn.execute("SELECT COUNT(*) FROM assignments") as cur:
                    db_info["assignments_total"] = (await cur.fetchone())[0]
                # optional: next lesson schedule sanity
                async with conn.execute("SELECT MIN(current_day), MAX(current_day) FROM users WHERE tariff IS NOT NULL") as cur:
                    row = await cur.fetchone()
                    db_info["current_day_min"] = row[0]
                    db_info["current_day_max"] = row[1]
    except Exception as e:
        db_info["error"] = str(e)

    return web.json_response({
        "deploy": meta,
        "runtime": {
            "sales_bot_ready": bool(sales_bot),
            "course_bot_ready": bool(course_bot),
        },
        "config": {
            "schedule_timezone": getattr(Config, "SCHEDULE_TIMEZONE", ""),
            "lesson_delivery_time_local": getattr(Config, "LESSON_DELIVERY_TIME_LOCAL", ""),
            "mentor_window_local": f"{Config.MENTOR_REMINDER_START_LOCAL}-{Config.MENTOR_REMINDER_END_LOCAL}",
        },
        "tokens": {
            "sales_token_set": bool(Config.SALES_BOT_TOKEN),
            "course_token_set": bool(Config.COURSE_BOT_TOKEN),
            # safe: only reveals whether they are identical (common root cause of getUpdates conflicts)
            "tokens_equal": bool(Config.SALES_BOT_TOKEN and Config.COURSE_BOT_TOKEN and Config.SALES_BOT_TOKEN == Config.COURSE_BOT_TOKEN),
        },
        "db": db_info,
        "payment_provider": (Config.PAYMENT_PROVIDER or "").lower(),
        "currency": Config.PAYMENT_CURRENCY,
        "yookassa_shop_id_set": bool(Config.YOOKASSA_SHOP_ID),
        "yookassa_shop_id_len": len(Config.YOOKASSA_SHOP_ID or ""),
        "yookassa_receipt_required": str(getattr(Config, "YOOKASSA_RECEIPT_REQUIRED", "0")).strip(),
        "yookassa_tax_system_code": str(getattr(Config, "YOOKASSA_TAX_SYSTEM_CODE", "")).strip(),
        "yookassa_vat_code": str(getattr(Config, "YOOKASSA_VAT_CODE", "")).strip(),
        "basic_price": PaymentService.TARIFF_PRICES.get(Tariff.BASIC, None),
    })


def _extract_payment_id(payload: dict) -> Optional[str]:
    try:
        obj = payload.get("object") or {}
        pid = obj.get("id")
        return str(pid) if pid else None
    except Exception:
        return None


async def _handle_yookassa_webhook(request: web.Request) -> web.Response:
    """
    YooKassa webhook endpoint.
    - returns 200 quickly for non-succeeded events
    - uses DB idempotency to avoid duplicate processing
    """
    app = request.app
    sales_bot: Optional[Any] = app.get("sales_bot")
    if not sales_bot:
        # App is up but bots not ready yet
        return web.Response(status=503, text="Bots not ready")

    try:
        payload = await request.json()
    except Exception:
        return web.Response(status=400, text="Invalid JSON")

    payment_id = _extract_payment_id(payload)
    if not payment_id:
        return web.Response(status=400, text="Missing payment id")

    try:
        # Idempotency: if already processed, acknowledge
        if await sales_bot.db.is_payment_processed(payment_id):
            return web.Response(text="OK")

        # Let processor validate/parse webhook and grant access
        result = await sales_bot.payment_service.process_payment_completion(
            payment_id=payment_id,
            webhook_data=payload,
        )

        # If not completed yet (or not a succeeded event), we still acknowledge
        if not result:
            return web.Response(text="OK")

        await sales_bot.db.mark_payment_processed(payment_id)
        logger.info(f"✅ Webhook processed: payment_id={payment_id}, user_id={result.get('user_id')}")
        return web.Response(text="OK")
    except Exception as e:
        logger.error(f"❌ Error processing YooKassa webhook: {e}", exc_info=True)
        # Return 500 so YooKassa can retry
        return web.Response(status=500, text="ERROR")


async def start_web_server(app: web.Application) -> web.AppRunner:
    """Start aiohttp server (health + webhook) on PORT."""
    port = _get_port()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=port)
    await site.start()
    logger.info(f"🌐 HTTP сервер запущен на порту {port}")
    logger.info(f"🌐 Healthcheck: http://0.0.0.0:{port}/health")
    logger.info(f"🌐 Webhook:     http://0.0.0.0:{port}/payment/webhook")
    return runner


async def main():
    """Запуск обоих ботов и HTTP сервера."""
    sales_bot: Optional[SalesBot] = None
    course_bot: Optional[CourseBot] = None
    admin_bot: Optional[AdminBot] = None
    web_runner: Optional[web.AppRunner] = None
    
    logger.info("=" * 60)
    logger.info("🚀 Запуск платформы курсов")
    logger.info("=" * 60)
    
    # Создаем и настраиваем HTTP приложение СРАЗУ для быстрого healthcheck
    web_app = web.Application()
    web_app.router.add_get("/", _handle_health)
    web_app.router.add_get("/health", _handle_health)
    web_app.router.add_get("/version", _handle_version)
    web_app.router.add_post("/payment/webhook", _handle_yookassa_webhook)
    web_app.router.add_get("/koob", _handle_koob_landing)
    web_app.router.add_get("/koob/", _handle_koob_landing)
    
    # КРИТИЧЕСКИ ВАЖНО: Запускаем HTTP сервер САМЫМ ПЕРВЫМ
    # Railway проверяет healthcheck сразу, даже если боты еще не готовы
    logger.info("🌐 Запуск HTTP сервера для healthcheck (приоритет #1)...")
    try:
        web_runner = await start_web_server(web_app)
        logger.info("✅ HTTP сервер успешно запущен и готов отвечать на healthcheck/webhook")
        logger.info(f"🌐 Healthcheck endpoint: http://0.0.0.0:{_get_port()}/health")
    except Exception as e:
        logger.error(f"❌ Критическая ошибка при запуске HTTP сервера: {e}", exc_info=True)
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
        logger.error("❌ Не могу продолжить без HTTP сервера. Завершаю работу.")
        sys.exit(1)

    # Railway / deploy diagnostics (helps confirm which revision is actually running)
    try:
        possible_keys = [
            "RAILWAY_GIT_COMMIT_SHA",
            "RAILWAY_GIT_BRANCH",
            "RAILWAY_DEPLOYMENT_ID",
            "RAILWAY_SERVICE_ID",
            "RAILWAY_ENVIRONMENT_ID",
            "GIT_COMMIT",
            "COMMIT_SHA",
        ]
        found = {k: os.environ.get(k) for k in possible_keys if os.environ.get(k)}
        if found:
            logger.info("🧾 Deploy metadata (from env):")
            for k in sorted(found.keys()):
                logger.info(f"   - {k}={found[k]}")
        else:
            logger.info("🧾 Deploy metadata (from env): not provided by platform")
    except Exception as e:
        logger.warning(f"⚠️ Could not read deploy metadata env vars: {e}")

    # Seed data presence diagnostics (lessons JSON should be in image, not in /app/data Volume)
    try:
        seed_dir = Path("/app/seed_data")
        seed_lessons = seed_dir / "lessons.json"
        logger.info(f"📦 seed_data dir exists: {seed_dir.exists()}")
        if seed_dir.exists():
            logger.info(f"📦 seed_data contents: {[p.name for p in sorted(seed_dir.glob('*'))][:30]}")
        logger.info(f"📦 seed_data/lessons.json exists: {seed_lessons.exists()}")
    except Exception as e:
        logger.warning(f"⚠️ Could not check /app/seed_data: {e}")
    
    # Диагностика переменных окружения при старте
    logger.info("🔍 Проверка переменных окружения...")
    
    # Проверяем все возможные способы получения переменных
    sales_token_raw = os.environ.get("SALES_BOT_TOKEN", "")
    course_token_raw = os.environ.get("COURSE_BOT_TOKEN", "")
    
    # Также проверяем через os.getenv для надежности
    if not sales_token_raw:
        sales_token_raw = os.getenv("SALES_BOT_TOKEN", "")
    if not course_token_raw:
        course_token_raw = os.getenv("COURSE_BOT_TOKEN", "")
    
    logger.info(f"   SALES_BOT_TOKEN из os.environ: {'✅ есть' if sales_token_raw else '❌ нет'} (длина: {len(sales_token_raw)})")
    logger.info(f"   COURSE_BOT_TOKEN из os.environ: {'✅ есть' if course_token_raw else '❌ нет'} (длина: {len(course_token_raw)})")
    
    # Проверяем все переменные окружения (для полной диагностики)
    all_env_vars = dict(os.environ)
    logger.info(f"   Всего переменных окружения: {len(all_env_vars)}")
    
    # Проверяем все релевантные переменные
    relevant_vars = {k: v for k, v in all_env_vars.items() if any(prefix in k.upper() for prefix in ['BOT', 'SALES', 'COURSE', 'TOKEN', 'ADMIN', 'GROUP', 'DATABASE'])}
    if relevant_vars:
        logger.info(f"   Найдено {len(relevant_vars)} релевантных переменных окружения:")
        for key in sorted(relevant_vars.keys()):
            val = relevant_vars[key]
            if 'TOKEN' in key.upper() or 'SECRET' in key.upper() or 'KEY' in key.upper():
                # Маскируем секретные значения
                masked = val[:10] + "..." if len(val) > 10 else val
                logger.info(f"   - {key}: длина={len(val)}, начало={masked}")
            else:
                logger.info(f"   - {key}: {val}")
    else:
        logger.warning("   ⚠️ Не найдено переменных окружения с ключевыми словами BOT/SALES/COURSE/TOKEN")
        logger.warning("   ⚠️ Убедитесь, что переменные добавлены в Railway Variables и привязаны к сервису")

    # Диагностика БД (частая причина "пропал доступ" на Railway — неперсистентный диск/другой путь)
    try:
        db_path = (os.environ.get("DATABASE_PATH") or Config.DATABASE_PATH or "").strip()
        logger.info(f"🗄️ DATABASE_PATH: '{db_path}'")
        # Диагностика Volume (на Railway Volume виден как mountpoint в /proc/mounts)
        try:
            mounts_file = Path("/proc/mounts")
            if mounts_file.exists():
                mounts_text = mounts_file.read_text(encoding="utf-8", errors="ignore")
                is_app_data_mounted = any(
                    line.split()[1] == "/app/data"
                    for line in mounts_text.splitlines()
                    if line and len(line.split()) >= 2
                )
                logger.info(f"🧩 Volume mount check: /app/data mounted = {is_app_data_mounted}")
                if not is_app_data_mounted:
                    logger.warning("⚠️ /app/data НЕ является отдельным mountpoint. Если вы ожидаете Railway Volume — он, вероятно, не подключён.")
                    logger.warning("⚠️ Без Volume SQLite будет храниться на эфемерном диске и доступ пользователей может 'слетать' после restart/redeploy.")
            else:
                logger.info("🧩 Volume mount check: /proc/mounts недоступен (не Linux контейнер?)")
        except Exception as e:
            logger.warning(f"⚠️ Не удалось проверить mountpoints (/proc/mounts): {e}")

        if db_path:
            p = Path(db_path)
            # Для относительных путей показываем абсолютный (в контейнере будет /app/...)
            logger.info(f"🗄️ DATABASE_PATH resolved: '{p.resolve()}' (exists: {p.exists()})")
            logger.info(f"🗄️ DB parent dir: '{p.parent.resolve()}' (exists: {p.parent.exists()})")
            # Проверяем возможность записи (если директория не writable — БД не сможет сохраняться/обновляться)
            try:
                if p.parent.exists():
                    test_file = p.parent / ".write_test"
                    test_file.write_text("ok", encoding="utf-8")
                    test_file.unlink(missing_ok=True)
                    logger.info("🗄️ DB directory writable: True")
            except Exception as e:
                logger.warning(f"⚠️ DB directory writable: False ({e})")
            if not p.exists():
                logger.warning("⚠️ Файл БД не найден. Если это Railway и вы делали redeploy/restart без Volume — доступ пользователей будет 'пропадать'.")
                logger.warning("⚠️ Решение: подключить Volume и поставить DATABASE_PATH на примонтированный путь (например /app/data/course_platform.db).")
    except Exception as e:
        logger.warning(f"⚠️ Не удалось выполнить диагностику DATABASE_PATH: {e}")
    
    # Теперь проверяем конфигурацию (после запуска HTTP сервера)
    try:
        # Диагностика: проверяем, какие переменные установлены (без логирования самих токенов)
        sales_token_set = bool(Config.SALES_BOT_TOKEN)
        course_token_set = bool(Config.COURSE_BOT_TOKEN)
        admin_token_set = bool(Config.ADMIN_BOT_TOKEN)
        logger.info(f"📋 Проверка конфигурации: SALES_BOT_TOKEN={('✅ установлен' if sales_token_set else '❌ не установлен')}, COURSE_BOT_TOKEN={('✅ установлен' if course_token_set else '❌ не установлен')}, ADMIN_BOT_TOKEN={('✅ установлен' if admin_token_set else '❌ не установлен')}")
        
        if not Config.validate():
            logger.error("❌ Неверная конфигурация: отсутствуют обязательные переменные окружения")
            logger.error("⚠️ Убедитесь, что в Railway Variables установлены:")
            logger.error("   - SALES_BOT_TOKEN")
            logger.error("   - COURSE_BOT_TOKEN")
            logger.error("   - ADMIN_BOT_TOKEN (опционально)")
            logger.error("⚠️ HTTP сервер работает, но боты не могут запуститься")
            # Не выходим, чтобы healthcheck продолжал работать
            # Просто ждем бесконечно, чтобы контейнер не перезапускался
            if web_runner:
                logger.info("🌐 HTTP сервер работает. Ожидание исправления конфигурации...")
                while True:
                    await asyncio.sleep(60)
            return
        
        logger.info("✅ Конфигурация валидна, все обязательные переменные установлены")
    except Exception as e:
        logger.error(f"❌ Ошибка при проверке конфигурации: {e}", exc_info=True)
        if web_runner:
            logger.info("🌐 HTTP сервер работает. Ожидание исправления конфигурации...")
            while True:
                await asyncio.sleep(60)
        return
    
    try:
        # Инициализация ботов
        logger.info("Инициализация продающего бота...")
        sales_bot = None
        try:
            sales_bot = SalesBot()
            logger.info("✅ Продающий бот инициализирован")
            # Expose sales_bot to webhook app (payment_service/db are inside)
            web_app["sales_bot"] = sales_bot
        except Exception as e:
            logger.error(f"❌ Ошибка при инициализации продающего бота: {e}", exc_info=True)
            # Не падаем, продолжаем с другим ботом
            logger.warning("⚠️ Продолжаем без продающего бота")
            sales_bot = None
        
        logger.info("Инициализация курс-бота...")
        try:
            course_bot = CourseBot()
            logger.info("✅ Курс-бот инициализирован")
            web_app["course_bot"] = course_bot
        except Exception as e:
            logger.error(f"❌ Ошибка при инициализации курс-бота: {e}", exc_info=True)
            # Не падаем, продолжаем
            logger.warning("⚠️ Продолжаем без курс-бота")
            course_bot = None
        
        logger.info("Инициализация админ-бота...")
        admin_bot = None
        try:
            if Config.ADMIN_BOT_TOKEN:
                try:
                    admin_bot = AdminBot()
                    logger.info("✅ Админ-бот инициализирован")
                    web_app["admin_bot"] = admin_bot
                except ValueError as ve:
                    # ADMIN_BOT_TOKEN not configured - это нормально, просто пропускаем
                    logger.warning(f"⚠️ Админ-бот не может быть инициализирован: {ve}")
                    admin_bot = None
                except Exception as e:
                    logger.error(f"❌ Ошибка при инициализации админ-бота: {e}", exc_info=True)
                    logger.warning("⚠️ Продолжаем без админ-бота")
                    admin_bot = None
            else:
                logger.warning("⚠️ ADMIN_BOT_TOKEN не установлен, админ-бот не будет запущен")
        except Exception as e:
            logger.error(f"❌ Критическая ошибка при проверке админ-бота: {e}", exc_info=True)
            logger.warning("⚠️ Продолжаем без админ-бота")
            admin_bot = None
        
        # Запуск ботов параллельно (если они были инициализированы)
        tasks = []
        if sales_bot:
            logger.info("Запуск продающего бота...")
            tasks.append(asyncio.create_task(sales_bot.start()))
        
        if course_bot:
            logger.info("Запуск курс-бота...")
            tasks.append(asyncio.create_task(course_bot.start()))
        
        if admin_bot:
            logger.info("Запуск админ-бота...")
            tasks.append(asyncio.create_task(admin_bot.start()))
        
        if tasks:
            logger.info(f"✅ Запущено {len(tasks)} бот(ов). Все сервисы готовы к работе")
            # Ждем завершения всех задач
            try:
                await asyncio.gather(*tasks, return_exceptions=True)
            except asyncio.CancelledError:
                logger.info("Получен сигнал остановки")
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
        else:
            logger.warning("⚠️ Ни один бот не запущен, но HTTP сервер работает")
            # Ждем бесконечно, чтобы контейнер не перезапускался
            if web_runner:
                while True:
                    await asyncio.sleep(60)
                    
    except KeyboardInterrupt:
        logger.info("Остановка ботов...")
    except Exception as e:
        logger.error(f"❌ Критическая ошибка: {e}", exc_info=True)
        # Не выходим, чтобы HTTP сервер продолжал работать для healthcheck
        logger.warning("⚠️ Ошибка в ботах, но HTTP сервер продолжает работать")
        if web_runner:
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
        
        if admin_bot:
            try:
                await admin_bot.stop()
            except Exception as e:
                logger.error(f"Ошибка при остановке админ-бота: {e}")
        
        # Stop aiohttp server
        if web_runner:
            try:
                await web_runner.cleanup()
            except Exception as e:
                logger.error(f"Ошибка при остановке HTTP сервера: {e}")
        logger.info("Все сервисы остановлены")


if __name__ == "__main__":
    try:
        logger.info("=" * 60)
        logger.info("🚀 Запуск приложения...")
        logger.info("=" * 60)
        # Используем asyncio.run для правильного запуска
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Завершение работы...")
    except Exception as e:
        logger.error(f"❌ Фатальная ошибка при запуске: {e}", exc_info=True)
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
        sys.exit(1)

