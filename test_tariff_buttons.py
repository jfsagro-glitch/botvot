"""Тест кнопок тарифа"""
import asyncio
import logging
import sys
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import Message, CallbackQuery
from core.config import Config
from utils.telegram_helpers import create_tariff_keyboard

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

async def handle_start(message: Message):
    """Обработчик /start"""
    logger.info(f"✅ START: User {message.from_user.id}")
    keyboard = create_tariff_keyboard()
    await message.answer("Выберите тариф:", reply_markup=keyboard)

async def handle_tariff(callback: CallbackQuery):
    """Обработчик выбора тарифа"""
    logger.info("=" * 60)
    logger.info(f"✅✅✅ TARIFF HANDLER ВЫЗВАН! ✅✅✅")
    logger.info(f"   Callback data: {callback.data}")
    logger.info(f"   User: {callback.from_user.id}")
    logger.info("=" * 60)
    
    await callback.answer()
    tariff_str = callback.data.split(":")[1]
    await callback.message.edit_text(f"✅ Выбран тариф: {tariff_str.upper()}")

async def main():
    """Главная функция"""
    try:
        bot = Bot(token=Config.SALES_BOT_TOKEN)
        dp = Dispatcher()
        
        # Регистрация обработчиков
        dp.message.register(handle_start, CommandStart())
        dp.callback_query.register(handle_tariff, F.data.startswith("tariff:"))
        
        me = await bot.get_me()
        logger.info(f"✅ Бот: @{me.username}")
        logger.info("")
        logger.info("=" * 60)
        logger.info("ОТПРАВЬТЕ /start В: t.me/StartNowQ_bot")
        logger.info("НАЖМИТЕ НА КНОПКУ ТАРИФА")
        logger.info("=" * 60)
        logger.info("")
        
        await dp.start_polling(bot, skip_updates=True)
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}", exc_info=True)
    finally:
        if 'bot' in locals():
            await bot.session.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Остановка...")

