"""
Sales & Payment Bot

Handles:
- User referrals (via ?start=partner_id)
- Course presentation
- Tariff selection
- Payment processing
- Access granting
- Group invitations
"""

import asyncio
import logging
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup
from aiogram.enums import ParseMode

from core.config import Config
from core.database import Database
from core.models import Tariff
from payment.mock_payment import MockPaymentProcessor
from services.user_service import UserService
from services.payment_service import PaymentService
from services.community_service import CommunityService
from utils.telegram_helpers import create_tariff_keyboard, format_tariff_description

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class SalesBot:
    """Sales and Payment Bot implementation."""
    
    def __init__(self):
        self.bot = Bot(token=Config.SALES_BOT_TOKEN, parse_mode=ParseMode.HTML)
        self.dp = Dispatcher()
        self.db = Database()
        self.payment_processor = MockPaymentProcessor()
        self.payment_service = PaymentService(self.db, self.payment_processor)
        self.user_service = UserService(self.db)
        self.community_service = CommunityService()
        
        # Register handlers
        self._register_handlers()
    
    def _register_handlers(self):
        """Register all bot handlers."""
        # Используем декораторы для регистрации handlers
        self.dp.message.register(self.handle_start, CommandStart())
        self.dp.message.register(self.handle_help, Command("help"))
        self.dp.callback_query.register(self.handle_tariff_selection, F.data.startswith("tariff:"))
        self.dp.callback_query.register(self.handle_payment_initiate, F.data.startswith("pay:"))
        self.dp.callback_query.register(self.handle_payment_check, F.data.startswith("check_payment:"))
        self.dp.callback_query.register(self.handle_cancel, F.data == "cancel")
        
        logger.info("Handlers registered successfully")
    
    async def handle_start(self, message: Message):
        """
        Handle /start command with optional referral parameter.
        
        Supports:
        - /start (direct access)
        - /start partner_id (referral link)
        """
        try:
            logger.info(f"Received /start from user {message.from_user.id}")
            
            # Сначала отправляем простой ответ для проверки
            await message.answer("🔄 Обработка команды /start...")
            
            user_id = message.from_user.id
            username = message.from_user.username
            first_name = message.from_user.first_name
            last_name = message.from_user.last_name
            
            logger.info(f"User info: {user_id}, {username}, {first_name}")
            
            # Extract referral partner ID from command arguments
            referral_partner_id = None
            if message.text and len(message.text.split()) > 1:
                referral_partner_id = message.text.split()[1]
                logger.info(f"User {user_id} accessed via referral: {referral_partner_id}")
            
            # Get or create user
            logger.info("Getting or creating user...")
            user = await self.user_service.get_or_create_user(
                user_id, username, first_name, last_name
            )
            logger.info(f"User created/retrieved: {user.user_id}, has_access: {user.has_access()}")
            
            # Store referral if provided
            if referral_partner_id and not user.referral_partner_id:
                user.referral_partner_id = referral_partner_id
                await self.db.update_user(user)
            
            # Check if user already has access
            if user.has_access():
                await message.answer(
                    f"👋 Welcome back, {first_name}!\n\n"
                    f"You already have access to the course with {user.tariff.value.upper()} tariff.\n\n"
                    f"Your course bot: @StartNowAI_bot\n"
                    f"Current day: {user.current_day}/30"
                )
                return
            
            # Show course description and tariffs
            logger.info("Showing course info...")
            await self._show_course_info(message, referral_partner_id, first_name)
            logger.info("Course info shown successfully")
        except Exception as e:
            logger.error(f"Error in handle_start: {e}", exc_info=True)
            try:
                await message.answer(f"❌ Произошла ошибка: {str(e)}\n\nПопробуйте позже или обратитесь в поддержку.")
            except:
                pass
    
    async def handle_help(self, message: Message):
        """Handle /help command."""
        await message.answer(
            "📚 <b>Course Sales Bot</b>\n\n"
            "Use /start to begin and see available tariffs.\n\n"
            "This bot helps you:\n"
            "• Browse course options\n"
            "• Select a tariff\n"
            "• Complete payment\n"
            "• Get access to the course"
        )
    
    async def _show_course_info(self, message: Message, referral_partner_id: str = None, first_name: str = None):
        """Show course information and tariff options."""
        # Приветствие с упоминанием партнёра, если есть
        greeting = ""
        if referral_partner_id:
            greeting = f"👋 Привет, {first_name or 'друг'}!\n\n"
            greeting += f"Вы пришли по рекомендации партнёра <b>{referral_partner_id}</b>.\n"
            greeting += "Добро пожаловать!\n\n"
        else:
            greeting = f"👋 Добро пожаловать, {first_name or 'друг'}!\n\n"
        
        course_description = (
            f"{greeting}"
            "🎓 <b>О курсе</b>\n\n"
            "Это 30-дневный курс, который поможет вам достичь ваших целей.\n\n"
            "📅 <b>Формат:</b>\n"
            "• 30 автоматических ежедневных уроков\n"
            "• Практические задания\n"
            "• Поддержка сообщества\n"
            "• Обратная связь от лидера (в выбранных тарифах)\n\n"
            "Выберите тариф ниже:"
        )
        
        keyboard = create_tariff_keyboard()
        await message.answer(course_description, reply_markup=keyboard)
    
    async def handle_tariff_selection(self, callback: CallbackQuery):
        """Handle tariff selection callback."""
        await callback.answer()
        
        tariff_str = callback.data.split(":")[1]
        tariff = Tariff(tariff_str)
        
        user_id = callback.from_user.id
        user = await self.user_service.get_or_create_user(
            user_id,
            callback.from_user.username,
            callback.from_user.first_name,
            callback.from_user.last_name
        )
        
        # Show tariff details
        description = format_tariff_description(tariff)
        await callback.message.edit_text(
            description + "\n\n💳 Proceed with payment?",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ Pay Now",
                        callback_data=f"pay:{tariff.value}"
                    ),
                    InlineKeyboardButton(
                        text="❌ Cancel",
                        callback_data="cancel"
                    )
                ]
            ])
        )
    
    async def handle_payment_initiate(self, callback: CallbackQuery):
        """Handle payment initiation."""
        await callback.answer()
        
        tariff_str = callback.data.split(":")[1]
        tariff = Tariff(tariff_str)
        
        user_id = callback.from_user.id
        user = await self.user_service.get_or_create_user(
            user_id,
            callback.from_user.username,
            callback.from_user.first_name,
            callback.from_user.last_name
        )
        
        # Initiate payment
        payment_info = await self.payment_service.initiate_payment(
            user_id=user_id,
            tariff=tariff,
            referral_partner_id=user.referral_partner_id
        )
        
        payment_id = payment_info["payment_id"]
        payment_url = payment_info["payment_url"]
        
        # Show payment information
        await callback.message.edit_text(
            f"💳 <b>Payment Required</b>\n\n"
            f"Tariff: <b>{tariff.value.upper()}</b>\n"
            f"Amount: ${PaymentService.TARIFF_PRICES[tariff]}\n\n"
            f"Click the button below to complete payment:\n\n"
            f"<i>Note: This is a mock payment system. Payment will auto-complete in 5 seconds.</i>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="💳 Pay Now",
                        url=payment_url
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🔄 Check Payment Status",
                        callback_data=f"check_payment:{payment_id}"
                    )
                ]
            ])
        )
        
        # In production, you might want to:
        # 1. Poll payment status in background
        # 2. Set up webhook handler for payment notifications
        # 3. Automatically check and grant access when payment completes
    
    async def handle_cancel(self, callback: CallbackQuery):
        """Handle cancel action."""
        await callback.answer("Cancelled")
        await callback.message.edit_text("Payment cancelled. Use /start to begin again.")
    
    async def handle_payment_check(self, callback: CallbackQuery):
        """Handle payment status check callback."""
        await callback.answer()
        
        payment_id = callback.data.split(":")[1]
        status = await self.payment_service.check_payment(payment_id)
        
        if status.value == "completed":
            # Process payment completion
            result = await self.payment_service.process_payment_completion(payment_id)
            
            if result:
                user = result["user"]
                await self._grant_access_and_notify(callback.message, user)
            else:
                await callback.message.edit_text(
                    "Payment completed, but there was an error processing your access. "
                    "Please contact support."
                )
        else:
            await callback.message.edit_text(
                f"Payment status: {status.value}\n\n"
                "Please wait for payment confirmation..."
            )
    
    async def _grant_access_and_notify(self, message: Message, user):
        """
        Grant access to course and send onboarding message.
        
        This is called after successful payment to:
        1. Send onboarding message
        2. Invite user to course bot
        3. Invite user to appropriate groups
        """
        # Send onboarding message
        onboarding_text = (
            f"🎉 <b>Поздравляем, {user.first_name}!</b>\n\n"
            f"Ваша оплата подтверждена.\n"
            f"Тариф: <b>{user.tariff.value.upper()}</b>\n\n"
            f"📚 <b>Сегодня — День 1 вашего путешествия!</b>\n\n"
            f"Нажмите кнопку ниже, чтобы начать курс 👇"
        )
        
        # Кнопка для перехода в курс-бот
        from aiogram.types import InlineKeyboardButton
        course_keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🚀 Перейти в курс",
                    url=f"https://t.me/StartNowAI_bot?start=course"
                )
            ]
        ])
        
        await message.answer(onboarding_text, reply_markup=course_keyboard)
        
        # Get groups user should have access to
        groups = self.community_service.get_groups_for_user(user)
        
        if groups:
            group_text = "🔗 <b>Join your communities:</b>\n\n"
            for group_id in groups:
                invite_link = self.community_service.get_group_invite_link(group_id)
                group_text += f"• <a href='{invite_link}'>Community Group</a>\n"
            
            await message.answer(group_text, disable_web_page_preview=True)
        
        # Note: In production, you would:
        # 1. Use bot API to actually invite user to groups
        # 2. Send first lesson immediately via course bot
        # 3. Set up webhook or polling to course bot to trigger first lesson
    
    async def start(self):
        """Start the bot."""
        try:
            await self.db.connect()
            logger.info("Sales Bot started")
            logger.info("Bot is ready to receive messages")
            await self.dp.start_polling(self.bot, skip_updates=True)
        except Exception as e:
            logger.error(f"Error starting bot: {e}", exc_info=True)
            raise
    
    async def stop(self):
        """Stop the bot."""
        await self.db.close()
        await self.bot.session.close()


async def main():
    """Main entry point."""
    if not Config.validate():
        logger.error("Invalid configuration. Please check your .env file.")
        return
    
    bot = None
    try:
        bot = SalesBot()
        logger.info("Initializing Sales Bot...")
        await bot.start()
    except KeyboardInterrupt:
        logger.info("Stopping bot...")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
    finally:
        if bot:
            try:
                await bot.stop()
            except Exception as e:
                logger.error(f"Error stopping bot: {e}")


if __name__ == "__main__":
    asyncio.run(main())

