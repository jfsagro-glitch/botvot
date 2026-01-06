"""
Configuration management for the Telegram Course Platform.

Loads configuration from environment variables and provides
centralized access to all system settings.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()


class Config:
    """Application configuration."""
    
    # Telegram Bot Tokens
    SALES_BOT_TOKEN: str = os.getenv("SALES_BOT_TOKEN", "")
    COURSE_BOT_TOKEN: str = os.getenv("COURSE_BOT_TOKEN", "")
    
    # Admin Chat ID (for assignment feedback)
    ADMIN_CHAT_ID: int = int(os.getenv("ADMIN_CHAT_ID", "0"))
    
    # Telegram Group Chat IDs
    GENERAL_GROUP_ID: str = os.getenv("GENERAL_GROUP_ID", "")
    PREMIUM_GROUP_ID: str = os.getenv("PREMIUM_GROUP_ID", "")
    
    # Database
    DATABASE_PATH: str = os.getenv("DATABASE_PATH", "./data/course_platform.db")
    
    # Course Settings
    COURSE_DURATION_DAYS: int = int(os.getenv("COURSE_DURATION_DAYS", "30"))
    LESSON_INTERVAL_HOURS: int = int(os.getenv("LESSON_INTERVAL_HOURS", "24"))
    
    # Payment Settings
    PAYMENT_PROVIDER: str = os.getenv("PAYMENT_PROVIDER", "mock")  # "mock" or "yookassa"
    
    # YooKassa Settings (if PAYMENT_PROVIDER == "yookassa")
    YOOKASSA_SHOP_ID: str = os.getenv("YOOKASSA_SHOP_ID", "")
    YOOKASSA_SECRET_KEY: str = os.getenv("YOOKASSA_SECRET_KEY", "")
    YOOKASSA_RETURN_URL: str = os.getenv("YOOKASSA_RETURN_URL", "https://t.me/StartNowQ_bot")
    
    # Payment Currency
    PAYMENT_CURRENCY: str = os.getenv("PAYMENT_CURRENCY", "RUB")  # RUB, USD, EUR, etc.
    
    @classmethod
    def validate(cls) -> bool:
        """Validate that required configuration is present."""
        required = [
            cls.SALES_BOT_TOKEN,
            cls.COURSE_BOT_TOKEN,
        ]
        return all(required)
    
    @classmethod
    def ensure_data_directory(cls):
        """Ensure data directory exists for database."""
        db_path = Path(cls.DATABASE_PATH)
        db_path.parent.mkdir(parents=True, exist_ok=True)

