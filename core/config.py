"""
Configuration management for the Telegram Course Platform.

Loads configuration from environment variables and provides
centralized access to all system settings.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

import logging

# Load environment variables from .env file
load_dotenv()


def _get_env_value(key: str, default: str = "") -> str:
    """Получает переменную окружения с очисткой пробелов."""
    value = os.environ.get(key, "")
    if not value:
        value = os.getenv(key, default)
    # Удаляем пробелы в начале и конце (частая ошибка при копировании)
    return value.strip() if value else default


def _normalize_env_name(raw: str) -> str:
    """Normalize ENV value to canonical tokens ('prod', 'staging', etc.)."""
    token = (raw or "").strip().lower()
    if not token:
        return "prod"
    if token in {"prod", "production", "live"}:
        return "prod"
    if token in {"staging", "stage", "stg"}:
        return "staging"
    return token


_CURRENT_ENV = _normalize_env_name(_get_env_value("ENV", "prod"))
_ENV_PREFIX = {
    "prod": "PROD",
    "staging": "STG",
}.get(_CURRENT_ENV, _CURRENT_ENV.upper())

# Track which environment variable actually provided each value (for diagnostics).
_RESOLVED_ENV_VARS: dict = {}


def _get_env_specific_value(key: str, default: str = "") -> str:
    """
    Retrieve an environment-specific variable based on ENV prefix (e.g. STG_, PROD_).
    Falls back to the unprefixed key and finally to the provided default.
    """
    candidates = []
    if _ENV_PREFIX:
        candidates.append(f"{_ENV_PREFIX}_{key}")
        # Accept a few common aliases for convenience.
        if _ENV_PREFIX == "STG":
            candidates.extend([f"STAGING_{key}", f"STAGE_{key}"])
        if _ENV_PREFIX == "PROD":
            candidates.append(f"PRODUCTION_{key}")

    for candidate in candidates:
        raw = os.environ.get(candidate)
        if raw is None:
            raw = os.getenv(candidate)
        if raw:
            value = raw.strip()
            if value:
                _RESOLVED_ENV_VARS[key] = candidate
                return value

    fallback_raw = os.environ.get(key)
    if fallback_raw is None:
        fallback_raw = os.getenv(key)
    if fallback_raw:
        value = fallback_raw.strip()
        if value:
            _RESOLVED_ENV_VARS[key] = key
            return value

    # No env var, use default.
    _RESOLVED_ENV_VARS[key] = None if not default else "default"
    return default


def _default_database_path(env: str) -> str:
    """
    Provide sensible default DB path per environment.
    Prod keeps historical path for backwards compatibility.
    """
    if env == "staging":
        return "./data/staging/course_platform.db"
    if env == "prod":
        return "./data/course_platform.db"
    return f"./data/{env}/course_platform.db"


def _parse_chat_id(raw: str) -> int:
    """
    Parse Telegram chat id from env formats:
    - "-100123..." / "123" / "-123"
    - "#-123456789" (web.telegram internal) -> "-100123456789"
    """
    s = (raw or "").strip()
    if not s:
        return 0

    # web.telegram format: "#-<digits>"
    if s.startswith("#-") and s[2:].isdigit():
        digits = s[2:]
        return int(f"-100{digits}")

    # already numeric
    try:
        return int(s)
    except Exception:
        return 0


class Config:
    """Application configuration."""
    
    ENV: str = _CURRENT_ENV
    ENV_PREFIX: str = _ENV_PREFIX
    RESOLVED_ENV_KEYS: dict = _RESOLVED_ENV_VARS

    # Telegram Bot Tokens
    # Используем os.environ напрямую для надежного чтения переменных окружения
    # Railway устанавливает переменные в os.environ
    SALES_BOT_TOKEN: str = _get_env_specific_value("SALES_BOT_TOKEN", "")
    COURSE_BOT_TOKEN: str = _get_env_specific_value("COURSE_BOT_TOKEN", "")
    ADMIN_BOT_TOKEN: str = _get_env_specific_value("ADMIN_BOT_TOKEN", "")
    
    # Admin Chat ID (for assignment feedback)
    # Read as string first, then convert to int (supports negative IDs for groups)
    ADMIN_CHAT_ID: int = _parse_chat_id(_get_env_specific_value("ADMIN_CHAT_ID", ""))
    
    # Curator Group ID (for questions from users)
    CURATOR_GROUP_ID: str = _get_env_specific_value("CURATOR_GROUP_ID", "")
    
    # Telegram Group Chat IDs
    # Note: use _get_env_value to strip spaces (common copy/paste issue in Railway Variables)
    GENERAL_GROUP_ID: str = _get_env_specific_value("GENERAL_GROUP_ID", "")
    PREMIUM_GROUP_ID: str = _get_env_specific_value("PREMIUM_GROUP_ID", "")
    
    # Telegram Group Invite Links (preferred over IDs for opening chats)
    # Example: https://t.me/+AbCdEfGhIjk or https://t.me/joinchat/AbCdEfGhIjk
    GENERAL_GROUP_INVITE_LINK: str = _get_env_specific_value("GENERAL_GROUP_INVITE_LINK", "")
    PREMIUM_GROUP_INVITE_LINK: str = _get_env_specific_value("PREMIUM_GROUP_INVITE_LINK", "")

    # Community / Discussion (optional direct URL for the "Обсуждение" button)
    # Recommended: a Telegram invite link like https://t.me/+AbCdEfGhIjk
    # You can also use a web.telegram.org link, but it's less reliable on mobile clients.
    DISCUSSION_GROUP_URL: str = _get_env_specific_value("DISCUSSION_GROUP_URL", "")
    
    # Database
    DATABASE_URL: str = _get_env_specific_value("DATABASE_URL", "")
    DATABASE_PATH: str = _get_env_specific_value("DATABASE_PATH", _default_database_path(ENV))

    # Content Sync (Google Drive)
    # If configured, admins can run /sync_content to pull lessons/tasks/media from Drive
    DRIVE_CONTENT_ENABLED: str = _get_env_specific_value("DRIVE_CONTENT_ENABLED", "0")  # "1" to enable
    DRIVE_ROOT_FOLDER_ID: str = _get_env_specific_value("DRIVE_ROOT_FOLDER_ID", "")
    # Optional: single master Google Doc that contains all lessons/tasks.
    # If set, Drive sync will parse lessons from this doc (instead of per-day folders).
    DRIVE_MASTER_DOC_ID: str = _get_env_specific_value("DRIVE_MASTER_DOC_ID", "")
    # Provide one of these (preferred: JSON string; alternative: base64-encoded JSON)
    GOOGLE_SERVICE_ACCOUNT_JSON: str = _get_env_specific_value("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    GOOGLE_SERVICE_ACCOUNT_JSON_B64: str = _get_env_specific_value("GOOGLE_SERVICE_ACCOUNT_JSON_B64", "")
    # Where to store synced media relative to project root (/app in container)
    DRIVE_MEDIA_DIR: str = _get_env_specific_value("DRIVE_MEDIA_DIR", "data/content_media")
    # Optional: auto-sync interval (minutes). 0 = disabled.
    DRIVE_AUTO_SYNC_MINUTES: int = int(_get_env_specific_value("DRIVE_AUTO_SYNC_MINUTES", "0") or "0")
    
    # Course Settings
    COURSE_DURATION_DAYS: int = int(os.getenv("COURSE_DURATION_DAYS", "30"))
    LESSON_INTERVAL_HOURS: int = int(os.getenv("LESSON_INTERVAL_HOURS", "24"))

    # Scheduling / Timezone
    # Used for mentor reminder window calculations. Default: Moscow time.
    SCHEDULE_TIMEZONE: str = _get_env_value("SCHEDULE_TIMEZONE", "Europe/Moscow")
    # Daily lesson delivery time in local time (SCHEDULE_TIMEZONE)
    # Example: "08:30"
    LESSON_DELIVERY_TIME_LOCAL: str = _get_env_value("LESSON_DELIVERY_TIME_LOCAL", "08:30")
    # Mentor reminders are allowed only within this local-time window
    MENTOR_REMINDER_START_LOCAL: str = _get_env_value("MENTOR_REMINDER_START_LOCAL", "09:30")
    MENTOR_REMINDER_END_LOCAL: str = _get_env_value("MENTOR_REMINDER_END_LOCAL", "22:00")
    
    # Payment Settings
    PAYMENT_PROVIDER: str = _get_env_value("PAYMENT_PROVIDER", "mock")  # "mock" or "yookassa"
    
    # YooKassa Settings (if PAYMENT_PROVIDER == "yookassa")
    YOOKASSA_SHOP_ID: str = _get_env_value("YOOKASSA_SHOP_ID", "")
    YOOKASSA_SECRET_KEY: str = _get_env_value("YOOKASSA_SECRET_KEY", "")
    YOOKASSA_RETURN_URL: str = _get_env_value("YOOKASSA_RETURN_URL", "https://t.me/StartNowQ_bot")

    # YooKassa receipt (54-FZ)
    # Some YooKassa shops require receipt data in every payment request.
    # In this project we collect user's email in SalesBot and pass it to payment metadata.
    YOOKASSA_RECEIPT_REQUIRED: str = _get_env_value("YOOKASSA_RECEIPT_REQUIRED", "0")  # "1" to enforce
    # Tax system code (1-6). Common: 2 (УСН доход), 3 (УСН доход-расход).
    YOOKASSA_TAX_SYSTEM_CODE: str = _get_env_value("YOOKASSA_TAX_SYSTEM_CODE", "2")
    # VAT code. Common: 1 (без НДС), 6 (20%).
    YOOKASSA_VAT_CODE: str = _get_env_value("YOOKASSA_VAT_CODE", "1")
    
    # Payment Currency
    PAYMENT_CURRENCY: str = _get_env_value("PAYMENT_CURRENCY", "RUB")  # RUB, USD, EUR, etc.

    # Sales Bot: new linear screen flow (format → program → step → plans → payment).
    # When "1" or "true", /start for users without access shows "Выберите формат" (online/offline) instead of old menu.
    # Default "0" so existing behaviour is unchanged.
    SALES_NEW_FLOW: str = _get_env_value("SALES_NEW_FLOW", "0").strip().lower()
    
    @classmethod
    def is_sales_new_flow_enabled(cls) -> bool:
        """True if new linear sales flow is enabled (SALES_NEW_FLOW=1 or true)."""
        return cls.SALES_NEW_FLOW in ("1", "true", "yes")
    
    @classmethod
    def validate(cls) -> bool:
        """Validate that required configuration is present."""
        # Перечитываем переменные окружения для надежности с очисткой пробелов
        sales_token = _get_env_specific_value("SALES_BOT_TOKEN", cls.SALES_BOT_TOKEN)
        course_token = _get_env_specific_value("COURSE_BOT_TOKEN", cls.COURSE_BOT_TOKEN)
        admin_token = _get_env_specific_value("ADMIN_BOT_TOKEN", cls.ADMIN_BOT_TOKEN)
        database_path = _get_env_specific_value("DATABASE_PATH", cls.DATABASE_PATH)
        
        # Обновляем значения в классе
        cls.SALES_BOT_TOKEN = sales_token.strip() if sales_token else ""
        cls.COURSE_BOT_TOKEN = course_token.strip() if course_token else ""
        cls.ADMIN_BOT_TOKEN = admin_token.strip() if admin_token else ""
        cls.DATABASE_PATH = database_path.strip() if database_path else ""
        
        # Проверяем, что токены не пустые (после очистки пробелов)
        sales_valid = bool(sales_token and sales_token.strip())
        course_valid = bool(course_token and course_token.strip())
        
        return sales_valid and course_valid
    
    @classmethod
    def ensure_data_directory(cls):
        """Ensure data directory exists for database."""
        db_path = Path(cls.DATABASE_PATH)
        logger = logging.getLogger(__name__)
        logger.debug(
            "Ensuring data directory for ENV=%s (database path: %s, resolved via: %s)",
            cls.ENV,
            db_path,
            cls.RESOLVED_ENV_KEYS.get("DATABASE_PATH"),
        )
        # Создаем директорию для базы данных
        # На Railway используется постоянное хранилище, но нужно убедиться, что директория существует
        try:
            db_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            # Логируем ошибку, но продолжаем работу
            logger.warning(f"⚠️ Не удалось создать директорию для БД: {e}")
            # Пытаемся использовать текущую директорию
            cls.DATABASE_PATH = "course_platform.db"
