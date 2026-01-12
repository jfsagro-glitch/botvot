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
import sys
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

from core.config import Config
from core.database import Database
from core.models import Tariff
from payment.base import PaymentStatus
from payment.mock_payment import MockPaymentProcessor
from services.user_service import UserService
from services.payment_service import PaymentService
from services.community_service import CommunityService
from services.question_service import QuestionService
from utils.telegram_helpers import create_tariff_keyboard, format_tariff_description, create_persistent_keyboard
from utils.premium_ui import (
    send_animated_message, send_typing_action,
    format_premium_header, format_premium_section, create_premium_separator,
    create_success_animation, format_price
)

# Try to import YooKassa processor (optional)
try:
    from payment.yookassa_payment import YooKassaPaymentProcessor
    YOOKASSA_AVAILABLE = True
except ImportError:
    YOOKASSA_AVAILABLE = False

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class SalesBot:
    """Sales and Payment Bot implementation."""
    
    def __init__(self):
        self.bot = Bot(token=Config.SALES_BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        self.dp = Dispatcher()
        self.db = Database()
        
        # Initialize payment processor based on configuration
        self.payment_processor = self._init_payment_processor()
        
        self.payment_service = PaymentService(self.db, self.payment_processor)
        self.user_service = UserService(self.db)
        self.community_service = CommunityService()
        self.question_service = QuestionService(self.db)
        
        # Initialize lesson loader with error handling
        try:
            self.lesson_loader = LessonLoader()  # For sending lesson 0
            logger.info("✅ LessonLoader initialized in SalesBot")
        except Exception as e:
            logger.error(f"❌ Failed to initialize LessonLoader in SalesBot: {e}", exc_info=True)
            logger.warning("⚠️ SalesBot will work, but lesson 0 won't be sent automatically")
            self.lesson_loader = None
        
        # Register handlers
        self._register_handlers()
    
    def _init_payment_processor(self):
        """Initialize payment processor based on configuration."""
        provider = Config.PAYMENT_PROVIDER.lower()
        
        if provider == "yookassa":
            if not YOOKASSA_AVAILABLE:
                logger.warning("YooKassa library not installed. Falling back to mock payment.")
                logger.warning("Install with: pip install yookassa")
                return MockPaymentProcessor()
            
            if not Config.YOOKASSA_SHOP_ID or not Config.YOOKASSA_SECRET_KEY:
                logger.warning("YooKassa credentials not configured. Falling back to mock payment.")
                logger.warning("Set YOOKASSA_SHOP_ID and YOOKASSA_SECRET_KEY in .env file")
                return MockPaymentProcessor()
            
            try:
                processor = YooKassaPaymentProcessor(
                    shop_id=Config.YOOKASSA_SHOP_ID,
                    secret_key=Config.YOOKASSA_SECRET_KEY,
                    return_url=Config.YOOKASSA_RETURN_URL
                )
                logger.info("✅ YooKassa payment processor initialized")
                return processor
            except Exception as e:
                logger.error(f"Failed to initialize YooKassa: {e}. Falling back to mock payment.")
                return MockPaymentProcessor()
        else:
            logger.info("Using mock payment processor (for development/testing)")
            return MockPaymentProcessor()
    
    def _register_handlers(self):
        """Register all bot handlers."""
        # Регистрация обработчиков сообщений
        # ВАЖНО: Регистрируем обработчики в правильном порядке
        self.dp.message.register(self.handle_start, CommandStart())
        self.dp.message.register(self.handle_help, Command("help"))
        self.dp.message.register(self.handle_author, Command("author"))
        
        # Регистрация обработчиков callback query
        # ВАЖНО: Порядок регистрации важен - более специфичные первыми
        self.dp.callback_query.register(self.handle_upgrade_tariff, F.data == "upgrade_tariff")
        self.dp.callback_query.register(self.handle_tariff_selection, F.data.startswith("tariff:"))
        self.dp.callback_query.register(self.handle_upgrade_tariff_selection, F.data.startswith("upgrade:"))
        self.dp.callback_query.register(self.handle_back_to_tariffs, F.data == "back_to_tariffs")
        self.dp.callback_query.register(self.handle_payment_initiate, F.data.startswith("pay:"))
        self.dp.callback_query.register(self.handle_payment_check, F.data.startswith("check_payment:"))
        self.dp.callback_query.register(self.handle_cancel, F.data == "cancel")
        self.dp.callback_query.register(self.handle_talk_to_human, F.data == "sales:talk_to_human")
        self.dp.callback_query.register(self.handle_about_course, F.data == "sales:about_course")
        
        # Регистрация обработчиков для постоянных кнопок клавиатуры
        # ВАЖНО: Регистрируем ПЕРЕД общим обработчиком текста, чтобы они имели приоритет
        self.dp.message.register(self.handle_keyboard_upgrade, F.text == "⬆️ Апгрейд тарифа")
        self.dp.message.register(self.handle_keyboard_go_to_course, F.text == "📚 Перейти в курс")
        self.dp.message.register(self.handle_keyboard_select_tariff, F.text == "📋 Выбор тарифа")
        self.dp.message.register(self.handle_keyboard_about_course, F.text == "📖 О курсе")
        
        # Регистрация обработчика вопросов из sales bot (должен быть после кнопок, но перед общим текстом)
        self.dp.message.register(self.handle_question_from_sales, F.text & ~F.command)
        
        logger.info("✅ Handlers registered successfully")
        logger.info(f"   - CommandStart handler: {self.handle_start.__name__}")
        logger.info(f"   - Command help handler: {self.handle_help.__name__}")
        logger.info(f"   - Command author handler: {self.handle_author.__name__}")
        logger.info(f"   - Callback handlers: 7 registered")
        logger.info(f"     * upgrade_tariff -> handle_upgrade_tariff")
        logger.info(f"     * tariff: -> handle_tariff_selection")
        logger.info(f"     * upgrade: -> handle_upgrade_tariff_selection")
        logger.info(f"     * back_to_tariffs -> handle_back_to_tariffs")
        logger.info(f"     * pay: -> handle_payment_initiate")
        logger.info(f"     * check_payment: -> handle_payment_check")
        logger.info(f"     * cancel -> handle_cancel")
    
    async def handle_start(self, message: Message):
        """
        Handle /start command with optional referral parameter.
        
        Supports:
        - /start (direct access)
        - /start partner_id (referral link)
        """
        # ЛОГИРОВАНИЕ В САМОМ НАЧАЛЕ - ДО ВСЕГО
        logger.info("=" * 60)
        logger.info("✅✅✅ HANDLE_START ВЫЗВАН! ✅✅✅")
        logger.info(f"   User ID: {message.from_user.id}")
        logger.info(f"   Username: @{message.from_user.username}")
        logger.info(f"   Message text: {message.text}")
        logger.info(f"   Chat ID: {message.chat.id}")
        logger.info("=" * 60)
        
        try:
            # Отправляем анимированное сообщение
            await send_typing_action(self.bot, message.chat.id, 0.8)
            await message.answer("✨ <b>Добро пожаловать!</b> ✨\n\n⏳ Обрабатываю ваш запрос...")
            logger.info("✅ Первый ответ отправлен")
            
            user_id = message.from_user.id
            username = message.from_user.username
            first_name = message.from_user.first_name
            last_name = message.from_user.last_name
            
            logger.info(f"User info: {user_id}, {username}, {first_name}")
            
            # Extract referral partner ID or upgrade/tariffs parameter from command arguments
            referral_partner_id = None
            upgrade_requested = False
            tariffs_requested = False
            if message.text and len(message.text.split()) > 1:
                param = message.text.split()[1]
                if param == "upgrade":
                    upgrade_requested = True
                    logger.info(f"User {user_id} requested tariff upgrade")
                elif param == "tariffs":
                    tariffs_requested = True
                    logger.info(f"User {user_id} requested tariffs view")
                else:
                    referral_partner_id = param
                    logger.info(f"User {user_id} accessed via referral: {referral_partner_id}")
            
            # Get or create user
            logger.info("Getting or creating user...")
            try:
                user = await self.user_service.get_or_create_user(
                    user_id, username, first_name, last_name
                )
                logger.info(f"User created/retrieved: {user.user_id}, has_access: {user.has_access()}")
            except Exception as e:
                logger.error(f"Error creating user: {e}", exc_info=True)
                await message.answer("❌ Ошибка при создании пользователя. Попробуйте позже.")
                return
            
            # Store referral if provided
            if referral_partner_id and not user.referral_partner_id:
                user.referral_partner_id = referral_partner_id
                await self.db.update_user(user)
            
            # Если запрошены тарифы - показываем только тарифы
            if tariffs_requested:
                await self.handle_keyboard_select_tariff(message)
                return
            
            # Если запрошен апгрейд и пользователь имеет доступ - показываем меню апгрейда
            if upgrade_requested and user.has_access():
                await self._show_upgrade_menu(message, user, first_name)
                return
            # Если запрошен апгрейд, но пользователь не имеет доступа - показываем обычное меню
            elif upgrade_requested:
                await message.answer(
                    "❌ У вас нет активного доступа к курсу.\n\n"
                    "Для обновления тарифа сначала необходимо приобрести доступ к курсу."
                )
                await self._show_course_info(message, referral_partner_id, first_name)
                return
            
            # Check if user already has access
            if user.has_access():
                # Создаем премиальную клавиатуру с кнопкой смены тарифа
                from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
                
                # Прогресс обучения
                progress = int((user.current_day / 30) * 100)
                progress_bar = "█" * int(user.current_day / 3) + "░" * (10 - int(user.current_day / 3))
                
                welcome_back = (
                    f"✨ <b>Добро пожаловать обратно, {first_name}!</b> ✨\n\n"
                    f"{create_premium_separator()}\n\n"
                    f"📊 <b>Ваш прогресс:</b>\n"
                    f"{progress_bar} {progress}%\n"
                    f"День {user.current_day} из 30\n\n"
                    f"🎯 <b>Текущий тариф:</b> <b>{user.tariff.value.upper()}</b>\n"
                    f"🤖 <b>Курс-бот:</b> @StartNowAI_bot\n\n"
                    f"{create_premium_separator()}\n\n"
                    f"💎 <b>Хотите улучшить свой тариф?</b>\n"
                    f"Получите больше возможностей и поддержки!"
                )
                
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="⬆️ Апгрейд тарифа",
                            callback_data="upgrade_tariff"
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="📚 Перейти в курс",
                            url=f"https://t.me/StartNowAI_bot?start=course"
                        )
                    ]
                ])
                
                # Устанавливаем постоянную клавиатуру
                persistent_keyboard = create_persistent_keyboard()
                await message.answer(welcome_back, reply_markup=persistent_keyboard)
                await send_animated_message(self.bot, message.chat.id, "", keyboard, 0.5)
                return
            
            # Show course description and tariffs
            logger.info("Showing course info...")
            # Устанавливаем постоянную клавиатуру
            persistent_keyboard = create_persistent_keyboard()
            await message.answer("Используйте кнопки внизу для навигации 👇", reply_markup=persistent_keyboard)
            await self._show_course_info(message, referral_partner_id, first_name)
            logger.info("Course info shown successfully")
        except Exception as e:
            logger.error(f"❌ Error in handle_start: {e}", exc_info=True)
            try:
                await message.answer("❌ Произошла ошибка. Попробуйте позже.")
            except Exception as send_error:
                logger.error(f"Error sending error message: {send_error}")
    
    async def handle_help(self, message: Message):
        """Handle /help command with premium styling."""
        help_text = (
            f"{create_premium_separator()}\n"
            f"📚 <b>ПОМОЩЬ</b>\n"
            f"{create_premium_separator()}\n\n"
            f"✨ <b>Добро пожаловать в бот курса!</b>\n\n"
            f"<b>🚀 Основные команды:</b>\n"
            f"  /start — Начать и просмотреть доступные тарифы\n"
            f"  /help — Показать эту справку\n"
            f"  /author — Информация об авторе курса\n\n"
            f"{create_premium_separator()}\n\n"
            f"<b>💡 Что может этот бот:</b>\n"
            f"  ✅ Просмотр вариантов курса\n"
            f"  ✅ Выбор тарифа\n"
            f"  ✅ Оплата курса\n"
            f"  ✅ Получение доступа к курсу\n"
            f"  ✅ Апгрейд тарифа\n\n"
            f"{create_premium_separator()}\n\n"
            f"💬 <b>Нужна помощь?</b> Обратитесь в поддержку."
        )
        await send_animated_message(self.bot, message.chat.id, help_text, typing_duration=0.5)
    
    async def handle_author(self, message: Message):
        """Handle /author command - show information about course author."""
        author_info = (
            "👨‍🏫 <b>Об авторе курса</b>\n\n"
            "<b>Артём Никитин</b>\n\n"
            "Журналист, телеведущий, диктор, кинорежиссёр, музыкант, поэт.\n\n"
            "📺 <b>Опыт:</b>\n"
            "• Провёл более 3000 интервью с выдающимися людьми\n"
            "• Создатель фильмов и телевизионных проектов\n"
            "• Опыт работы в медиа и киноиндустрии\n\n"
            "🎓 <b>О курсе:</b>\n"
            "Телеграм-практикум «Вопросы, которые меняют всё» — это уникальный формат обучения, "
            "где Артём Никитин делится опытом, полученным в ходе тысяч интервью. "
            "Вы освоите искусство задавать вопросы не только для интервью, "
            "но также для карьеры и повседневной жизни.\n\n"
            "🌐 <a href='https://sites.google.com/view/nikitinartem'>Официальный сайт Артёма Никитина</a>\n\n"
            "Используйте /start для выбора тарифа и начала обучения."
        )
        await message.answer(author_info, disable_web_page_preview=False)
    
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
        
        # Премиальное оформление описания курса с анимацией
        # Отправляем приветствие с анимацией
        await send_typing_action(self.bot, message.chat.id, 0.8)
        
        # Первое сообщение - заголовок с анимацией
        header_message = (
            f"{create_premium_separator()}\n"
            f"✨ <b>ВОПРОСЫ, КОТОРЫЕ МЕНЯЮТ ВСЁ</b> ✨\n"
            f"{create_premium_separator()}\n\n"
            f"{greeting}"
            f"📱 <b>Телеграм-практикум</b>\n\n"
        )
        await message.answer(header_message)
        await asyncio.sleep(0.5)
        
        # Второе сообщение - проблема
        await send_typing_action(self.bot, message.chat.id, 0.6)
        problem_message = (
            f"💭 <b>Знакомо ли вам, когда...</b>\n\n"
            f"• Собеседник отвечает односложно, а вы не знаете, как разговорить?\n"
            f"• На мероприятии хочется подойти к интересному человеку, но не знаете, с чего начать?\n"
            f"• Коллеги и клиенты не раскрывают свой настоящий потенциал в общении с вами?\n"
            f"• Хочется строить глубокие связи, но вместо этого — только поверхностные контакты?\n\n"
            f"{create_premium_separator()}\n"
        )
        await message.answer(problem_message)
        await asyncio.sleep(0.5)
        
        # Третье сообщение - решение
        await send_typing_action(self.bot, message.chat.id, 0.7)
        solution_message = (
            f"🎯 <b>Что если через 30 дней вы сможете:</b>\n\n"
            f"✨ С первого вопроса создавать атмосферу доверия, где люди сами хотят раскрываться\n\n"
            f"✨ Превращать случайные знакомства в ценные связи для бизнеса и жизни\n\n"
            f"✨ Находить подход к любому человеку — от замкнутого подростка до важного клиента\n\n"
            f"✨ Строить личный бренд через искреннюю коммуникацию, привлекающую нужных людей\n\n"
            f"{create_premium_separator()}\n"
        )
        await message.answer(solution_message)
        await asyncio.sleep(0.5)
        
        # Четвертое сообщение - особенности
        await send_typing_action(self.bot, message.chat.id, 0.6)
        features_message = (
            f"💎 <b>Что делает этот практикум особенным:</b>\n\n"
            f"🎯 <b>Не теория, а пошаговая инструкция</b> — конкретные инструменты для раскрытия собеседника, которые работают сразу\n\n"
            f"🎯 <b>Система нетворкинга</b> — учитесь выстраивать связи, которые приведут к новым возможностям и проектам\n\n"
            f"🎯 <b>Практика с обратной связью</b> — применяете знания сразу, получаете фидбек и корректируете подход\n\n"
            f"🎯 <b>Среда единомышленников</b> — находите партнеров, клиентов и друзей среди участников\n\n"
            f"{create_premium_separator()}\n"
        )
        await message.answer(features_message)
        await asyncio.sleep(0.5)
        
        # Пятое сообщение - для кого
        await send_typing_action(self.bot, message.chat.id, 0.6)
        audience_message = (
            f"👥 <b>Кому это необходимо:</b>\n\n"
            f"💼 <b>Бизнесмену</b> — чтобы улучшить навыки нетворкинга\n"
            f"👔 <b>Руководителю</b> — чтобы быстро и детально распаковывать людей и информацию\n"
            f"💼 <b>Продажнику</b> — чтобы отточить искусство диалога и продавать больше\n"
            f"📚 <b>Преподавателю</b> — чтобы использовать вопросы как инструмент для достижения лучших результатов\n"
            f"📱 <b>Блогеру и журналисту</b> — чтобы начать вести интервью\n"
            f"🚀 <b>Профессионалу</b> — чтобы активнее расти и развиваться через правильные вопросы\n"
            f"💫 <b>Любому человеку</b> — желающему сделать свои диалоги, а значит и жизнь, более насыщенными и интересными\n\n"
            f"{create_premium_separator()}\n"
        )
        await message.answer(audience_message)
        await asyncio.sleep(0.5)
        
        # Шестое сообщение - формат
        await send_typing_action(self.bot, message.chat.id, 0.6)
        format_message = (
            f"📅 <b>Как это будет проходить:</b>\n\n"
            f"🔹 <b>Закрытая группа в Telegram</b> — уютное пространство для роста\n"
            f"🔹 <b>Ежедневные посты</b> — краткая теория + практическое задание\n"
            f"🔹 <b>Короткие задания на 5-10 минут</b> — легко встроить в любой график\n"
            f"🔹 <b>Ответы от мастера</b> — персональные комментарии к вашим вопросам и работам\n"
            f"🔹 <b>Поддержка сообщества</b> — обмен опытом с единомышленниками\n\n"
            f"{create_premium_separator()}\n"
        )
        await message.answer(format_message)
        await asyncio.sleep(0.5)
        
        # Седьмое сообщение - автор и призыв к действию
        await send_typing_action(self.bot, message.chat.id, 0.7)
        final_message = (
            f"👨‍🏫 <b>Об авторе:</b>\n\n"
            f"<b>Артём Никитин</b> — журналист, телеведущий, диктор, кинорежиссёр, музыкант, поэт.\n"
            f"Провёл <b>3000+ интервью</b> с выдающимися людьми.\n"
            f"Разрабатываю идеи, создаю текстовый, аудио- и видеоконтент с 2000 года.\n\n"
            f"🌐 <a href='https://sites.google.com/view/nikitinartem'>Официальный сайт Артёма Никитина</a>\n\n"
            f"{create_premium_separator()}\n\n"
            f"💬 <b>Важно:</b> После оплаты прислать своё имя в Telegram на <a href='https://t.me/niktatv'>@niktatv</a>, чтобы вас включили в рабочую группу.\n\n"
            f"{create_premium_separator()}\n\n"
            f"💎 <b>Это инвестиция в ваш главный актив — умение выстраивать качественные связи.</b>\n\n"
            f"🚀 <b>Начнем создавать вашу историю успеха через осознанное общение?</b>\n\n"
            f"{create_premium_separator()}\n\n"
            f"💎 <b>Выберите тариф ниже:</b>"
        )
        
        keyboard = create_tariff_keyboard()
        await send_animated_message(self.bot, message.chat.id, final_message, keyboard, 0.8)
    
    async def _show_upgrade_menu(self, message: Message, user, first_name: str):
        """Show tariff upgrade menu for user with access."""
        try:
            current_tariff = user.tariff
            current_price = PaymentService.TARIFF_PRICES[current_tariff]
            
            # Определяем доступные тарифы для апгрейда
            available_upgrades = []
            if current_tariff == Tariff.BASIC:
                available_upgrades = [
                    (Tariff.FEEDBACK, PaymentService.TARIFF_PRICES[Tariff.FEEDBACK])
                ]
            elif current_tariff == Tariff.FEEDBACK:
                await message.answer(
                    "✅ У вас уже максимальный доступный тариф!\n\n"
                    "Вы получаете:\n"
                    "• Все материалы курса\n"
                    "• Персональную обратную связь\n"
                    "• Доступ к общему сообществу участников"
                )
                return
            
            if not available_upgrades:
                await message.answer("❌ Нет доступных тарифов для апгрейда.")
                return
            
            # Формируем сообщение с доступными тарифами
            upgrade_text = (
                f"{create_premium_separator()}\n"
                f"🔄 <b>СМЕНА ТАРИФА (АПГРЕЙД)</b>\n"
                f"{create_premium_separator()}\n\n"
                f"👋 Привет, {first_name}!\n\n"
                f"Ваш текущий тариф: <b>{current_tariff.value.upper()}</b> ({current_price:.0f}₽)\n\n"
                f"Доступные тарифы для апгрейда:\n\n"
            )
            
            # Создаем клавиатуру с доступными тарифами
            keyboard_buttons = []
            for tariff, price in available_upgrades:
                price_diff = price - current_price
                tariff_name = tariff.value.upper()
                if tariff == Tariff.FEEDBACK:
                    tariff_name = "С ОБРАТНОЙ СВЯЗЬЮ"
                elif tariff == Tariff.PRACTIC:
                    tariff_name = "PRACTIC"
                
                upgrade_text += (
                    f"• <b>{tariff_name}</b> — {price:.0f}₽\n"
                    f"  (доплата: {price_diff:.0f}₽)\n\n"
                )
                
                keyboard_buttons.append([
                    InlineKeyboardButton(
                        text=f"⬆️ {tariff_name} (+{price_diff:.0f}₽)",
                        callback_data=f"upgrade:{tariff.value}"
                    )
                ])
            
            keyboard_buttons.append([
                InlineKeyboardButton(
                    text="❌ Отмена",
                    callback_data="cancel"
                )
            ])
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
            
            await send_animated_message(self.bot, message.chat.id, upgrade_text + "Выберите тариф для апгрейда:", keyboard, 0.5)
            
        except Exception as e:
            logger.error(f"❌ Error in _show_upgrade_menu: {e}", exc_info=True)
            try:
                await message.answer("❌ Ошибка при загрузке меню апгрейда. Попробуйте позже.")
            except:
                pass
    
    async def handle_tariff_selection(self, callback: CallbackQuery):
        """Handle tariff selection callback."""
        # ЛОГИРОВАНИЕ В САМОМ НАЧАЛЕ - ДО ВСЕГО
        logger.info("=" * 60)
        logger.info("✅✅✅ HANDLE_TARIFF_SELECTION ВЫЗВАН! ✅✅✅")
        logger.info(f"   Callback data: '{callback.data}'")
        logger.info(f"   User ID: {callback.from_user.id}")
        logger.info(f"   Username: @{callback.from_user.username}")
        logger.info("=" * 60)
        
        try:
            # Сначала отвечаем на callback, чтобы убрать индикатор загрузки
            try:
                await callback.answer()
                logger.info("   ✅ Callback answered")
            except Exception as answer_error:
                logger.warning(f"   Не удалось ответить на callback (возможно устарел): {answer_error}")
                # Продолжаем выполнение, даже если не удалось ответить
            
            # Парсим тариф из callback data
            if not callback.data or ":" not in callback.data:
                logger.error(f"   ❌ Invalid callback data format: '{callback.data}'")
                await callback.message.answer("❌ Ошибка: неверный формат данных. Попробуйте снова.")
                return
            
            tariff_str = callback.data.split(":")[1].strip().lower()
            logger.info(f"   Parsed tariff string: '{tariff_str}'")
            
            try:
                tariff = Tariff(tariff_str)
                logger.info(f"   ✅ Selected tariff: {tariff.value}")
            except ValueError as e:
                logger.error(f"   ❌ Invalid tariff value: '{tariff_str}', error: {e}")
                try:
                    await callback.message.answer(f"❌ Ошибка: неверный тариф '{tariff_str}'. Попробуйте снова.")
                except:
                    pass
                return
            
            # Проверяем, что выбранный тариф доступен
            if tariff not in [Tariff.BASIC, Tariff.FEEDBACK, Tariff.PRACTIC]:
                await callback.message.answer(
                    "❌ Этот тариф временно недоступен.\n\n"
                    "Доступные тарифы:\n"
                    "• 📚 БАЗОВЫЙ - 3000₽\n"
                    "• 💬 С ОБРАТНОЙ СВЯЗЬЮ - 5000₽\n"
                    "• 🎯 PRACTIC - 20000₽\n\n"
                    "Используйте /start для выбора тарифа."
                )
                return
            
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
                description + "\n\n💳 Перейти к оплате?",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="✅ Оплатить",
                            callback_data=f"pay:{tariff.value}"
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="📋 Выбор тарифа",
                            callback_data="back_to_tariffs"
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="❌ Отмена",
                            callback_data="cancel"
                        )
                    ]
                ])
            )
            logger.info(f"   Payment button created with callback_data: pay:{tariff.value}")
        except Exception as e:
            logger.error(f"❌ Error in handle_tariff_selection: {e}", exc_info=True)
            try:
                await callback.answer("❌ Ошибка при выборе тарифа", show_alert=True)
            except:
                # Если не удалось ответить на callback, пробуем отправить сообщение
                try:
                    await callback.message.answer("❌ Ошибка при выборе тарифа. Попробуйте снова.")
                except:
                    pass
    
    async def handle_back_to_tariffs(self, callback: CallbackQuery):
        """Handle back to tariffs button - show tariff selection again."""
        try:
            # Отвечаем на callback сразу
            try:
                await callback.answer()
            except Exception as answer_error:
                logger.warning(f"   Не удалось ответить на callback: {answer_error}")
            
            logger.info(f"📋 Back to tariffs requested by user {callback.from_user.id}")
            
            # Получаем информацию о пользователе для приветствия
            user_id = callback.from_user.id
            first_name = callback.from_user.first_name
            
            # Показываем список тарифов снова
            await self._show_course_info(callback.message, None, first_name)
            
        except Exception as e:
            logger.error(f"❌ Error in handle_back_to_tariffs: {e}", exc_info=True)
            try:
                await callback.answer("❌ Ошибка при загрузке тарифов", show_alert=True)
            except:
                try:
                    await callback.message.answer("❌ Ошибка при загрузке тарифов. Попробуйте позже.")
                except:
                    pass
    
    async def handle_upgrade_tariff(self, callback: CallbackQuery):
        """Handle upgrade tariff button click - show available upgrade options."""
        try:
            # Отвечаем на callback сразу
            try:
                await callback.answer()
            except Exception as answer_error:
                logger.warning(f"   Не удалось ответить на callback: {answer_error}")
            
            logger.info(f"🔄 Upgrade tariff requested by user {callback.from_user.id}")
            
            user_id = callback.from_user.id
            user = await self.user_service.get_user(user_id)
            
            if not user or not user.has_access():
                await callback.message.answer("❌ У вас нет активного доступа к курсу.")
                return
            
            current_tariff = user.tariff
            current_price = PaymentService.TARIFF_PRICES[current_tariff]
            
            # Определяем доступные тарифы для апгрейда
            available_upgrades = []
            if current_tariff == Tariff.BASIC:
                available_upgrades = [
                    (Tariff.FEEDBACK, PaymentService.TARIFF_PRICES[Tariff.FEEDBACK]),
                    (Tariff.PRACTIC, PaymentService.TARIFF_PRICES[Tariff.PRACTIC])
                ]
            elif current_tariff == Tariff.FEEDBACK:
                available_upgrades = [
                    (Tariff.PRACTIC, PaymentService.TARIFF_PRICES[Tariff.PRACTIC])
                ]
            elif current_tariff == Tariff.PRACTIC:
                await callback.message.answer(
                    "✅ У вас уже максимальный доступный тариф!\n\n"
                    "Вы получаете:\n"
                    "• Все материалы курса\n"
                    "• Персональную обратную связь\n"
                    "• 3 онлайн интервью с разбором\n"
                    "• Видеозапись интервью\n"
                    "• Доступ к общему сообществу участников"
                )
                return
            
            if not available_upgrades:
                await callback.message.answer("❌ Нет доступных тарифов для апгрейда.")
                return
            
            # Формируем сообщение с доступными тарифами
            upgrade_text = (
                f"{create_premium_separator()}\n"
                f"🔄 <b>СМЕНА ТАРИФА (АПГРЕЙД)</b>\n"
                f"{create_premium_separator()}\n\n"
                f"Ваш текущий тариф: <b>{current_tariff.value.upper()}</b> ({current_price:.0f}₽)\n\n"
                f"Доступные тарифы для апгрейда:\n\n"
            )
            
            # Создаем клавиатуру с доступными тарифами
            keyboard_buttons = []
            for tariff, price in available_upgrades:
                price_diff = price - current_price
                tariff_name = tariff.value.upper()
                if tariff == Tariff.FEEDBACK:
                    tariff_name = "С ОБРАТНОЙ СВЯЗЬЮ"
                elif tariff == Tariff.PRACTIC:
                    tariff_name = "PRACTIC"
                
                upgrade_text += (
                    f"• <b>{tariff_name}</b> — {price:.0f}₽\n"
                    f"  (доплата: {price_diff:.0f}₽)\n\n"
                )
                
                keyboard_buttons.append([
                    InlineKeyboardButton(
                        text=f"⬆️ {tariff_name} (+{price_diff:.0f}₽)",
                        callback_data=f"upgrade:{tariff.value}"
                    )
                ])
            
            keyboard_buttons.append([
                InlineKeyboardButton(
                    text="❌ Отмена",
                    callback_data="cancel"
                )
            ])
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
            
            await callback.message.edit_text(
                upgrade_text + "Выберите тариф для апгрейда:",
                reply_markup=keyboard
            )
            
        except Exception as e:
            logger.error(f"❌ Error in handle_upgrade_tariff: {e}", exc_info=True)
            try:
                await callback.answer("❌ Ошибка при загрузке тарифов", show_alert=True)
            except:
                try:
                    await callback.message.answer("❌ Ошибка при загрузке тарифов. Попробуйте позже.")
                except:
                    pass
    
    async def handle_upgrade_tariff_selection(self, callback: CallbackQuery):
        """Handle upgrade tariff selection - calculate price difference and initiate payment."""
        try:
            # Отвечаем на callback сразу
            try:
                await callback.answer()
            except Exception as answer_error:
                logger.warning(f"   Не удалось ответить на callback: {answer_error}")
            
            logger.info(f"🔄 Upgrade tariff selection: {callback.data}")
            
            user_id = callback.from_user.id
            user = await self.user_service.get_user(user_id)
            
            if not user or not user.has_access():
                await callback.message.answer("❌ У вас нет активного доступа к курсу.")
                return
            
            # Парсим выбранный тариф
            tariff_str = callback.data.split(":")[1].strip().lower()
            new_tariff = Tariff(tariff_str)
            current_tariff = user.tariff
            
            # Проверяем, что это действительно апгрейд
            tariff_order = {Tariff.BASIC: 1, Tariff.FEEDBACK: 2, Tariff.PRACTIC: 3}
            if new_tariff not in tariff_order or current_tariff not in tariff_order:
                await callback.message.answer(
                    "❌ Этот тариф временно недоступен.\n"
                    "Используйте /start для просмотра доступных тарифов."
                )
                return
            if tariff_order[new_tariff] <= tariff_order[current_tariff]:
                await callback.message.answer(
                    "❌ Вы можете только улучшить тариф, а не понизить его.\n"
                    "Используйте /start для просмотра доступных тарифов."
                )
                return
            
            # Вычисляем разницу в цене
            current_price = PaymentService.TARIFF_PRICES[current_tariff]
            new_price = PaymentService.TARIFF_PRICES[new_tariff]
            price_diff = new_price - current_price
            
            logger.info(f"   Current: {current_tariff.value} ({current_price}₽)")
            logger.info(f"   New: {new_tariff.value} ({new_price}₽)")
            logger.info(f"   Difference: {price_diff}₽")
            
            # Создаем платеж на разницу
            payment_info = await self.payment_service.initiate_payment(
                user_id=user_id,
                tariff=new_tariff,  # Новый тариф
                referral_partner_id=user.referral_partner_id,
                upgrade_from=current_tariff,  # Старый тариф для справки
                upgrade_price=price_diff  # Цена апгрейда
            )
            
            payment_id = payment_info["payment_id"]
            payment_url = payment_info["payment_url"]
            
            # Форматируем цену с валютой
            currency_symbol = "₽" if Config.PAYMENT_CURRENCY == "RUB" else Config.PAYMENT_CURRENCY
            
            payment_note = ""
            if Config.PAYMENT_PROVIDER.lower() == "mock":
                payment_note = "\n\n<i>Примечание: Это тестовая система оплаты. Платеж автоматически завершится через 5 секунд.</i>\n\nЧерез 5 секунд нажмите кнопку 'Проверить статус оплаты'."
            else:
                payment_note = "\n\n<i>После оплаты нажмите кнопку 'Проверить статус оплаты' для подтверждения.</i>"
            
            upgrade_message = (
                f"{create_premium_separator()}\n"
                f"💳 <b>ОПЛАТА АПГРЕЙДА ТАРИФА</b>\n"
                f"{create_premium_separator()}\n\n"
                f"Текущий тариф: <b>{current_tariff.value.upper()}</b> ({current_price:.0f}₽)\n"
                f"Новый тариф: <b>{new_tariff.value.upper()}</b> ({new_price:.0f}₽)\n\n"
                f"💰 К доплате: <b>{price_diff:.0f}{currency_symbol}</b>{payment_note}"
            )
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="💳 Оплатить",
                        url=payment_url
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🔄 Проверить статус оплаты",
                        callback_data=f"check_payment:{payment_id}"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="❌ Отмена",
                        callback_data="cancel"
                    )
                ]
            ])
            
            await callback.message.edit_text(upgrade_message, reply_markup=keyboard)
            
        except Exception as e:
            logger.error(f"❌ Error in handle_upgrade_tariff_selection: {e}", exc_info=True)
            try:
                await callback.answer("❌ Ошибка при создании платежа", show_alert=True)
            except:
                try:
                    await callback.message.answer("❌ Ошибка при создании платежа. Попробуйте позже.")
                except:
                    pass
    
    async def handle_payment_initiate(self, callback: CallbackQuery):
        """Handle payment initiation."""
        # ЛОГИРОВАНИЕ В САМОМ НАЧАЛЕ
        logger.info("=" * 60)
        logger.info("✅✅✅ HANDLE_PAYMENT_INITIATE ВЫЗВАН! ✅✅✅")
        logger.info(f"   Callback data: {callback.data}")
        logger.info(f"   User ID: {callback.from_user.id}")
        logger.info(f"   Username: @{callback.from_user.username}")
        logger.info("=" * 60)
        
        try:
            # Отвечаем на callback сразу
            try:
                await callback.answer()
            except Exception as answer_error:
                logger.warning(f"   Не удалось ответить на callback: {answer_error}")
            
            logger.info(f"💳 Payment initiation requested by user {callback.from_user.id}")
            
            tariff_str = callback.data.split(":")[1]
            tariff = Tariff(tariff_str)
            
            user_id = callback.from_user.id
            user = await self.user_service.get_or_create_user(
                user_id,
                callback.from_user.username,
                callback.from_user.first_name,
                callback.from_user.last_name
            )
            
            logger.info(f"   Tariff: {tariff.value}, User: {user_id}")
            
            # Initiate payment
            payment_info = await self.payment_service.initiate_payment(
                user_id=user_id,
                tariff=tariff,
                referral_partner_id=user.referral_partner_id
            )
            
            payment_id = payment_info["payment_id"]
            payment_url = payment_info["payment_url"]
            
            logger.info(f"   Payment created: {payment_id}")
            
            # Show payment information
            payment_note = ""
            if Config.PAYMENT_PROVIDER.lower() == "mock":
                payment_note = "\n\n<i>Примечание: Это тестовая система оплаты. Платеж автоматически завершится через 5 секунд.</i>\n\nЧерез 5 секунд нажмите кнопку 'Проверить статус оплаты'."
            else:
                payment_note = "\n\n<i>После оплаты нажмите кнопку 'Проверить статус оплаты' для подтверждения.</i>"
            
            # Форматируем цену с валютой
            price = PaymentService.TARIFF_PRICES[tariff]
            currency_symbol = "₽" if Config.PAYMENT_CURRENCY == "RUB" else Config.PAYMENT_CURRENCY
            
            await callback.message.edit_text(
                f"💳 <b>Требуется оплата</b>\n\n"
                f"Тариф: <b>{tariff.value.upper()}</b>\n"
                f"Сумма: {price:.0f}{currency_symbol}\n\n"
                f"Нажмите кнопку ниже для завершения оплаты:{payment_note}",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="💳 Оплатить",
                            url=payment_url
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="🔄 Проверить статус оплаты",
                            callback_data=f"check_payment:{payment_id}"
                        )
                    ]
                ])
            )
            
            logger.info(f"   Payment message sent to user")
        except Exception as e:
            logger.error(f"❌ Error in handle_payment_initiate: {e}", exc_info=True)
            await callback.message.edit_text("❌ Ошибка при создании платежа. Попробуйте позже.")
        
        # In production, you might want to:
        # 1. Poll payment status in background
        # 2. Set up webhook handler for payment notifications
        # 3. Automatically check and grant access when payment completes
    
    async def handle_cancel(self, callback: CallbackQuery):
        """Handle cancel action."""
        try:
            await callback.answer("Отменено")
        except:
            pass
        try:
            await callback.message.edit_text("Оплата отменена. Используйте /start для начала заново.")
        except:
            try:
                await callback.message.answer("Оплата отменена. Используйте /start для начала заново.")
            except:
                pass
    
    async def handle_talk_to_human(self, callback: CallbackQuery):
        """Handle 'Talk to human' button - send question to curator group."""
        try:
            await callback.answer()
        except:
            pass
        
        user_id = callback.from_user.id
        first_name = callback.from_user.first_name or "Пользователь"
        username = callback.from_user.username
        
        # Сохраняем контекст, что пользователь хочет поговорить с человеком
        if not hasattr(self, '_user_question_context'):
            self._user_question_context = {}
        self._user_question_context[user_id] = {
            'waiting_for_question': True,
            'source': 'sales_bot'
        }
        
        await callback.message.answer(
            f"💬 <b>Поговорить с человеком</b>\n\n"
            f"👋 Привет, {first_name}!\n\n"
            f"✍️ Напишите ваш вопрос прямо здесь 👇\n\n"
            f"📤 Ваш вопрос будет отправлен куратору, и мы ответим вам как можно скорее ⚡\n\n"
            f"💡 <i>Можете задать любой вопрос о курсе, тарифах или оплате.</i>"
        )
    
    async def handle_about_course(self, callback: CallbackQuery):
        """Handle 'About course' button - show course description."""
        try:
            await callback.answer()
        except:
            pass
        
        # Показываем описание курса (используем ту же логику, что и в _show_course_info)
        await send_typing_action(self.bot, callback.message.chat.id, 0.5)
        
        course_description = (
            f"{create_premium_separator()}\n"
            f"✨ <b>ВОПРОСЫ, КОТОРЫЕ МЕНЯЮТ ВСЁ</b> ✨\n"
            f"{create_premium_separator()}\n\n"
            f"📱 <b>Телеграм-практикум</b>\n\n"
            f"💭 <b>Знакомо ли вам, когда...</b>\n\n"
            f"• Собеседник отвечает односложно, а вы не знаете, как разговорить?\n"
            f"• На мероприятии хочется подойти к интересному человеку, но не знаете, с чего начать?\n"
            f"• Коллеги и клиенты не раскрывают свой настоящий потенциал в общении с вами?\n"
            f"• Хочется строить глубокие связи, но вместо этого — только поверхностные контакты?\n\n"
            f"{create_premium_separator()}\n\n"
            f"🎯 <b>Что если через 30 дней вы сможете:</b>\n\n"
            f"✨ С первого вопроса создавать атмосферу доверия, где люди сами хотят раскрываться\n\n"
            f"✨ Превращать случайные знакомства в ценные связи для бизнеса и жизни\n\n"
            f"✨ Находить подход к любому человеку — от замкнутого подростка до важного клиента\n\n"
            f"✨ Строить личный бренд через искреннюю коммуникацию, привлекающую нужных людей\n\n"
            f"{create_premium_separator()}\n\n"
            f"💎 <b>Что делает этот практикум особенным:</b>\n\n"
            f"🎯 <b>Не теория, а пошаговая инструкция</b> — конкретные инструменты для раскрытия собеседника, которые работают сразу\n\n"
            f"🎯 <b>Система нетворкинга</b> — учитесь выстраивать связи, которые приведут к новым возможностям и проектам\n\n"
            f"🎯 <b>Практика с обратной связью</b> — применяете знания сразу, получаете фидбек и корректируете подход\n\n"
            f"🎯 <b>Среда единомышленников</b> — находите партнеров, клиентов и друзей среди участников\n\n"
            f"{create_premium_separator()}\n\n"
            f"👥 <b>Кому это необходимо:</b>\n\n"
            f"💼 <b>Бизнесмену</b> — чтобы улучшить навыки нетворкинга\n"
            f"👔 <b>Руководителю</b> — чтобы быстро и детально распаковывать людей и информацию\n"
            f"💼 <b>Продажнику</b> — чтобы отточить искусство диалога и продавать больше\n"
            f"📚 <b>Преподавателю</b> — чтобы использовать вопросы как инструмент для достижения лучших результатов\n"
            f"📱 <b>Блогеру и журналисту</b> — чтобы начать вести интервью\n"
            f"🚀 <b>Профессионалу</b> — чтобы активнее расти и развиваться через правильные вопросы\n"
            f"💫 <b>Любому человеку</b> — желающему сделать свои диалоги, а значит и жизнь, более насыщенными и интересными\n\n"
            f"{create_premium_separator()}\n\n"
            f"📅 <b>Как это будет проходить:</b>\n\n"
            f"🔹 <b>Закрытая группа в Telegram</b> — уютное пространство для роста\n"
            f"🔹 <b>Ежедневные посты</b> — краткая теория + практическое задание\n"
            f"🔹 <b>Короткие задания на 5-10 минут</b> — легко встроить в любой график\n"
            f"🔹 <b>Ответы от мастера</b> — персональные комментарии к вашим вопросам и работам\n"
            f"🔹 <b>Поддержка сообщества</b> — обмен опытом с единомышленниками\n\n"
            f"{create_premium_separator()}\n\n"
            f"👨‍🏫 <b>Об авторе:</b>\n\n"
            f"<b>Артём Никитин</b> — журналист, телеведущий, диктор, кинорежиссёр, музыкант, поэт.\n"
            f"Провёл <b>3000+ интервью</b> с выдающимися людьми.\n"
            f"Разрабатываю идеи, создаю текстовый, аудио- и видеоконтент с 2000 года.\n\n"
            f"🌐 <a href='https://sites.google.com/view/nikitinartem'>Официальный сайт Артёма Никитина</a>\n\n"
            f"{create_premium_separator()}\n\n"
            f"💎 <b>Это инвестиция в ваш главный актив — умение выстраивать качественные связи.</b>"
        )
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💎 Выбрать тариф",
                    callback_data="back_to_tariffs"
                )
            ]
        ])
        
        await callback.message.answer(course_description, reply_markup=keyboard, disable_web_page_preview=False)
    
    async def handle_keyboard_upgrade(self, message: Message):
        """Handle 'Апгрейд тарифа' button from persistent keyboard."""
        user_id = message.from_user.id
        user = await self.user_service.get_user(user_id)
        
        if not user or not user.has_access():
            await message.answer("❌ У вас нет активного доступа к курсу.\n\nДля обновления тарифа сначала необходимо приобрести доступ к курсу.")
            return
        
        # Используем логику из handle_upgrade_tariff
        current_tariff = user.tariff
        current_price = PaymentService.TARIFF_PRICES[current_tariff]
        
        # Определяем доступные тарифы для апгрейда
        available_upgrades = []
        if current_tariff == Tariff.BASIC:
            available_upgrades = [
                (Tariff.FEEDBACK, PaymentService.TARIFF_PRICES[Tariff.FEEDBACK]),
                (Tariff.PRACTIC, PaymentService.TARIFF_PRICES[Tariff.PRACTIC])
            ]
        elif current_tariff == Tariff.FEEDBACK:
            available_upgrades = [
                (Tariff.PRACTIC, PaymentService.TARIFF_PRICES[Tariff.PRACTIC])
            ]
        elif current_tariff == Tariff.PRACTIC:
            await message.answer(
                "✅ У вас уже максимальный доступный тариф!\n\n"
                "Вы получаете:\n"
                "• Все материалы курса\n"
                "• Персональную обратную связь\n"
                "• 3 онлайн интервью с разбором\n"
                "• Видеозапись интервью\n"
                "• Доступ к общему сообществу участников"
            )
            return
        
        if not available_upgrades:
            await message.answer("❌ Нет доступных тарифов для апгрейда.")
            return
        
        # Формируем сообщение с доступными тарифами
        upgrade_text = (
            f"{create_premium_separator()}\n"
            f"🔄 <b>СМЕНА ТАРИФА (АПГРЕЙД)</b>\n"
            f"{create_premium_separator()}\n\n"
            f"Ваш текущий тариф: <b>{current_tariff.value.upper()}</b> ({current_price:.0f}₽)\n\n"
            f"Доступные тарифы для апгрейда:\n\n"
        )
        
        # Создаем клавиатуру с доступными тарифами
        keyboard_buttons = []
        for tariff, price in available_upgrades:
            price_diff = price - current_price
            tariff_name = tariff.value.upper()
            if tariff == Tariff.FEEDBACK:
                tariff_name = "С ОБРАТНОЙ СВЯЗЬЮ"
            elif tariff == Tariff.PRACTIC:
                tariff_name = "PRACTIC"
            
            upgrade_text += (
                f"• <b>{tariff_name}</b> — {price:.0f}₽\n"
                f"  (доплата: {price_diff:.0f}₽)\n\n"
            )
            
            keyboard_buttons.append([
                InlineKeyboardButton(
                    text=f"⬆️ {tariff_name} (+{price_diff:.0f}₽)",
                    callback_data=f"upgrade:{tariff.value}"
                )
            ])
        
        keyboard_buttons.append([
            InlineKeyboardButton(
                text="❌ Отмена",
                callback_data="cancel"
            )
        ])
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
        
        await message.answer(upgrade_text + "Выберите тариф для апгрейда:", reply_markup=keyboard)
    
    async def handle_keyboard_go_to_course(self, message: Message):
        """Handle 'Перейти в курс' button from persistent keyboard."""
        user_id = message.from_user.id
        user = await self.user_service.get_user(user_id)
        
        if not user or not user.has_access():
            await message.answer(
                "❌ У вас нет активного доступа к курсу.\n\n"
                "Используйте кнопку '📋 Выбор тарифа' для приобретения доступа."
            )
            return
        
        await message.answer(
            "🚀 <b>Переход в курс</b>\n\n"
            "Нажмите на ссылку ниже, чтобы перейти в курс-бот:\n\n"
            "🤖 <a href='https://t.me/StartNowAI_bot?start=course'>@StartNowAI_bot</a>",
            disable_web_page_preview=False
        )
    
    async def handle_keyboard_select_tariff(self, message: Message):
        """Handle 'Выбор тарифа' button from persistent keyboard - show only tariff descriptions."""
        # Показываем только описание тарифов
        tariff_message = (
            f"{create_premium_separator()}\n"
            f"💎 <b>ВЫБОР ТАРИФА</b>\n"
            f"{create_premium_separator()}\n\n"
            f"Выберите тариф, который подходит вам:\n\n"
        )
        
        # Добавляем описание каждого тарифа
        tariff_message += format_tariff_description(Tariff.BASIC) + "\n\n"
        tariff_message += format_tariff_description(Tariff.FEEDBACK) + "\n\n"
        tariff_message += format_tariff_description(Tariff.PRACTIC) + "\n\n"
        
        tariff_message += (
            f"{create_premium_separator()}\n\n"
            f"💳 <b>Выберите тариф для оплаты:</b>"
        )
        
        keyboard = create_tariff_keyboard()
        await message.answer(tariff_message, reply_markup=keyboard)
    
    async def handle_keyboard_about_course(self, message: Message):
        """Handle 'О курсе' button from persistent keyboard."""
        # Используем логику из handle_about_course
        await send_typing_action(self.bot, message.chat.id, 0.5)
        
        course_description = (
            f"{create_premium_separator()}\n"
            f"✨ <b>ВОПРОСЫ, КОТОРЫЕ МЕНЯЮТ ВСЁ</b> ✨\n"
            f"{create_premium_separator()}\n\n"
            f"📱 <b>Телеграм-практикум</b>\n\n"
            f"💭 <b>Знакомо ли вам, когда...</b>\n\n"
            f"• Собеседник отвечает односложно, а вы не знаете, как разговорить?\n"
            f"• На мероприятии хочется подойти к интересному человеку, но не знаете, с чего начать?\n"
            f"• Коллеги и клиенты не раскрывают свой настоящий потенциал в общении с вами?\n"
            f"• Хочется строить глубокие связи, но вместо этого — только поверхностные контакты?\n\n"
            f"{create_premium_separator()}\n\n"
            f"🎯 <b>Что если через 30 дней вы сможете:</b>\n\n"
            f"✨ С первого вопроса создавать атмосферу доверия, где люди сами хотят раскрываться\n\n"
            f"✨ Превращать случайные знакомства в ценные связи для бизнеса и жизни\n\n"
            f"✨ Находить подход к любому человеку — от замкнутого подростка до важного клиента\n\n"
            f"✨ Строить личный бренд через искреннюю коммуникацию, привлекающую нужных людей\n\n"
            f"{create_premium_separator()}\n\n"
            f"💎 <b>Что делает этот практикум особенным:</b>\n\n"
            f"🎯 <b>Не теория, а пошаговая инструкция</b> — конкретные инструменты для раскрытия собеседника, которые работают сразу\n\n"
            f"🎯 <b>Система нетворкинга</b> — учитесь выстраивать связи, которые приведут к новым возможностям и проектам\n\n"
            f"🎯 <b>Практика с обратной связью</b> — применяете знания сразу, получаете фидбек и корректируете подход\n\n"
            f"🎯 <b>Среда единомышленников</b> — находите партнеров, клиентов и друзей среди участников\n\n"
            f"{create_premium_separator()}\n\n"
            f"👥 <b>Кому это необходимо:</b>\n\n"
            f"💼 <b>Бизнесмену</b> — чтобы улучшить навыки нетворкинга\n"
            f"👔 <b>Руководителю</b> — чтобы быстро и детально распаковывать людей и информацию\n"
            f"💼 <b>Продажнику</b> — чтобы отточить искусство диалога и продавать больше\n"
            f"📚 <b>Преподавателю</b> — чтобы использовать вопросы как инструмент для достижения лучших результатов\n"
            f"📱 <b>Блогеру и журналисту</b> — чтобы начать вести интервью\n"
            f"🚀 <b>Профессионалу</b> — чтобы активнее расти и развиваться через правильные вопросы\n"
            f"💫 <b>Любому человеку</b> — желающему сделать свои диалоги, а значит и жизнь, более насыщенными и интересными\n\n"
            f"{create_premium_separator()}\n\n"
            f"📅 <b>Как это будет проходить:</b>\n\n"
            f"🔹 <b>Закрытая группа в Telegram</b> — уютное пространство для роста\n"
            f"🔹 <b>Ежедневные посты</b> — краткая теория + практическое задание\n"
            f"🔹 <b>Короткие задания на 5-10 минут</b> — легко встроить в любой график\n"
            f"🔹 <b>Ответы от мастера</b> — персональные комментарии к вашим вопросам и работам\n"
            f"🔹 <b>Поддержка сообщества</b> — обмен опытом с единомышленниками\n\n"
            f"{create_premium_separator()}\n\n"
            f"👨‍🏫 <b>Об авторе:</b>\n\n"
            f"<b>Артём Никитин</b> — журналист, телеведущий, диктор, кинорежиссёр, музыкант, поэт.\n"
            f"Провёл <b>3000+ интервью</b> с выдающимися людьми.\n"
            f"Разрабатываю идеи, создаю текстовый, аудио- и видеоконтент с 2000 года.\n\n"
            f"🌐 <a href='https://sites.google.com/view/nikitinartem'>Официальный сайт Артёма Никитина</a>\n\n"
            f"{create_premium_separator()}\n\n"
            f"💎 <b>Это инвестиция в ваш главный актив — умение выстраивать качественные связи.</b>"
        )
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💎 Выбрать тариф",
                    callback_data="back_to_tariffs"
                )
            ]
        ])
        
        await message.answer(course_description, reply_markup=keyboard, disable_web_page_preview=False)
    
    async def handle_question_from_sales(self, message: Message):
        """Handle question text from sales bot (when user clicked 'Talk to human')."""
        user_id = message.from_user.id
        
        # Проверяем, ожидаем ли мы вопрос от этого пользователя
        if not hasattr(self, '_user_question_context') or user_id not in self._user_question_context:
            # Не ожидаем вопрос, пропускаем
            return
        
        context = self._user_question_context[user_id]
        if not context.get('waiting_for_question'):
            return
        
        # Удаляем контекст после обработки
        del self._user_question_context[user_id]
        
        # Создаем вопрос
        question_data = await self.question_service.create_question(
            user_id=user_id,
            lesson_id=None,
            question_text=message.text,
            context="Вопрос из бота оплаты (sales bot)"
        )
        
        # Форматируем вопрос для кураторов
        curator_message = await self.question_service.format_question_for_admin(question_data)
        curator_message += "\n\n📍 <b>Источник:</b> Бот оплаты (sales bot)"
        
        # Отправляем в группу кураторов (если настроена), иначе в админ-чат
        target_chat_id = Config.CURATOR_GROUP_ID if Config.CURATOR_GROUP_ID else Config.ADMIN_CHAT_ID
        
        if target_chat_id:
            try:
                # Отправляем вопрос в группу кураторов с кнопкой для ответа
                await self.bot.send_message(
                    target_chat_id,
                    curator_message,
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="💬 Ответить",
                                callback_data=f"curator_reply:{user_id}:0"
                            )
                        ]
                    ])
                )
                logger.info(f"✅ Question from sales bot sent to curator group from user {user_id}")
            except Exception as e:
                logger.error(f"❌ Error sending question to curator group: {e}")
                # Fallback: отправляем в админ-чат
                if Config.ADMIN_CHAT_ID:
                    await self.bot.send_message(
                        Config.ADMIN_CHAT_ID,
                        curator_message,
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                            [
                                InlineKeyboardButton(
                                    text="💬 Ответить",
                                    callback_data=f"curator_reply:{user_id}:0"
                                )
                            ]
                        ])
                    )
        else:
            logger.warning("⚠️ No curator group or admin chat configured!")
        
        await message.answer(
            "✅ <b>Вопрос отправлен!</b>\n\n"
            "📤 Ваш вопрос отправлен куратору 👥.\n"
            "⏳ Мы ответим вам как можно скорее 💬.\n\n"
            "💎 <i>Пока ждёте ответа, можете выбрать тариф и начать обучение!</i>"
        )
    
    async def handle_payment_check(self, callback: CallbackQuery):
        """Handle payment status check callback."""
        try:
            # Отвечаем на callback сразу
            try:
                await callback.answer()
            except Exception as answer_error:
                logger.warning(f"   Не удалось ответить на callback: {answer_error}")
            
            payment_id = callback.data.split(":")[1]
            logger.info(f"🔄 Checking payment status: {payment_id}")
            
            status = await self.payment_service.check_payment(payment_id)
            logger.info(f"   Payment status: {status.value}")
            
            if status == PaymentStatus.COMPLETED:
                logger.info(f"   Payment completed! Processing access...")
                # Process payment completion
                result = await self.payment_service.process_payment_completion(payment_id)
                
                if result:
                    logger.info(f"   Access granted/upgraded to user {result['user_id']}")
                    user = result["user"]
                    is_upgrade = result.get("is_upgrade", False)
                    await self._grant_access_and_notify(callback.message, user, is_upgrade=is_upgrade)
                else:
                    logger.error(f"   Failed to process payment completion for {payment_id}")
                    await callback.message.edit_text(
                        "❌ Оплата завершена, но произошла ошибка при предоставлении доступа. "
                        "Пожалуйста, обратитесь в поддержку."
                    )
            elif status == PaymentStatus.PENDING:
                await callback.message.edit_text(
                    f"⏳ Статус оплаты: <b>{status.value}</b>\n\n"
                    "Пожалуйста, подождите подтверждения оплаты...\n\n"
                    "Тестовая оплата автоматически завершится через 5 секунд.\n\n"
                    "Нажмите 'Проверить статус оплаты' снова через 5 секунд.",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="🔄 Проверить статус оплаты снова",
                                callback_data=f"check_payment:{payment_id}"
                            )
                        ]
                    ])
                )
            else:
                await callback.message.edit_text(
                    f"❌ Статус оплаты: <b>{status.value}</b>\n\n"
                    "Пожалуйста, попробуйте снова или обратитесь в поддержку."
                )
        except Exception as e:
            logger.error(f"❌ Error in handle_payment_check: {e}", exc_info=True)
            await callback.message.edit_text("❌ Ошибка при проверке платежа. Попробуйте позже.")
    
    async def _grant_access_and_notify(self, message: Message, user, is_upgrade: bool = False):
        """
        Grant access to course and send onboarding message.
        
        This is called after successful payment to:
        1. Send onboarding message
        2. Invite user to course bot
        3. Invite user to appropriate groups
        
        Args:
            message: Message object to reply to
            user: User object
            is_upgrade: True if this is a tariff upgrade, False if new access
        """
        # Send premium onboarding message
        if is_upgrade:
            onboarding_text = (
                f"{create_success_animation()}\n\n"
                f"{create_premium_separator()}\n"
                f"✨ <b>ТАРИФ ОБНОВЛЁН!</b> ✨\n"
                f"{create_premium_separator()}\n\n"
                f"🎉 <b>Поздравляем, {user.first_name}!</b>\n\n"
                f"✅ Ваш тариф успешно обновлён!\n"
                f"📦 <b>Новый тариф:</b> <b>{user.tariff.value.upper()}</b>\n\n"
                f"{create_premium_separator()}\n\n"
                f"💎 Теперь у вас есть доступ ко всем возможностям нового тарифа!\n\n"
                f"🤖 <b>Продолжайте обучение:</b> @StartNowAI_bot"
            )
        else:
            onboarding_text = (
                f"{create_success_animation()}\n\n"
                f"{create_premium_separator()}\n"
                f"🎊 <b>ДОБРО ПОЖАЛОВАТЬ В КУРС!</b> 🎊\n"
                f"{create_premium_separator()}\n\n"
                f"🎉 <b>Поздравляем, {user.first_name}!</b>\n\n"
                f"✅ Ваша оплата подтверждена\n"
                f"📦 <b>Тариф:</b> <b>{user.tariff.value.upper()}</b>\n\n"
                f"{create_premium_separator()}\n\n"
                f"📚 <b>Сегодня — День 1 вашего путешествия!</b>\n\n"
                f"🚀 Нажмите кнопку ниже, чтобы начать курс 👇"
            )
        
        # Кнопка для перехода в курс-бот (только для новых пользователей)
        if not is_upgrade:
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
        else:
            await message.answer(onboarding_text)
        
        # Get groups user should have access to
        groups = self.community_service.get_groups_for_user(user)
        
        if groups:
            group_text = "🔗 <b>Присоединяйтесь к сообществам:</b>\n\n"
            for group_id in groups:
                invite_link = self.community_service.get_group_invite_link(group_id)
                group_text += f"• <a href='{invite_link}'>Группа сообщества</a>\n"
            
            await message.answer(group_text, disable_web_page_preview=True)
        
        # Информация об авторе курса
        author_info = (
            "\n👨‍🏫 <b>Об авторе курса:</b>\n"
            "Курс ведёт <b>Артём Никитин</b> — журналист, телеведущий, кинорежиссёр.\n"
            "Опыт проведения 3000+ интервью с выдающимися людьми.\n\n"
            "🌐 <a href='https://sites.google.com/view/nikitinartem'>Официальный сайт Артёма Никитина</a>"
        )
        await message.answer(author_info, disable_web_page_preview=False)
        
        # Send lesson 0 immediately via course bot
        if not is_upgrade:
            if self.lesson_loader:
                try:
                    await self._send_lesson_0_to_user(user.user_id)
                except Exception as e:
                    logger.error(f"Error sending lesson 0 to user {user.user_id}: {e}", exc_info=True)
            else:
                logger.warning(f"LessonLoader not available, skipping lesson 0 for user {user.user_id}")
        
        # Note: In production, you would:
        # 1. Use bot API to actually invite user to groups
    
    async def _send_lesson_0_to_user(self, user_id: int):
        """
        Send lesson 0 to user immediately after subscription purchase.
        
        Args:
            user_id: Telegram user ID
        """
        if not self.lesson_loader:
            logger.warning(f"LessonLoader not available, cannot send lesson 0 to user {user_id}")
            return
        
        try:
            # Import course bot components
            from aiogram import Bot
            from aiogram.client.default import DefaultBotProperties
            from aiogram.enums import ParseMode
            from utils.premium_ui import send_typing_action, create_premium_separator
            
            # Create course bot instance for sending lesson
            course_bot = Bot(token=Config.COURSE_BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
            
            # Get lesson 0 data
            lesson_data = self.lesson_loader.get_lesson(0)
            if not lesson_data:
                logger.warning(f"Lesson 0 not found for user {user_id}")
                await course_bot.session.close()
                return
            
            # Get user from database
            user = await self.user_service.get_user(user_id)
            if not user:
                logger.error(f"User {user_id} not found")
                await course_bot.session.close()
                return
            
            # Send lesson 0 using the same method as course bot
            await send_typing_action(course_bot, user_id, 0.8)
            
            # Send intro text if exists
            intro_text = lesson_data.get("intro_text", "")
            if intro_text:
                intro_message = f"{intro_text}\n\n{create_premium_separator()}\n\n"
                await course_bot.send_message(user_id, intro_message)
                await asyncio.sleep(0.5)
            
            # Send video if exists (lesson 0 has video with intro_text in caption)
            media = lesson_data.get("media", [])
            lesson0_video_media = None
            for item in media:
                if item.get("type") == "video" and item.get("file_id"):
                    lesson0_video_media = item
                    break
            
            if lesson0_video_media:
                video_file_id = lesson0_video_media.get("file_id")
                video_caption = intro_text if intro_text else None
                
                await course_bot.send_video(
                    user_id,
                    video_file_id,
                    caption=video_caption,
                    parse_mode="HTML"
                )
                await asyncio.sleep(0.5)
            
            # Send main text
            main_text = lesson_data.get("text", "")
            if main_text:
                await course_bot.send_message(user_id, main_text, parse_mode="HTML")
                await asyncio.sleep(0.5)
            
            # Send persistent keyboard
            from utils.telegram_helpers import create_persistent_keyboard
            persistent_keyboard = create_persistent_keyboard()
            await course_bot.send_message(user_id, "\u200B", reply_markup=persistent_keyboard)
            
            logger.info(f"✅ Lesson 0 sent to user {user_id}")
            
            # Close bot session
            await course_bot.session.close()
            
        except Exception as e:
            logger.error(f"Error in _send_lesson_0_to_user for user {user_id}: {e}", exc_info=True)
            # Try to close bot session even on error
            try:
                if 'course_bot' in locals():
                    await course_bot.session.close()
            except:
                pass
            raise
    
    async def start(self):
        """Start the bot."""
        try:
            logger.info("Connecting to database...")
            await self.db.connect()
            logger.info("✅ Database connected")
            
            logger.info("Starting Sales Bot...")
            me = await self.bot.get_me()
            logger.info(f"✅ Bot connected: @{me.username} ({me.first_name})")
            logger.info(f"✅ Bot ID: {me.id}")
            
            # Проверка регистрации обработчиков
            logger.info("")
            logger.info("=" * 60)
            logger.info("РЕГИСТРАЦИЯ ОБРАБОТЧИКОВ:")
            logger.info(f"   Message handlers: {len(self.dp.message.handlers)}")
            for i, handler in enumerate(self.dp.message.handlers):
                callback_name = handler.callback.__name__ if hasattr(handler, 'callback') else 'unknown'
                logger.info(f"   [{i+1}] {callback_name}")
            logger.info(f"   Callback handlers: {len(self.dp.callback_query.handlers)}")
            logger.info("=" * 60)
            logger.info("")
            
            logger.info("✅ Sales Bot started")
            logger.info("✅ Bot is ready to receive messages")
            logger.info("")
            logger.info("=" * 60)
            logger.info("ОТПРАВЬТЕ /start В TELEGRAM: t.me/StartNowQ_bot")
            logger.info("=" * 60)
            logger.info("")
            
            await self.dp.start_polling(self.bot, skip_updates=True)
        except Exception as e:
            logger.error(f"❌ Error starting bot: {e}", exc_info=True)
            raise
    
    async def stop(self):
        """Stop the bot."""
        await self.db.close()
        await self.bot.session.close()


async def main():
    """Main entry point."""
    if not Config.validate():
        logger.error("❌ Неверная конфигурация. Проверьте файл .env")
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
    # Локальный запуск отключен. Используйте run_all_bots.py для запуска ботов.
    # Это предотвращает конфликты getUpdates при одновременном запуске на Railway и локально.
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except (AttributeError, ValueError):
        pass  # Python < 3.7 или уже настроено
    
    print("=" * 60)
    print("WARNING: LOCAL STARTUP DISABLED")
    print("=" * 60)
    print("Bots must be started via run_all_bots.py")
    print("This prevents getUpdates conflicts when running on Railway.")
    print("")
    print("To start bots, use:")
    print("  python run_all_bots.py")
    print("=" * 60)
    sys.exit(1)

