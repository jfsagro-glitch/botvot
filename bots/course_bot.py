"""
Course Delivery Bot

Handles:
- Automated daily lesson delivery
- Assignment submissions
- Question handling
- Feedback delivery
- Lesson navigation
"""

import asyncio
import logging
import sys
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

from core.config import Config
from core.database import Database
from core.models import User, Tariff
from services.user_service import UserService
from services.lesson_service import LessonService
from services.lesson_loader import LessonLoader
from services.assignment_service import AssignmentService
from services.community_service import CommunityService
from services.question_service import QuestionService
from utils.telegram_helpers import create_lesson_keyboard, format_lesson_message, create_lesson_keyboard_from_json, create_upgrade_tariff_keyboard
from utils.scheduler import LessonScheduler
from utils.mentor_scheduler import MentorReminderScheduler
from utils.premium_ui import send_typing_action, create_premium_separator
from utils.navigator import create_navigator_keyboard, format_navigator_message

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class CourseBot:
    """Course Delivery Bot implementation."""
    
    def __init__(self):
        self.bot = Bot(token=Config.COURSE_BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        self.dp = Dispatcher()
        self.db = Database()
        self.user_service = UserService(self.db)
        self.lesson_service = LessonService(self.db)
        self.lesson_loader = LessonLoader()  # Загрузчик уроков из JSON
        self.assignment_service = AssignmentService(self.db)
        self.community_service = CommunityService()
        self.question_service = QuestionService(self.db)
        self.scheduler = None
        self.mentor_scheduler = None
        
        # Проверяем, что уроки загружены
        if self.lesson_loader:
            # Принудительно перезагружаем уроки при старте, чтобы убедиться, что используется актуальная версия
            self.lesson_loader.reload()
            lesson_count = self.lesson_loader.get_lesson_count()
            logger.info(f"✅ LessonLoader initialized with {lesson_count} lessons")
            if lesson_count == 0:
                logger.warning("⚠️ No lessons loaded! Check data/lessons.json")
        else:
            logger.error("❌ LessonLoader failed to initialize!")
        
        # Register handlers
        self._register_handlers()
    
    def _create_persistent_keyboard(self) -> ReplyKeyboardMarkup:
        """Create persistent keyboard for course bot with main buttons."""
        keyboard = ReplyKeyboardMarkup(
            keyboard=[
                [
                    KeyboardButton(text="🧭"),
                    KeyboardButton(text="❓"),
                    KeyboardButton(text="💎"),
                    KeyboardButton(text="💬"),
                    KeyboardButton(text="👨‍🏫")
                ]
            ],
            resize_keyboard=True,
            persistent=True
        )
        return keyboard
    
    def _create_cards_keyboard(self, cards: list) -> InlineKeyboardMarkup:
        """Create inline keyboard with card buttons for lesson 21."""
        buttons = []
        row = []
        
        logger.info(f"   🔍 Creating keyboard for {len(cards)} cards")
        
        # Создаем кнопки для каждой карточки (6 кнопок в ряд, 3 ряда)
        for card in cards:
            card_number = card.get("number", 0)
            if card_number == 0:
                logger.warning(f"   ⚠️ Card with invalid number: {card}")
                continue
            row.append(InlineKeyboardButton(
                text=f"🎴 {card_number}",
                callback_data=f"lesson21_card:{card_number}"
            ))
            
            # По 6 кнопок в ряд
            if len(row) == 6:
                buttons.append(row)
                row = []
        
        # Добавляем оставшиеся кнопки
        if row:
            buttons.append(row)
        
        # Добавляем кнопку "Рандом" в отдельный ряд
        buttons.append([InlineKeyboardButton(
            text="🎲 Рандом",
            callback_data="lesson21_card:random"
        )])
        
        logger.info(f"   🔍 Created keyboard with {len(buttons)} rows")
        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
        return keyboard
    
    async def _ensure_persistent_keyboard(self, user_id: int):
        """Ensure persistent keyboard is always visible by sending it if needed."""
        try:
            persistent_keyboard = self._create_persistent_keyboard()
            # Используем невидимый символ вместо пустого сообщения
            await self.bot.send_message(user_id, "\u200B", reply_markup=persistent_keyboard)
        except Exception as e:
            logger.debug(f"Could not send persistent keyboard to {user_id}: {e}")
    
    async def _send_video_with_retry(self, user_id: int, video, caption: str = None, 
                                     width: int = None, height: int = None, 
                                     supports_streaming: bool = True, max_retries: int = 3):
        """
        Отправляет видео с повторными попытками и оптимизированными параметрами.
        
        Args:
            user_id: ID пользователя
            video: file_id или FSInputFile
            caption: Подпись к видео
            width: Ширина видео
            height: Высота видео
            supports_streaming: Поддержка стриминга
            max_retries: Максимальное количество попыток
        """
        for attempt in range(max_retries):
            try:
                # Увеличиваем таймаут для больших файлов
                request_timeout = 300 if attempt == 0 else 600  # 5 минут, затем 10 минут
                
                await self.bot.send_video(
                    user_id,
                    video,
                    caption=caption,
                    width=width,
                    height=height,
                    supports_streaming=supports_streaming,
                    request_timeout=request_timeout
                )
                logger.info(f"   ✅ Видео успешно отправлено (попытка {attempt + 1})")
                return
            except Exception as e:
                error_msg = str(e).lower()
                if attempt < max_retries - 1:
                    # Увеличиваем задержку между попытками
                    delay = (attempt + 1) * 5  # 5, 10, 15 секунд
                    logger.warning(f"   ⚠️ Ошибка при отправке видео (попытка {attempt + 1}/{max_retries}): {e}")
                    logger.info(f"   🔄 Повторная попытка через {delay} секунд...")
                    await asyncio.sleep(delay)
                else:
                    logger.error(f"   ❌ Не удалось отправить видео после {max_retries} попыток: {e}")
                    raise
    
    def _register_handlers(self):
        """Register all bot handlers."""
        # ВАЖНО: Команды регистрируем ПЕРВЫМИ, до общих обработчиков текста
        self.dp.message.register(self.handle_start, CommandStart())
        self.dp.message.register(self.handle_current_lesson, Command("lesson"))
        self.dp.message.register(self.handle_progress, Command("progress"))
        # ВРЕМЕННАЯ КНОПКА ДЛЯ ПРОВЕРКИ УРОКОВ
        self.dp.message.register(self.handle_test_lessons, Command("test_lessons"))
        # НАВИГАТОР КУРСА
        self.dp.message.register(self.handle_navigator, Command("navigator"))
        
        logger.info("✅ Course bot handlers registered:")
        logger.info(f"   - /start -> handle_start")
        logger.info(f"   - /lesson -> handle_current_lesson")
        logger.info(f"   - /progress -> handle_progress")
        logger.info(f"   - /test_lessons -> handle_test_lessons")
        logger.info(f"   - /navigator -> handle_navigator")
        
        # Callback handlers
        self.dp.callback_query.register(self.handle_test_lesson_select, F.data.startswith("test_lesson:"))
        self.dp.callback_query.register(self.handle_navigator_open, F.data == "navigator:open")
        self.dp.callback_query.register(self.handle_navigator_lesson_select, F.data.startswith("navigator:lesson:"))
        self.dp.callback_query.register(self.handle_navigator_close, F.data == "navigator:close")
        self.dp.callback_query.register(self.handle_submit_assignment, F.data.startswith("assignment:submit:"))
        self.dp.callback_query.register(self.handle_ask_question, F.data.startswith("question:ask:"))
        self.dp.callback_query.register(self.handle_admin_reply, F.data.startswith("admin_reply:"))
        self.dp.callback_query.register(self.handle_curator_reply, F.data.startswith("curator_reply:"))
        self.dp.callback_query.register(self.handle_lesson21_card, F.data.startswith("lesson21_card:"))
        self.dp.callback_query.register(self.handle_lesson21_download_cards, F.data == "lesson21_download_cards")
        self.dp.callback_query.register(self.handle_lesson19_show_levels, F.data == "lesson19_show_levels")
        self.dp.callback_query.register(self.handle_final_message, F.data == "lesson30_final_message")
        
        # Обработчики для постоянных кнопок клавиатуры
        # ВАЖНО: Регистрируем ПЕРЕД общими обработчиками текста, чтобы они имели приоритет
        self.dp.message.register(self.handle_keyboard_navigator, F.text == "🧭")
        self.dp.message.register(self.handle_keyboard_ask_question, F.text == "❓")
        self.dp.message.register(self.handle_keyboard_tariffs, F.text == "💎")
        # Кнопка 🔍 была тестовой и удалена из постоянной клавиатуры
        self.dp.message.register(self.handle_keyboard_discussion, F.text == "💬")
        self.dp.message.register(self.handle_keyboard_mentor, F.text == "👨‍🏫")
        
        # Обработчики для настройки наставника
        self.dp.callback_query.register(self.handle_mentor_set_frequency, F.data.startswith("mentor:set:"))
        
        # Общие обработчики сообщений (после команд!)
        # ВАЖНО: Используем F.text & ~F.command чтобы НЕ перехватывать команды
        self.dp.message.register(self.handle_assignment_text, F.text & ~F.command)
        self.dp.message.register(self.handle_assignment_media, F.photo | F.video | F.document)
        self.dp.message.register(self.handle_question_text, F.text & ~F.command)
        
        # Обработка ответов кураторов на вопросы (в группе кураторов или админ-чате)
        curator_chat_ids = []
        if Config.CURATOR_GROUP_ID:
            try:
                curator_chat_ids.append(int(Config.CURATOR_GROUP_ID))
            except (ValueError, TypeError):
                pass
        if Config.ADMIN_CHAT_ID:
            curator_chat_ids.append(Config.ADMIN_CHAT_ID)
        
        if curator_chat_ids:
            for chat_id in curator_chat_ids:
                self.dp.message.register(
                    self.handle_curator_feedback,
                    F.chat.id == chat_id,
                    F.reply_to_message
                )
        
        self.dp.message.register(self.handle_admin_feedback, F.chat.id == Config.ADMIN_CHAT_ID, F.reply_to_message)
    
    async def handle_start(self, message: Message):
        """Handle /start command - check access and show current lesson."""
        user_id = message.from_user.id
        
        # Обновляем информацию о пользователе из Telegram
        username = message.from_user.username
        first_name = message.from_user.first_name
        last_name = message.from_user.last_name
        
        # Получаем или создаем пользователя с обновленной информацией
        user = await self.user_service.get_or_create_user(
            user_id, username, first_name, last_name
        )
        
        persistent_keyboard = self._create_persistent_keyboard()
        
        if not user:
            await message.answer(
                "❌ У вас нет доступа к этому курсу.\n\n"
                "Пожалуйста, сначала приобретите доступ через нашего продающего бота @StartNowQ_bot",
                reply_markup=persistent_keyboard
            )
            return
        
        if not user.has_access():
            await message.answer(
                "❌ У вас нет активного доступа к курсу.\n\n"
                "Пожалуйста, сначала приобретите доступ через нашего продающего бота @StartNowQ_bot",
                reply_markup=persistent_keyboard
            )
            return
        
        # Show welcome and current lesson
        # Имя для приветствия: сначала берем из Telegram, потом из БД, затем username
        # Важно: проверяем на None и пустую строку
        user_name = None
        if first_name and str(first_name).strip() and str(first_name).strip().lower() != "none":
            user_name = str(first_name).strip()
        elif user.first_name and str(user.first_name).strip() and str(user.first_name).strip().lower() != "none":
            user_name = str(user.first_name).strip()
        elif username and str(username).strip():
            user_name = f"@{str(username).strip()}"
        elif user.username and str(user.username).strip():
            user_name = f"@{str(user.username).strip()}"
        else:
            user_name = "друг"
        
        logger.info(f"   User name determined: '{user_name}' (first_name={first_name}, user.first_name={user.first_name}, username={username})")
        
        persistent_keyboard = self._create_persistent_keyboard()
        await message.answer(
            f"👋 Добро пожаловать в курс, {user_name}!\n\n"
            f"📅 День {user.current_day} из {Config.COURSE_DURATION_DAYS}\n"
            f"📚 Тариф: {user.tariff.value.upper()}\n\n"
            f"Используйте /lesson для просмотра текущего урока.",
            reply_markup=persistent_keyboard
        )
    
    async def handle_current_lesson(self, message: Message):
        """Handle /lesson command - show current lesson."""
        user_id = message.from_user.id
        logger.info(f"📚 Command /lesson received from user {user_id}")
        logger.info(f"   Message text: {message.text}")
        logger.info(f"   Chat ID: {message.chat.id}")
        
        # Отправляем индикатор печати
        try:
            await send_typing_action(self.bot, user_id, 0.5)
        except Exception as e:
            logger.warning(f"   Failed to send typing action: {e}")
        
        try:
            await self._send_current_lesson(user_id)
        except Exception as e:
            logger.error(f"❌ Error in handle_current_lesson for user {user_id}: {e}", exc_info=True)
            try:
                persistent_keyboard = self._create_persistent_keyboard()
                await message.answer("❌ Произошла ошибка при загрузке урока. Попробуйте позже.", reply_markup=persistent_keyboard)
            except Exception as send_error:
                logger.error(f"   Failed to send error message: {send_error}")
    
    async def _send_current_lesson(self, user_id: int):
        """Send current lesson to user from JSON."""
        try:
            user = await self.user_service.get_user(user_id)
            logger.info(f"📚 _send_current_lesson called for user {user_id}")
            logger.info(f"   User lookup: {user is not None}, has_access: {user.has_access() if user else False}")
            
            if not user:
                logger.warning(f"   User {user_id} not found in database")
                await self.bot.send_message(
                    user_id,
                    "❌ У вас нет доступа к этому курсу.\n\n"
                    "Пожалуйста, сначала приобретите доступ через нашего продающего бота @StartNowQ_bot"
                )
                return
            
            if not user.has_access():
                logger.warning(f"   User {user_id} does not have access")
                await self.bot.send_message(
                    user_id,
                    "❌ У вас нет активного доступа к курсу.\n\n"
                    "Пожалуйста, сначала приобретите доступ через нашего продающего бота @StartNowQ_bot"
                )
                return
            
            logger.info(f"   User {user_id}: current_day={user.current_day} (type: {type(user.current_day)}), tariff={user.tariff}")
            
            # Проверяем, не завершен ли курс
            if user.current_day > Config.COURSE_DURATION_DAYS:
                await self.bot.send_message(
                    user_id,
                    f"🎉 <b>Поздравляем!</b>\n\n"
                    f"Вы завершили все {Config.COURSE_DURATION_DAYS} уроков курса!\n\n"
                    f"Спасибо за участие! 🎊"
                )
                return
            
            # Загружаем урок из JSON
            logger.info(f"   Loading lesson for day {user.current_day}")
            logger.info(f"   Lesson loader available: {self.lesson_loader is not None}")
            
            if not self.lesson_loader:
                logger.error(f"   ❌ Lesson loader is None!")
                await self.bot.send_message(
                    user_id,
                    "❌ Ошибка: загрузчик уроков не инициализирован. Обратитесь в поддержку."
                )
                return
            
            # Проверяем кэш уроков
            if not self.lesson_loader._lessons_cache:
                logger.error(f"   ❌ Lessons cache is empty! Reloading...")
                self.lesson_loader.reload()
            
            cache_size = len(self.lesson_loader._lessons_cache) if self.lesson_loader._lessons_cache else 0
            logger.info(f"   Lessons cache size: {cache_size}")
            
            if self.lesson_loader._lessons_cache:
                available_days = sorted([int(k) for k in self.lesson_loader._lessons_cache.keys() if k.isdigit()])[:20]
                logger.info(f"   Available days (first 20): {available_days}")
            
            # Пробуем получить урок
            day_key = str(user.current_day)
            logger.info(f"   Looking for lesson with key: '{day_key}' (day={user.current_day}, type={type(user.current_day)})")
            
            lesson_data = self.lesson_loader.get_lesson(user.current_day)
            logger.info(f"   Lesson data loaded: {lesson_data is not None}")
            
            if lesson_data:
                logger.info(f"   ✅ Lesson found! Title: {lesson_data.get('title', 'No title')}")
            else:
                logger.warning(f"   ❌ No lesson data for day {user.current_day} (key: '{day_key}')")
                # Проверяем, есть ли урок в кэше напрямую
                if self.lesson_loader._lessons_cache:
                    direct_check = self.lesson_loader._lessons_cache.get(day_key)
                    logger.info(f"   Direct cache check for '{day_key}': {direct_check is not None}")
                    if direct_check:
                        logger.info(f"   Direct cache has lesson! Title: {direct_check.get('title', 'No title')}")
            
            if not lesson_data:
                persistent_keyboard = self._create_persistent_keyboard()
                await self.bot.send_message(
                    user_id,
                    f"⏳ Урок для дня {user.current_day} пока не готов.\n"
                    f"Он будет отправлен автоматически, когда наступит время.",
                    reply_markup=persistent_keyboard
                )
                return
            
            # Проверяем день тишины (но для урока 21 все равно отправляем урок с карточками)
            if self.lesson_loader.is_silent_day(user.current_day) and user.current_day != 21:
                logger.info(f"   Day {user.current_day} is silent day for user {user_id}")
                persistent_keyboard = self._create_persistent_keyboard()
                await self.bot.send_message(
                    user_id,
                    f"🔇 Сегодня день тишины (День {user.current_day}).\n\n"
                    f"Отдыхайте и переваривайте полученные знания! 📚",
                    reply_markup=persistent_keyboard
                )
                return
            
            # Отправляем урок с анимацией
            logger.info(f"   ✅ Lesson data found! Sending lesson {user.current_day} to user {user_id}")
            logger.info(f"   Lesson title: {lesson_data.get('title', 'No title')}")
            try:
                await send_typing_action(self.bot, user_id, 0.8)
                await self._send_lesson_from_json(user, lesson_data, user.current_day)
                logger.info(f"   ✅ Lesson {user.current_day} sent successfully to user {user_id}")
            except Exception as send_error:
                logger.error(f"   ❌ Error sending lesson: {send_error}", exc_info=True)
                raise
            
        except Exception as e:
            logger.error(f"❌ Error in _send_current_lesson for user {user_id}: {e}", exc_info=True)
            try:
                persistent_keyboard = self._create_persistent_keyboard()
                await self.bot.send_message(
                    user_id,
                    "❌ Произошла ошибка при загрузке урока. Попробуйте позже или обратитесь в поддержку.",
                    reply_markup=persistent_keyboard
                )
            except:
                pass
    
    async def handle_progress(self, message: Message):
        """Handle /progress command - show user progress."""
        user_id = message.from_user.id
        user = await self.user_service.get_user(user_id)
        
        persistent_keyboard = self._create_persistent_keyboard()
        
        if not user or not user.has_access():
            await message.answer("❌ У вас нет доступа к этому курсу.", reply_markup=persistent_keyboard)
            return
        
        progress_percent = (user.current_day / Config.COURSE_DURATION_DAYS) * 100
        
        await message.answer(
            f"📊 <b>Ваш прогресс</b>\n\n"
            f"Текущий день: <b>{user.current_day}/{Config.COURSE_DURATION_DAYS}</b>\n"
            f"Прогресс: <b>{progress_percent:.1f}%</b>\n"
            f"Тариф: <b>{user.tariff.value.upper()}</b>\n"
            f"Начало: {user.start_date.strftime('%Y-%m-%d') if user.start_date else 'Не указано'}",
            reply_markup=persistent_keyboard
        )
    
    async def handle_test_lessons(self, message: Message):
        """ВРЕМЕННАЯ КНОПКА ДЛЯ ПРОВЕРКИ УРОКОВ - показать список уроков для выбора."""
        user_id = message.from_user.id
        user = await self.user_service.get_user(user_id)
        
        persistent_keyboard = self._create_persistent_keyboard()
        
        if not user:
            await message.answer("❌ У вас нет доступа. Эта функция только для тестирования.", reply_markup=persistent_keyboard)
            return
        
        # Создаем клавиатуру с номерами уроков (0-30)
        buttons = []
        row = []
        
        for day in range(31):  # 0-30
            row.append(InlineKeyboardButton(
                text=f"{day}",
                callback_data=f"test_lesson:{day}"
            ))
            
            # По 5 кнопок в ряд
            if len(row) == 5:
                buttons.append(row)
                row = []
        
        # Добавляем оставшиеся кнопки
        if row:
            buttons.append(row)
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
        
        await message.answer(
            "🔍 <b>ВРЕМЕННАЯ КНОПКА ДЛЯ ПРОВЕРКИ УРОКОВ</b>\n\n"
            "Выберите урок для просмотра:\n\n"
            "⚠️ <i>Эта функция временная и будет удалена после проверки.</i>",
            reply_markup=keyboard
        )
    
    async def handle_test_lesson_select(self, callback: CallbackQuery):
        """ВРЕМЕННАЯ КНОПКА ДЛЯ ПРОВЕРКИ УРОКОВ - отправить выбранный урок."""
        try:
            await callback.answer()
        except:
            pass
        
        user_id = callback.from_user.id
        user = await self.user_service.get_user(user_id)
        
        persistent_keyboard = self._create_persistent_keyboard()
        
        if not user:
            await callback.message.answer("❌ У вас нет доступа.", reply_markup=persistent_keyboard)
            return
        
        # Парсим номер урока из callback
        try:
            day = int(callback.data.split(":")[1])
        except (ValueError, IndexError):
            await callback.message.answer("❌ Ошибка: неверный номер урока.", reply_markup=persistent_keyboard)
            return
        
        logger.info(f"🔍 Test lesson {day} requested by user {user_id}")
        
        # Проверяем, что lesson_loader инициализирован
        if not self.lesson_loader:
            logger.error("❌ LessonLoader не инициализирован!")
            await callback.message.answer(
                "❌ Ошибка: загрузчик уроков не инициализирован. Обратитесь в поддержку.",
                reply_markup=persistent_keyboard
            )
            return
        
        # Проверяем, что уроки загружены
        lesson_count = self.lesson_loader.get_lesson_count()
        if lesson_count == 0:
            logger.error(f"❌ Уроки не загружены! Всего уроков: {lesson_count}")
            await callback.message.answer(
                "❌ Ошибка: уроки не загружены. Обратитесь в поддержку.",
                reply_markup=persistent_keyboard
            )
            return
        
        logger.info(f"   📚 Загружено уроков: {lesson_count}, ищу урок {day}")
        
        # Загружаем урок из JSON
        lesson_data = self.lesson_loader.get_lesson(day)
        
        if not lesson_data:
            # Получаем список доступных уроков безопасным способом
            available_lessons = []
            try:
                all_lessons = self.lesson_loader.get_all_lessons()
                available_lessons = sorted([int(k) for k in all_lessons.keys() if k.isdigit()])
            except Exception as e:
                logger.error(f"Ошибка при получении списка уроков: {e}")
            
            logger.error(f"❌ Урок {day} не найден в JSON файле. Доступные уроки: {available_lessons}")
            await callback.message.answer(
                f"❌ Урок для дня {day} не найден.\n\n"
                f"Доступные уроки: {', '.join(map(str, available_lessons[:10]))}{'...' if len(available_lessons) > 10 else ''}" if available_lessons else f"Доступные уроки: 0-{lesson_count-1}",
                reply_markup=persistent_keyboard
            )
            return
        
        logger.info(f"   ✅ Урок {day} найден: {lesson_data.get('title', 'No title')}")
        
        # Временно меняем current_day пользователя для корректного отображения
        original_day = user.current_day
        user.current_day = day
        
        # Отправляем урок
        try:
            await send_typing_action(self.bot, user_id, 0.8)
            await self._send_lesson_from_json(user, lesson_data, day)
            # Убеждаемся, что постоянная клавиатура видна после отправки урока
            await self._ensure_persistent_keyboard(user_id)
        except Exception as e:
            error_msg = str(e)
            # Фильтруем технические ошибки Telegram API, которые не нужно показывать пользователю
            if "text must be non-empty" in error_msg or "message text is empty" in error_msg:
                logger.warning(f"⚠️ Empty message error for lesson {day} (suppressed): {error_msg}")
            else:
                logger.error(f"❌ Error sending test lesson {day}: {e}", exc_info=True)
                # Показываем пользователю только понятные ошибки
                if "Bad Request" not in error_msg or "text must be non-empty" not in error_msg:
                    try:
                        await callback.message.answer(f"❌ Ошибка при отправке урока {day}. Попробуйте позже.", reply_markup=persistent_keyboard)
                    except:
                        pass
        finally:
            # Восстанавливаем original_day (не сохраняем в БД, это только для отображения)
            user.current_day = original_day
    
    async def handle_lesson21_card(self, callback: CallbackQuery):
        """Обработчик для отображения карточки из урока 21."""
        try:
            await callback.answer()
        except:
            pass
        
        user_id = callback.from_user.id
        user = await self.user_service.get_user(user_id)
        
        if not user:
            await callback.message.answer("❌ У вас нет доступа.")
            return
        
        # Загружаем урок 21
        lesson_data = self.lesson_loader.get_lesson(21)
        if not lesson_data:
            await callback.message.answer("❌ Урок 21 не найден.")
            return
        
        cards = lesson_data.get("cards", [])
        if not cards:
            await callback.message.answer("❌ Карточки не найдены.")
            return
        
        # Парсим данные из callback
        callback_data = callback.data.split(":")[1]
        
        # Если выбрана случайная карточка
        if callback_data == "random":
            import random
            card = random.choice(cards)
            card_number = card.get("number", 0)
            logger.info(f"   🎲 Random card {card_number} selected for lesson 21 to user {user_id}")
        else:
            # Парсим номер карточки из callback
            try:
                card_number = int(callback_data)
            except (ValueError, IndexError):
                await callback.message.answer("❌ Ошибка: неверный номер карточки.")
                return
            
            # Находим карточку
            card = None
            for c in cards:
                if c.get("number") == card_number:
                    card = c
                    break
            
            if not card:
                await callback.message.answer(f"❌ Карточка {card_number} не найдена.")
                return
        
        # Отправляем карточку
        try:
            # Анимация перед отправкой карточки
            await send_typing_action(self.bot, user_id, 0.3)
            file_id = card.get("file_id")
            centered_caption = "━━━━━━━━━━━━━━"
            if file_id:
                await self.bot.send_photo(user_id, file_id, caption=centered_caption)
                logger.info(f"   ✅ Sent card {card_number} for lesson 21 to user {user_id}")
            else:
                # Fallback: загрузка с диска
                from pathlib import Path
                from aiogram.types import FSInputFile
                import os
                
                file_path = card.get("path", "")
                if file_path:
                    normalized_path = file_path.replace('/', os.sep)
                    project_root = Path.cwd()
                    card_file = project_root / normalized_path
                    
                    if card_file.exists():
                        photo_file = FSInputFile(card_file)
                        centered_caption = "━━━━━━━━━━━━━━"
                        await self.bot.send_photo(user_id, photo_file, caption=centered_caption)
                        logger.info(f"   ✅ Sent card {card_number} (from file) for lesson 21 to user {user_id}")
                    else:
                        await callback.message.answer(f"❌ Файл карточки {card_number} не найден.")
                else:
                    await callback.message.answer(f"❌ Не удалось загрузить карточку {card_number}.")
        except Exception as e:
            logger.error(f"   ❌ Ошибка при отправке карточки {card_number}: {e}", exc_info=True)
            await callback.message.answer(f"❌ Ошибка при отправке карточки {card_number}.")
    
    async def handle_lesson21_download_cards(self, callback: CallbackQuery):
        """Обработчик для скачивания всех карточек урока 21."""
        try:
            await callback.answer("📥 Загружаю карточки...")
        except:
            pass
        
        user_id = callback.from_user.id
        user = await self.user_service.get_user(user_id)
        
        if not user:
            await callback.message.answer("❌ У вас нет доступа.")
            return
        
        # Загружаем урок 21
        lesson_data = self.lesson_loader.get_lesson(21)
        if not lesson_data:
            await callback.message.answer("❌ Урок 21 не найден.")
            return
        
        cards = lesson_data.get("cards", [])
        if not cards:
            await callback.message.answer("❌ Карточки не найдены.")
            return
        
        # Сортируем карточки по номеру
        cards = sorted(cards, key=lambda x: x.get("number", 0))
        
        try:
            from aiogram.types import InputMediaPhoto
            
            # Telegram позволяет отправлять до 10 медиа в одной группе
            # Разбиваем на группы по 10 карточек
            MAX_MEDIA_PER_GROUP = 10
            
            for group_start in range(0, len(cards), MAX_MEDIA_PER_GROUP):
                group_cards = cards[group_start:group_start + MAX_MEDIA_PER_GROUP]
                media_group = []
                
                for card in group_cards:
                    card_number = card.get("number", 0)
                    file_id = card.get("file_id")
                    
                    if file_id:
                        media_group.append(
                            InputMediaPhoto(
                                media=file_id
                            )
                        )
                    else:
                        # Fallback: загрузка с диска
                        from pathlib import Path
                        from aiogram.types import FSInputFile
                        import os
                        
                        file_path = card.get("path", "")
                        if file_path:
                            normalized_path = file_path.replace('/', os.sep)
                            project_root = Path.cwd()
                            card_file = project_root / normalized_path
                            
                            if card_file.exists():
                                photo_file = FSInputFile(card_file)
                                media_group.append(
                                    InputMediaPhoto(
                                        media=photo_file
                                    )
                                )
                
                if media_group:
                    # Отправляем медиа-группу
                    await self.bot.send_media_group(user_id, media_group)
                    logger.info(f"   ✅ Sent media group {group_start // MAX_MEDIA_PER_GROUP + 1} with {len(media_group)} cards to user {user_id}")
                    
                    # Небольшая пауза между группами
                    if group_start + MAX_MEDIA_PER_GROUP < len(cards):
                        await asyncio.sleep(0.5)
            
            logger.info(f"   ✅ All {len(cards)} cards sent to user {user_id}")
            
        except Exception as e:
            logger.error(f"   ❌ Ошибка при отправке карточек: {e}", exc_info=True)
            await callback.message.answer("❌ Произошла ошибка при отправке карточек. Попробуйте позже.")
    
    async def handle_lesson19_show_levels(self, callback: CallbackQuery):
        """Обработчик для показа всех уровней урока 19."""
        try:
            await callback.answer("📊 Загружаю уровни...")
        except:
            pass
        
        user_id = callback.from_user.id
        user = await self.user_service.get_user(user_id)
        
        if not user:
            await callback.message.answer("❌ У вас нет доступа.")
            return
        
        # Загружаем урок 19
        lesson_data = self.lesson_loader.get_lesson(19)
        if not lesson_data:
            await callback.message.answer("❌ Урок 19 не найден.")
            return
        
        levels_images = lesson_data.get("levels_images", [])
        if not levels_images:
            await callback.message.answer("❌ Изображения уровней не найдены.")
            return
        
        # Сортируем изображения по номеру
        levels_images = sorted(levels_images, key=lambda x: x.get("number", 0))
        
        try:
            from aiogram.types import InputMediaPhoto, InputMediaGroup
            from pathlib import Path
            from aiogram.types import FSInputFile
            import os
            
            logger.info(f"   📊 Начинаю оптимизированную отправку {len(levels_images)} изображений для урока 19")
            
            # Убираем дубликаты по file_id и path
            seen_file_ids = set()
            seen_paths = set()
            unique_images = []
            
            for image in levels_images:
                file_id = image.get("file_id")
                file_path = image.get("path", "")
                
                # Проверяем дубликаты
                is_duplicate = False
                if file_id and file_id in seen_file_ids:
                    is_duplicate = True
                    logger.debug(f"   ⏭️ Пропускаю дубликат по file_id: {image.get('number', '?')}")
                elif file_path and file_path in seen_paths:
                    is_duplicate = True
                    logger.debug(f"   ⏭️ Пропускаю дубликат по path: {image.get('number', '?')}")
                
                if not is_duplicate:
                    if file_id:
                        seen_file_ids.add(file_id)
                    if file_path:
                        seen_paths.add(file_path)
                    unique_images.append(image)
            
            logger.info(f"   📊 После удаления дубликатов: {len(unique_images)} уникальных изображений")
            
            # Сортируем по номеру
            unique_images = sorted(unique_images, key=lambda x: x.get("number", 0))
            
            # Определяем корень проекта
            project_root = None
            possible_roots = [Path.cwd(), Path(__file__).parent.parent]
            for root in possible_roots:
                if (root / "Photo" / "video_pic").exists() or (root / "Photo" / "video_pic_optimized").exists():
                    project_root = root
                    break
            if not project_root:
                project_root = Path.cwd()
            
            # Создаем медиа-группу для быстрой отправки (максимум 10 в группе)
            media_groups = []
            current_group = []
            
            for image in unique_images:
                image_number = image.get("number", 0)
                file_id = image.get("file_id")
                file_path = image.get("path", "")
                
                try:
                    if file_id:
                        # Используем file_id (самый быстрый способ)
                        media_item = InputMediaPhoto(media=file_id)
                        current_group.append(media_item)
                    elif file_path:
                        # Загрузка с диска
                        normalized_path = file_path.replace('/', os.sep)
                        image_file = project_root / normalized_path
                        
                        if not image_file.exists():
                            original_path = file_path.replace('video_pic_optimized', 'video_pic')
                            image_file = project_root / original_path.replace('/', os.sep)
                        
                        if image_file.exists() and image_file.is_file():
                            photo_file = FSInputFile(image_file)
                            media_item = InputMediaPhoto(media=photo_file)
                            current_group.append(media_item)
                        else:
                            logger.warning(f"   ⚠️ Файл не найден: {file_path}")
                            continue
                    
                    # Telegram ограничение: максимум 10 медиа в группе
                    if len(current_group) >= 10:
                        media_groups.append(current_group)
                        current_group = []
                        
                except Exception as img_error:
                    logger.error(f"   ❌ Ошибка при подготовке изображения {image_number}: {img_error}")
                    continue
            
            # Добавляем последнюю группу, если она не пустая
            if current_group:
                media_groups.append(current_group)
            
            # Отправляем медиа-группы
            total_sent = 0
            for i, media_group in enumerate(media_groups):
                try:
                    if len(media_group) == 1:
                        # Одно изображение отправляем отдельно
                        media_item = media_group[0]
                        if isinstance(media_item.media, str):
                            # file_id
                            await self.bot.send_photo(user_id, media_item.media)
                        else:
                            # FSInputFile
                            await self.bot.send_photo(user_id, media_item.media)
                        total_sent += 1
                    else:
                        # Медиа-группа (2-10 изображений)
                        await self.bot.send_media_group(user_id, media_group)
                        total_sent += len(media_group)
                    
                    # Минимальная пауза между группами для стабильности API
                    if i < len(media_groups) - 1:
                        await asyncio.sleep(0.1)
                        
                except Exception as group_error:
                    logger.error(f"   ❌ Ошибка при отправке медиа-группы {i+1}: {group_error}")
                    # Пробуем отправить по одному
                    for media_item in media_group:
                        try:
                            if isinstance(media_item.media, str):
                                await self.bot.send_photo(user_id, media_item.media)
                            else:
                                await self.bot.send_photo(user_id, media_item.media)
                            total_sent += 1
                            await asyncio.sleep(0.1)
                        except:
                            continue
            
            if total_sent > 0:
                logger.info(f"   ✅ Отправлено {total_sent} уникальных изображений")
                if total_sent < len(unique_images):
                    await callback.message.answer(f"✅ Отправлено {total_sent} из {len(unique_images)} изображений.")
            else:
                raise Exception(f"Не удалось отправить ни одного изображения из {len(unique_images)}")
            
        except Exception as e:
            logger.error(f"   ❌ Ошибка при отправке уровней: {e}", exc_info=True)
            logger.error(f"   📊 Debug info: total_images={len(levels_images)}, user_id={user_id}")
            
            # Пробуем отправить изображения по одному, если медиа-группа не работает
            try:
                logger.info(f"   🔄 Пробую отправить изображения по одному...")
                sent_count = 0
                
                for image in levels_images:
                    file_id = image.get("file_id")
                    file_path = image.get("path", "")
                    
                    try:
                        if file_id:
                            await self.bot.send_photo(user_id, file_id)
                            sent_count += 1
                            await asyncio.sleep(0.3)
                        elif file_path:
                            from pathlib import Path
                            from aiogram.types import FSInputFile
                            import os
                            
                            normalized_path = file_path.replace('/', os.sep)
                            project_root = Path.cwd()
                            image_file = project_root / normalized_path
                            
                            # Если оптимизированный файл не найден, пробуем оригинальный
                            if not image_file.exists():
                                original_path = file_path.replace('video_pic_optimized', 'video_pic')
                                original_file = project_root / original_path.replace('/', os.sep)
                                if original_file.exists():
                                    image_file = original_file
                            
                            if image_file.exists():
                                photo_file = FSInputFile(image_file)
                                await self.bot.send_photo(user_id, photo_file)
                                sent_count += 1
                                await asyncio.sleep(0.3)
                            else:
                                logger.warning(f"   ⚠️ Файл не найден: {file_path}")
                    except Exception as single_error:
                        logger.error(f"   ❌ Ошибка при отправке одного изображения: {single_error}")
                
                if sent_count > 0:
                    await callback.message.answer(f"✅ Отправлено {sent_count} из {len(levels_images)} изображений.")
                else:
                    await callback.message.answer("❌ Не удалось отправить изображения. Проверьте логи.")
            except Exception as fallback_error:
                logger.error(f"   ❌ Ошибка в fallback режиме: {fallback_error}", exc_info=True)
                await callback.message.answer("❌ Произошла ошибка при отправке уровней. Попробуйте позже.")
    
    async def handle_final_message(self, callback: CallbackQuery):
        """Обработчик для финального сообщения урока 30."""
        try:
            await callback.answer("🎊 Загружаю финальное сообщение...")
        except:
            pass
        
        user_id = callback.from_user.id
        user = await self.user_service.get_user(user_id)
        
        if not user:
            await callback.message.answer("❌ У вас нет доступа.")
            return
        
        # Загружаем урок 30
        lesson_data = self.lesson_loader.get_lesson(30)
        if not lesson_data:
            await callback.message.answer("❌ Урок 30 не найден.")
            return

        # Автоматическая/единая отправка финального сообщения (используется также в авто-режиме после задания)
        try:
            await self._send_lesson30_final_message_to_user(
                user_id=user_id,
                lesson_data=lesson_data,
                send_keyboard=True
            )
            return
        except Exception as e:
            logger.error(f"   ❌ Ошибка при отправке финального сообщения (единый метод): {e}", exc_info=True)
            await callback.message.answer("❌ Произошла ошибка при отправке финального сообщения. Попробуйте позже.")
            return
        
        try:
            follow_up_text = lesson_data.get("follow_up_text", "")
            follow_up_photo_path = lesson_data.get("follow_up_photo_path", "")
            follow_up_photo_file_id = lesson_data.get("follow_up_photo_file_id", "")
            
            persistent_keyboard = self._create_persistent_keyboard()
            
            # Отправляем фото с текстом в caption, если есть фото
            # Telegram ограничение: caption максимум 1024 символа
            photo_sent = False
            if follow_up_photo_file_id:
                try:
                    # Анимация перед отправкой фото
                    await send_typing_action(self.bot, user_id, 0.6)
                    # Разделяем текст на части, если он длиннее 1024 символов
                    # Важно: не делим слова при разбиении
                    if follow_up_text and follow_up_text.strip():
                            if len(follow_up_text) > 1024:
                                # Ищем оптимальную точку разбиения - не делим слова
                                split_point = 1024
                                
                                # Проверяем, не попадает ли слово "Отснятый" на границу разбиения
                                # Слово начинается на позиции 1023, нужно переместить его полностью во второй блок
                                word_to_check = "Отснятый"
                                word_index = follow_up_text.find(word_to_check, split_point - 30, split_point + 10)
                                if word_index != -1:
                                    # Найдено слово "Отснятый" в области границы разбиения
                                    if word_index <= split_point:
                                        # Слово начинается до или на границе - сдвигаем границу перед началом слова
                                        # Ищем последний перенос строки перед началом слова
                                        optimal_split = follow_up_text.rfind('\n', 0, word_index)
                                        if optimal_split != -1 and optimal_split > split_point - 50:
                                            split_point = optimal_split
                                        else:
                                            # Если переноса строки нет, ищем пробел
                                            optimal_split = follow_up_text.rfind(' ', 0, word_index)
                                            if optimal_split != -1 and optimal_split > split_point - 50:
                                                split_point = optimal_split
                                            else:
                                                # Если пробела нет, разбиваем прямо перед словом
                                                split_point = word_index
                                    elif word_index < split_point + len(word_to_check):
                                        # Слово пересекает границу разбиения - сдвигаем границу перед началом слова
                                        optimal_split = follow_up_text.rfind('\n', 0, word_index)
                                        if optimal_split != -1 and optimal_split > split_point - 50:
                                            split_point = optimal_split
                                        else:
                                            optimal_split = follow_up_text.rfind(' ', 0, word_index)
                                            if optimal_split != -1 and optimal_split > split_point - 50:
                                                split_point = optimal_split
                                            else:
                                                split_point = word_index
                                
                                # Если не нашли слово "Отснятый", используем стандартную логику
                                if word_index == -1 or split_point == 1024:
                                    # Ищем последний пробел или перенос строки перед 1024-м символом
                                    # Но не раньше, чем за 50 символов от 1024
                                    search_start = max(0, split_point - 50)
                                    optimal_split = follow_up_text.rfind('\n', search_start, split_point)
                                    if optimal_split == -1:
                                        optimal_split = follow_up_text.rfind(' ', search_start, split_point)
                                    if optimal_split != -1 and optimal_split > split_point - 100:
                                        split_point = optimal_split
                                
                                caption_text = follow_up_text[:split_point].rstrip()
                                remaining_text = follow_up_text[split_point:].lstrip()
                            else:
                                caption_text = follow_up_text
                                remaining_text = None
                    else:
                        caption_text = "━━━━━━━━━━━━━━"
                        remaining_text = None
                    
                    await self.bot.send_photo(user_id, follow_up_photo_file_id, caption=caption_text, reply_markup=persistent_keyboard if not remaining_text else None)
                    logger.info(f"   ✅ Sent final message photo with text (file_id) for lesson 30")
                    photo_sent = True
                    
                    # Если есть остаток текста, отправляем его отдельным сообщением
                    if remaining_text:
                        await asyncio.sleep(0.5)
                        await self.bot.send_message(user_id, remaining_text, reply_markup=persistent_keyboard)
                        logger.info(f"   ✅ Sent remaining final message text for lesson 30")
                    
                    await asyncio.sleep(0.8)
                except Exception as photo_error:
                    logger.error(f"   ❌ Не удалось отправить финальное фото (file_id) для урока 30: {photo_error}", exc_info=True)
            
            if not photo_sent and follow_up_photo_path:
                try:
                    from pathlib import Path
                    from aiogram.types import FSInputFile
                    import os
                    
                    # Нормализуем путь
                    normalized_path = follow_up_photo_path.replace('/', os.sep)
                    photo_path = Path(normalized_path)
                    if not photo_path.exists():
                        project_root = Path.cwd()
                        photo_path = project_root / normalized_path
                    
                    # Пробуем альтернативные пути
                    if not photo_path.exists():
                        possible_paths = [
                            Path("Photo/30/photo_5377557667917794132_y.jpg"),
                            Path("Photo/30/photo_5404715149857328372_y.jpg"),
                            Path.cwd() / "Photo" / "30" / "photo_5377557667917794132_y.jpg",
                            Path.cwd() / "Photo" / "30" / "photo_5404715149857328372_y.jpg",
                        ]
                        for possible_path in possible_paths:
                            if possible_path.exists():
                                photo_path = possible_path
                                logger.info(f"   🔍 Found photo at alternative path: {photo_path.absolute()}")
                                break
                    
                    if photo_path.exists():
                        # Анимация перед отправкой фото
                        await send_typing_action(self.bot, user_id, 0.6)
                        photo_file = FSInputFile(photo_path)
                        # Разделяем текст на части, если он длиннее 1024 символов
                        # Важно: не делим слова при разбиении
                        if follow_up_text and follow_up_text.strip():
                            if len(follow_up_text) > 1024:
                                # Ищем оптимальную точку разбиения - не делим слова
                                split_point = 1024
                                
                                # Проверяем, не попадает ли слово "Отснятый" на границу разбиения
                                # Слово начинается на позиции 1023, нужно переместить его полностью во второй блок
                                word_to_check = "Отснятый"
                                word_index = follow_up_text.find(word_to_check, split_point - 30, split_point + 10)
                                if word_index != -1:
                                    # Найдено слово "Отснятый" в области границы разбиения
                                    if word_index <= split_point:
                                        # Слово начинается до или на границе - сдвигаем границу перед началом слова
                                        # Ищем последний перенос строки перед началом слова
                                        optimal_split = follow_up_text.rfind('\n', 0, word_index)
                                        if optimal_split != -1 and optimal_split > split_point - 50:
                                            split_point = optimal_split
                                        else:
                                            # Если переноса строки нет, ищем пробел
                                            optimal_split = follow_up_text.rfind(' ', 0, word_index)
                                            if optimal_split != -1 and optimal_split > split_point - 50:
                                                split_point = optimal_split
                                            else:
                                                # Если пробела нет, разбиваем прямо перед словом
                                                split_point = word_index
                                    elif word_index < split_point + len(word_to_check):
                                        # Слово пересекает границу разбиения - сдвигаем границу перед началом слова
                                        optimal_split = follow_up_text.rfind('\n', 0, word_index)
                                        if optimal_split != -1 and optimal_split > split_point - 50:
                                            split_point = optimal_split
                                        else:
                                            optimal_split = follow_up_text.rfind(' ', 0, word_index)
                                            if optimal_split != -1 and optimal_split > split_point - 50:
                                                split_point = optimal_split
                                            else:
                                                split_point = word_index
                                
                                # Если не нашли слово "Отснятый", используем стандартную логику
                                if word_index == -1 or split_point == 1024:
                                    # Ищем последний пробел или перенос строки перед 1024-м символом
                                    # Но не раньше, чем за 50 символов от 1024
                                    search_start = max(0, split_point - 50)
                                    optimal_split = follow_up_text.rfind('\n', search_start, split_point)
                                    if optimal_split == -1:
                                        optimal_split = follow_up_text.rfind(' ', search_start, split_point)
                                    if optimal_split != -1 and optimal_split > split_point - 100:
                                        split_point = optimal_split
                                
                                caption_text = follow_up_text[:split_point].rstrip()
                                remaining_text = follow_up_text[split_point:].lstrip()
                            else:
                                caption_text = follow_up_text
                                remaining_text = None
                        else:
                            caption_text = "━━━━━━━━━━━━━━"
                            remaining_text = None
                        
                        await self.bot.send_photo(user_id, photo_file, caption=caption_text, reply_markup=persistent_keyboard if not remaining_text else None)
                        logger.info(f"   ✅ Sent final message photo with text (file path: {photo_path}) for lesson 30")
                        photo_sent = True
                        
                        # Если есть остаток текста, отправляем его отдельным сообщением
                        if remaining_text:
                            await asyncio.sleep(0.5)
                            await self.bot.send_message(user_id, remaining_text, reply_markup=persistent_keyboard)
                            logger.info(f"   ✅ Sent remaining final message text for lesson 30")
                        
                        await asyncio.sleep(0.8)
                    else:
                        logger.error(f"   ❌ Final message photo not found: {photo_path.absolute()}")
                except Exception as photo_error:
                    logger.error(f"   ❌ Не удалось отправить финальное фото (file path) для урока 30: {photo_error}", exc_info=True)
            
            # Отправляем только текст, если фото нет
            if not photo_sent and follow_up_text and follow_up_text.strip():
                try:
                    # Анимация перед отправкой текста
                    await send_typing_action(self.bot, user_id, 0.8)
                    await self.bot.send_message(user_id, follow_up_text, reply_markup=persistent_keyboard)
                    logger.info(f"   ✅ Sent final message text (no photo) for lesson 30")
                except Exception as text_error:
                    error_msg = str(text_error)
                    logger.error(f"   ❌ Error sending final message text for lesson 30: {error_msg}", exc_info=True)
                    # Пробуем отправить еще раз без клавиатуры
                    try:
                        await self.bot.send_message(user_id, follow_up_text)
                        logger.info(f"   ✅ Sent final message text without keyboard for lesson 30")
                    except Exception as retry_error:
                        logger.error(f"   ❌ Retry also failed for lesson 30: {retry_error}")
                        await callback.message.answer("❌ Произошла ошибка при отправке финального сообщения. Попробуйте позже.")
            elif not photo_sent:
                await callback.message.answer("❌ Текст финального сообщения не найден.")
                
        except Exception as e:
            logger.error(f"   ❌ Ошибка при отправке финального сообщения: {e}", exc_info=True)
            await callback.message.answer("❌ Произошла ошибка при отправке финального сообщения. Попробуйте позже.")

    async def _send_lesson30_final_message_to_user(self, user_id: int, lesson_data: dict, send_keyboard: bool = True):
        """
        Единый метод отправки финального сообщения (follow_up) для урока 30.
        Используется:
        - по нажатию кнопки "🎊 ФИНАЛЬНОЕ СООБЩЕНИЕ"
        - автоматически после отправки обратной связи по заданию 30
        """
        follow_up_text = (lesson_data.get("follow_up_text", "") or "").strip()
        follow_up_photo_file_id = (lesson_data.get("follow_up_photo_file_id", "") or "").strip()
        follow_up_photo_path = (lesson_data.get("follow_up_photo_path", "") or "").strip()

        if not (follow_up_text or follow_up_photo_file_id or follow_up_photo_path):
            logger.warning("   ⚠️ Lesson 30 final message is empty (no text/photo).")
            return

        persistent_keyboard = self._create_persistent_keyboard() if send_keyboard else None

        CAPTION_LIMIT = 1024
        MAX_MESSAGE_LENGTH = 4000

        def _split_caption(text: str):
            if not text:
                return "━━━━━━━━━━━━━━", None
            if len(text) <= CAPTION_LIMIT:
                return text, None
            cut = text.rfind("\n", 0, CAPTION_LIMIT)
            if cut < 900:
                cut = text.rfind(" ", 0, CAPTION_LIMIT)
            if cut < 900:
                cut = CAPTION_LIMIT
            return text[:cut].rstrip(), text[cut:].lstrip()

        async def _send_text(text: str):
            if not text or not text.strip():
                return
            if len(text) > MAX_MESSAGE_LENGTH:
                parts = self._split_long_message(text, MAX_MESSAGE_LENGTH)
                for part in parts[:-1]:
                    if part and part.strip():
                        await self.bot.send_message(user_id, part)
                        await asyncio.sleep(0.3)
                last_part = parts[-1]
                if last_part and last_part.strip():
                    await self.bot.send_message(user_id, last_part, reply_markup=persistent_keyboard)
                elif persistent_keyboard:
                    await self.bot.send_message(user_id, "\u200B", reply_markup=persistent_keyboard)
            else:
                await self.bot.send_message(user_id, text, reply_markup=persistent_keyboard)

        # 1) Photo by file_id
        if follow_up_photo_file_id:
            caption, remaining = _split_caption(follow_up_text)
            await send_typing_action(self.bot, user_id, 0.6)
            await self.bot.send_photo(
                user_id,
                follow_up_photo_file_id,
                caption=caption,
                reply_markup=persistent_keyboard if (send_keyboard and not remaining) else None
            )
            if remaining:
                await asyncio.sleep(0.5)
                await _send_text(remaining)
            return

        # 2) Photo by path (optional)
        if follow_up_photo_path:
            try:
                from pathlib import Path
                from aiogram.types import FSInputFile
                import os

                normalized_path = follow_up_photo_path.replace("/", os.sep)
                photo_path = Path(normalized_path)
                if not photo_path.exists():
                    photo_path = Path.cwd() / normalized_path
                if photo_path.exists():
                    caption, remaining = _split_caption(follow_up_text)
                    await send_typing_action(self.bot, user_id, 0.6)
                    await self.bot.send_photo(
                        user_id,
                        FSInputFile(photo_path),
                        caption=caption,
                        reply_markup=persistent_keyboard if (send_keyboard and not remaining) else None
                    )
                    if remaining:
                        await asyncio.sleep(0.5)
                        await _send_text(remaining)
                    return
            except Exception as e:
                logger.warning(f"   ⚠️ Failed to send final photo by path: {e}")

        # 3) Text only
        if follow_up_text:
            await send_typing_action(self.bot, user_id, 0.6)
            await _send_text(follow_up_text)

    async def _send_lesson30_final_message_to_user(self, user_id: int, lesson_data: dict, send_keyboard: bool = True):
        """
        Отправляет финальное сообщение (follow_up) для урока 30 пользователю.
        Используется:
        - по нажатию кнопки "🎊 ФИНАЛЬНОЕ СООБЩЕНИЕ"
        - автоматически после отправки задания дня 30
        """
        follow_up_text = (lesson_data.get("follow_up_text", "") or "").strip()
        follow_up_photo_path = (lesson_data.get("follow_up_photo_path", "") or "").strip()
        follow_up_photo_file_id = (lesson_data.get("follow_up_photo_file_id", "") or "").strip()

        if not (follow_up_text or follow_up_photo_path or follow_up_photo_file_id):
            logger.warning("   ⚠️ Lesson 30 final message is empty (no text/photo).")
            return

        persistent_keyboard = self._create_persistent_keyboard() if send_keyboard else None

        CAPTION_LIMIT = 1024
        MAX_MESSAGE_LENGTH = 4000

        def split_caption(text: str):
            if not text:
                return "━━━━━━━━━━━━━━", None
            if len(text) <= CAPTION_LIMIT:
                return text, None
            cut = text.rfind("\n", 0, CAPTION_LIMIT)
            if cut < 900:
                cut = text.rfind(" ", 0, CAPTION_LIMIT)
            if cut < 900:
                cut = CAPTION_LIMIT
            return text[:cut].rstrip(), text[cut:].lstrip()

        async def send_text_parts(text: str):
            if not text or not text.strip():
                return
            if len(text) > MAX_MESSAGE_LENGTH:
                parts = self._split_long_message(text, MAX_MESSAGE_LENGTH)
                for part in parts[:-1]:
                    if part and part.strip():
                        await self.bot.send_message(user_id, part)
                        await asyncio.sleep(0.3)
                last_part = parts[-1]
                if last_part and last_part.strip():
                    await self.bot.send_message(user_id, last_part, reply_markup=persistent_keyboard)
                elif persistent_keyboard:
                    await self.bot.send_message(user_id, "\u200B", reply_markup=persistent_keyboard)
            else:
                await self.bot.send_message(user_id, text, reply_markup=persistent_keyboard)

        # 1) Photo by file_id
        if follow_up_photo_file_id:
            caption, remaining = split_caption(follow_up_text)
            await send_typing_action(self.bot, user_id, 0.6)
            await self.bot.send_photo(
                user_id,
                follow_up_photo_file_id,
                caption=caption,
                reply_markup=persistent_keyboard if (send_keyboard and not remaining) else None
            )
            if remaining:
                await asyncio.sleep(0.5)
                await send_text_parts(remaining)
            return

        # 2) Photo by path
        if follow_up_photo_path:
            try:
                from pathlib import Path
                from aiogram.types import FSInputFile
                import os

                normalized = follow_up_photo_path.replace("/", os.sep)
                photo_path = Path(normalized)
                if not photo_path.exists():
                    photo_path = Path.cwd() / normalized

                if photo_path.exists():
                    caption, remaining = split_caption(follow_up_text)
                    await send_typing_action(self.bot, user_id, 0.6)
                    await self.bot.send_photo(
                        user_id,
                        FSInputFile(photo_path),
                        caption=caption,
                        reply_markup=persistent_keyboard if (send_keyboard and not remaining) else None
                    )
                    if remaining:
                        await asyncio.sleep(0.5)
                        await send_text_parts(remaining)
                    return
            except Exception as e:
                logger.warning(f"   ⚠️ Failed to send final photo by path: {e}")

        # 3) Text only
        if follow_up_text:
            await send_typing_action(self.bot, user_id, 0.6)
            await send_text_parts(follow_up_text)
    
    async def _show_navigator(self, user_id: int, message_or_callback):
        """Показывает навигатор курса (вспомогательный метод)."""
        user = await self.user_service.get_user(user_id)
        
        if not user or not user.has_access():
            # Проверяем тип объекта для отправки сообщения
            if isinstance(message_or_callback, CallbackQuery):
                await message_or_callback.message.answer("❌ У вас нет доступа к этому курсу.")
            else:
                await message_or_callback.answer("❌ У вас нет доступа к этому курсу.")
            return
        
        # Получаем все доступные уроки
        all_lessons = self.lesson_loader.get_all_lessons()
        
        if not all_lessons:
            # Проверяем тип объекта для отправки сообщения
            if isinstance(message_or_callback, CallbackQuery):
                await message_or_callback.message.answer("❌ Уроки не загружены.", reply_markup=persistent_keyboard)
            else:
                await message_or_callback.answer("❌ Уроки не загружены.", reply_markup=persistent_keyboard)
            return
        
        # Создаем клавиатуру навигатора
        keyboard = create_navigator_keyboard(all_lessons, user.current_day)
        navigator_text = format_navigator_message()
        
        # Отправляем сообщение в зависимости от типа объекта
        persistent_keyboard = self._create_persistent_keyboard()
        if isinstance(message_or_callback, CallbackQuery):
            await message_or_callback.message.answer(navigator_text, reply_markup=keyboard)
            # Устанавливаем постоянную клавиатуру отдельным сообщением (используем невидимый символ)
            await message_or_callback.message.answer("\u200B", reply_markup=persistent_keyboard)
        else:
            await message_or_callback.answer(navigator_text, reply_markup=keyboard)
            # Устанавливаем постоянную клавиатуру отдельным сообщением (используем невидимый символ)
            await message_or_callback.answer("\u200B", reply_markup=persistent_keyboard)
        
        logger.info(f"🧭 Navigator opened by user {user_id}")
    
    async def handle_navigator(self, message: Message):
        """Handle /navigator command - show course navigator."""
        await self._show_navigator(message.from_user.id, message)
    
    async def handle_navigator_open(self, callback: CallbackQuery):
        """Handle navigator open button from lesson keyboard."""
        try:
            await callback.answer()
        except:
            pass
        await self._show_navigator(callback.from_user.id, callback)
    
    async def handle_navigator_lesson_select(self, callback: CallbackQuery):
        """Handle lesson selection from navigator."""
        try:
            await callback.answer()
        except:
            pass
        
        user_id = callback.from_user.id
        user = await self.user_service.get_user(user_id)
        
        persistent_keyboard = self._create_persistent_keyboard()
        
        if not user or not user.has_access():
            await callback.message.answer("❌ У вас нет доступа.", reply_markup=persistent_keyboard)
            return
        
        # Парсим номер урока из callback
        try:
            day = int(callback.data.split(":")[2])
        except (ValueError, IndexError):
            await callback.message.answer("❌ Ошибка: неверный номер урока.", reply_markup=persistent_keyboard)
            return
        
        logger.info(f"🧭 Navigator: lesson {day} selected by user {user_id}")
        
        # Проверяем, что lesson_loader инициализирован
        if not self.lesson_loader:
            logger.error("❌ LessonLoader не инициализирован!")
            await callback.message.answer(
                "❌ Ошибка: загрузчик уроков не инициализирован. Обратитесь в поддержку.",
                reply_markup=persistent_keyboard
            )
            return
        
        # Проверяем, что уроки загружены
        lesson_count = self.lesson_loader.get_lesson_count()
        if lesson_count == 0:
            logger.error(f"❌ Уроки не загружены! Всего уроков: {lesson_count}")
            await callback.message.answer(
                "❌ Ошибка: уроки не загружены. Обратитесь в поддержку.",
                reply_markup=persistent_keyboard
            )
            return
        
        logger.info(f"   📚 Загружено уроков: {lesson_count}, ищу урок {day}")
        
        # Загружаем урок из JSON
        lesson_data = self.lesson_loader.get_lesson(day)
        
        if not lesson_data:
            # Получаем список доступных уроков безопасным способом
            available_lessons = []
            try:
                all_lessons = self.lesson_loader.get_all_lessons()
                available_lessons = sorted([int(k) for k in all_lessons.keys() if k.isdigit()])
            except Exception as e:
                logger.error(f"Ошибка при получении списка уроков: {e}")
            
            logger.error(f"❌ Урок {day} не найден в JSON файле. Доступные уроки: {available_lessons}")
            await callback.message.answer(
                f"❌ Урок для дня {day} не найден.\n\n"
                f"Доступные уроки: {', '.join(map(str, available_lessons[:10]))}{'...' if len(available_lessons) > 10 else ''}" if available_lessons else f"Доступные уроки: 0-{lesson_count-1}",
                reply_markup=persistent_keyboard
            )
            return
        
        logger.info(f"   ✅ Урок {day} найден: {lesson_data.get('title', 'No title')}")
        
        # Отправляем урок БЕЗ intro_text и about_me_text (только основной контент)
        await send_typing_action(self.bot, user_id, 0.8)
        await self._send_lesson_from_json(user, lesson_data, day, skip_intro=True, skip_about_me=True)
        # Убеждаемся, что постоянная клавиатура видна после отправки урока
        await self._ensure_persistent_keyboard(user_id)
        logger.info(f"   ✅ Navigator lesson {day} sent successfully to user {user_id}")
    
    async def handle_navigator_close(self, callback: CallbackQuery):
        """Handle navigator close button."""
        try:
            await callback.answer("Навигатор закрыт")
            await callback.message.delete()
        except:
            pass
    
    async def handle_submit_assignment(self, callback: CallbackQuery):
        """Handle assignment submission button click."""
        await callback.answer()
        
        user_id = callback.from_user.id
        user = await self.user_service.get_user(user_id)
        
        if not user or not user.has_access():
            await callback.message.answer("❌ У вас нет доступа к этому курсу.")
            return
        
        # Парсим lesson_id из callback (формат: assignment:submit:lesson_1 или assignment:submit:1)
        try:
            callback_parts = callback.data.split(":")
            if len(callback_parts) >= 3:
                lesson_str = callback_parts[2]
                if lesson_str.startswith("lesson_"):
                    day_from_callback = int(lesson_str.replace("lesson_", ""))
                else:
                    day_from_callback = int(lesson_str)
            else:
                day_from_callback = user.current_day
        except (ValueError, IndexError):
            day_from_callback = user.current_day
        
        # Загружаем урок из JSON
        lesson_data = self.lesson_loader.get_lesson(day_from_callback)
        
        if not lesson_data:
            await callback.message.answer(f"❌ Урок для дня {day_from_callback} не найден.")
            return
        
        # Check if user can submit assignments (BASIC tariff cannot)
        if not user.can_receive_feedback():
            upgrade_keyboard = create_upgrade_tariff_keyboard()
            await callback.message.answer(
                "ℹ️ <b>Обратная связь не включена</b>\n\n"
                "📋 В вашем текущем тарифе (BASIC) задания не проверяются.\n\n"
                "✅ Вы можете выполнять задания для себя, "
                "но они не будут проверяться нашей командой 👥.\n\n"
                "⬆️ Для получения обратной связи обновитесь до тарифа FEEDBACK 💬.\n\n"
                "💬 Но вы можете обсудить задания в общем пространстве участников 👇",
                reply_markup=upgrade_keyboard
            )
            return
        
        # Получаем информацию об уроке из JSON
        lesson_data = self.lesson_loader.get_lesson(day_from_callback)
        lesson_title = lesson_data.get("title", f"День {day_from_callback}") if lesson_data else f"День {day_from_callback}"
        
        await callback.message.answer(
            f"📝 <b>Отправить задание для {lesson_title}</b>\n\n"
            f"✍️ Отправьте ваше задание текстом, фото 📸, видео 🎥 или документом 📄.\n\n"
            f"💬 <i>Можно отправить несколько сообщений. Напишите 'готово' ✅, когда закончите.</i>"
        )
    
    async def handle_ask_question(self, callback: CallbackQuery):
        """Handle question button click - immediately ready to receive question."""
        await callback.answer()
        
        user_id = callback.from_user.id
        user = await self.user_service.get_user(user_id)
        
        if not user or not user.has_access():
            await callback.message.answer("❌ У вас нет доступа к этому курсу.")
            return
        
        # Проверяем тариф - вопросы доступны только для FEEDBACK, PREMIUM, PRACTIC тарифов
        if user.tariff not in [Tariff.FEEDBACK, Tariff.PREMIUM, Tariff.PRACTIC]:
            upgrade_keyboard = create_upgrade_tariff_keyboard()
            await callback.message.answer(
                "ℹ️ <b>Вопросы доступны только для тарифа с обратной связью</b>\n\n"
                "📋 В вашем текущем тарифе (BASIC) функция задавать вопросы не включена.\n\n"
                "⬆️ Для возможности задавать вопросы обновитесь до тарифа FEEDBACK 💬.\n\n"
                "💬 Но вы можете обсудить вопросы в общем пространстве участников 👇",
                reply_markup=upgrade_keyboard
            )
            return
        
        # Парсим lesson_id из callback
        callback_parts = callback.data.split(":")
        if len(callback_parts) >= 3:
            lesson_str = callback_parts[2]
            if lesson_str.startswith("lesson_"):
                day_from_callback = int(lesson_str.replace("lesson_", ""))
            else:
                day_from_callback = int(lesson_str)
        else:
            day_from_callback = user.current_day
        
        # Сохраняем информацию о том, что пользователь задает вопрос по конкретному уроку
        # Используем временное хранилище (в продакшене можно использовать FSM или БД)
        if not hasattr(self, '_user_question_context'):
            self._user_question_context = {}
        self._user_question_context[user_id] = {
            'lesson_day': day_from_callback,
            'waiting_for_question': True
        }
        
        await callback.message.answer(
            f"❓ <b>Задать вопрос</b>\n\n"
            f"📚 Напишите ваш вопрос по уроку <b>День {day_from_callback}</b> прямо здесь 👇\n\n"
            f"✍️ Просто отправьте сообщение с вашим вопросом, и он сразу поступит кураторам.\n\n"
            f"👥 Наша команда ответит вам как можно скорее ⚡\n\n"
            f"💡 <i>Совет: Чем конкретнее вопрос, тем быстрее вы получите ответ! 🎯</i>"
        )
    
    async def _send_media_item(self, user_id: int, media_item: dict, day: int) -> bool:
        """
        Отправляет один медиа-файл (фото или видео) с анимацией и центрированием.
        
        Args:
            user_id: ID пользователя
            media_item: Словарь с данными медиа (type, file_id, path)
            day: Номер урока (для логирования)
        
        Returns:
            True если медиа успешно отправлено, False в противном случае
        """
        try:
            media_type = media_item.get("type", "photo")
            file_id = media_item.get("file_id")
            file_path = media_item.get("path")
            
            # Анимация: показываем, что бот работает (уменьшено для скорости)
            await send_typing_action(self.bot, user_id, 0.2)
            
            # Центрированная подпись с эмодзи-разделителями для визуального центрирования
            centered_caption = "━━━━━━━━━━━━━━"
            
            # Используем file_id если есть (самый быстрый способ)
            if file_id:
                if media_type == "photo":
                    await self.bot.send_photo(user_id, file_id, caption=centered_caption)
                elif media_type == "video":
                    # Для видео не указываем width/height, чтобы сохранить родные пропорции
                    # Урок 1 имеет специальную обработку в _send_lesson_from_json (не доходит до сюда)
                    # Для всех остальных видео (включая уроки 11 и 30) сохраняем пропорции
                    await self.bot.send_video(user_id, file_id, caption=centered_caption, supports_streaming=True)
                await asyncio.sleep(0.2)  # Минимальная пауза для стабильности
                return True
            
            # Fallback: загрузка с диска (только если нет file_id)
            if file_path:
                from pathlib import Path
                from aiogram.types import FSInputFile
                import os
                
                # Определяем корень проекта (кэшируем)
                if not hasattr(self, '_project_root_cache'):
                    possible_roots = [
                        Path.cwd(),
                        Path(__file__).parent.parent,
                    ]
                    self._project_root_cache = None
                    for root in possible_roots:
                        if (root / "Photo" / "video_pic").exists() or (root / "Photo" / "video_pic_optimized").exists():
                            self._project_root_cache = root
                            break
                    if not self._project_root_cache:
                        self._project_root_cache = Path.cwd()
                
                project_root = self._project_root_cache
                normalized_path = file_path.replace('/', os.sep).replace('\\', os.sep)
                
                # Пробуем сначала оптимизированную версию, потом оригинальную
                possible_paths = [
                    project_root / normalized_path,  # Указанный путь
                    project_root / normalized_path.replace('video_pic', 'video_pic_optimized'),  # Оптимизированная версия
                ]
                
                media_file = None
                for test_path in possible_paths:
                    if test_path.exists() and test_path.is_file():
                        media_file = FSInputFile(test_path)
                        break
                
                if media_file:
                    if media_type == "photo":
                        await self.bot.send_photo(user_id, media_file, caption=centered_caption)
                    elif media_type == "video":
                        # Для видео не указываем width/height, чтобы сохранить родные пропорции
                        # Урок 1 имеет специальную обработку в _send_lesson_from_json (не доходит до сюда)
                        # Для всех остальных видео (включая уроки 11 и 30) сохраняем пропорции
                        await self.bot.send_video(user_id, media_file, caption=centered_caption, supports_streaming=True)
                    await asyncio.sleep(0.2)  # Минимальная пауза для стабильности
                    return True
        except Exception as e:
            # Ошибка на одном медиа не прерывает урок
            logger.debug(f"   ⚠️ Медиа не отправлено для урока {day}: {e}")
            return False
        
        return False
    
    def _split_long_message(self, text: str, max_length: int = 4000) -> list:
        """
        Разбивает длинное сообщение на части, стараясь разрывать по абзацам.
        
        Args:
            text: Текст для разбивки
            max_length: Максимальная длина одной части
        
        Returns:
            Список частей сообщения
        """
        if len(text) <= max_length:
            return [text]
        
        parts = []
        current_part = ""
        
        # Разбиваем по абзацам (двойной перенос строки)
        paragraphs = text.split("\n\n")
        
        for paragraph in paragraphs:
            # Если текущая часть + новый абзац помещается
            if len(current_part) + len(paragraph) + 2 <= max_length:
                if current_part:
                    current_part += "\n\n" + paragraph
                else:
                    current_part = paragraph
            else:
                # Сохраняем текущую часть
                if current_part:
                    parts.append(current_part)
                    current_part = ""
                
                # Если абзац сам по себе длиннее лимита, разбиваем по строкам
                if len(paragraph) > max_length:
                    lines = paragraph.split("\n")
                    for line in lines:
                        if len(current_part) + len(line) + 1 <= max_length:
                            if current_part:
                                current_part += "\n" + line
                            else:
                                current_part = line
                        else:
                            if current_part:
                                parts.append(current_part)
                            current_part = line
                else:
                    current_part = paragraph
        
        # Добавляем последнюю часть (только если она не пустая)
        if current_part and current_part.strip():
            parts.append(current_part)
        
        # Фильтруем пустые части
        parts = [part for part in parts if part and part.strip()]
        
        # Если все части оказались пустыми, возвращаем исходный текст (хотя бы часть)
        if not parts:
            # Если исходный текст тоже пустой, возвращаем пробел
            if not text or not text.strip():
                parts = [" "]
            else:
                # Возвращаем хотя бы часть исходного текста
                parts = [text[:max_length] if len(text) > max_length else text]
        
        return parts if parts else [text]
    
    async def _safe_send_message(self, chat_id: int, text: str, reply_markup=None, **kwargs):
        """
        Безопасная отправка сообщения с проверкой на пустой текст.
        Фильтрует технические ошибки Telegram API.
        """
        if not text or not text.strip():
            logger.warning(f"⚠️ Attempted to send empty message to {chat_id}, using zero-width space")
            text = "\u200B"
        
        try:
            await self.bot.send_message(chat_id, text, reply_markup=reply_markup, **kwargs)
        except Exception as e:
            error_msg = str(e)
            # Фильтруем технические ошибки о пустых сообщениях
            if "text must be non-empty" in error_msg or "message text is empty" in error_msg:
                logger.warning(f"⚠️ Empty message error suppressed for {chat_id}: {error_msg}")
            else:
                raise
    
    async def _send_lesson_from_json(self, user: User, lesson_data: dict, day: int = None, skip_intro: bool = False, skip_about_me: bool = False):
        """
        Отправляет урок из JSON структуры пользователю.
        
        Args:
            user: Пользователь
            lesson_data: Данные урока из JSON
            day: Номер дня (если не указан, берется из user.current_day)
            skip_intro: Пропустить intro_text (для навигатора)
            skip_about_me: Пропустить блок "ОБО МНЕ" (для навигатора)
        """
        # Тяжёлое логирование стека сильно замедляет отправку уроков и раздувает логи.
        # Оставляем подробности только на DEBUG.
        logger.info(f"🔵 _send_lesson_from_json CALLED for day {day}, user {user.user_id}, skip_intro={skip_intro}, skip_about_me={skip_about_me}")
        if logger.isEnabledFor(logging.DEBUG):
            import traceback
            logger.debug(f"Call stack: {''.join(traceback.format_stack()[-3:-1])}")
        
        try:
            if day is None:
                day = user.current_day
            
            title = lesson_data.get("title", f"День {day}")
            text = lesson_data.get("text", "")
            
            # Получаем задание в зависимости от тарифа
            task = self.lesson_loader.get_task_for_tariff(day, user.tariff)
            
            # Формируем сообщение урока
            # Проверяем, есть ли вводный текст (intro_text) - для урока 22
            intro_text = lesson_data.get("intro_text", "")
            
            # Проверяем, есть ли фото для начала урока (для урока 30)
            intro_photo_file_id = lesson_data.get("intro_photo_file_id", "")
            intro_photo_path = lesson_data.get("intro_photo_path", "")
            
            # Отправляем фото в начале урока, если есть (для урока 30)
            if intro_photo_file_id or intro_photo_path:
                try:
                    # Анимация перед отправкой фото
                    await send_typing_action(self.bot, user.user_id, 0.4)
                    centered_caption = "━━━━━━━━━━━━━━"
                    
                    if intro_photo_file_id:
                        await self.bot.send_photo(user.user_id, intro_photo_file_id, caption=centered_caption)
                        logger.info(f"   ✅ Sent intro photo (file_id) for lesson {day}")
                    elif intro_photo_path:
                        from pathlib import Path
                        from aiogram.types import FSInputFile
                        photo_file = FSInputFile(Path(intro_photo_path))
                        await self.bot.send_photo(user.user_id, photo_file, caption=centered_caption)
                        logger.info(f"   ✅ Sent intro photo (file path) for lesson {day}")
                    await asyncio.sleep(0.6)  # Пауза для плавности
                except Exception as photo_error:
                    logger.warning(f"   ⚠️ Не удалось отправить intro photo для урока {day}: {photo_error}")
            
            # Получаем список медиа для урока
            media_list = lesson_data.get("media", [])
            
            # Для урока 0: извлекаем видео, чтобы отправить его с intro_text в caption
            lesson0_video_with_intro = None
            if (day == 0 or str(day) == "0") and media_list and intro_text:
                # Ищем первое видео в списке медиа
                for i, media_item in enumerate(media_list):
                    if media_item.get("type") == "video":
                        lesson0_video_with_intro = media_item
                        # Удаляем его из основного списка медиа
                        media_list = media_list[:i] + media_list[i+1:]
                        logger.info(f"   📹 Извлечено видео для урока 0 с intro_text, осталось медиа: {len(media_list)}")
                        break
            
            # Для урока 1: извлекаем видео, чтобы отправить его перед заданием
            lesson1_video_media = None
            if (day == 1 or str(day) == "1") and media_list:
                # Ищем видео в списке медиа
                for i, media_item in enumerate(media_list):
                    if media_item.get("type") == "video":
                        lesson1_video_media = media_item
                        # Удаляем его из основного списка медиа, чтобы не отправлять дважды
                        media_list = media_list[:i] + media_list[i+1:]
                        media_count = len(media_list)  # Обновляем количество медиа
                        logger.info(f"   📹 Извлечено видео для урока 1, осталось медиа: {len(media_list)}")
                        break
            
            # Для урока 30: извлекаем первое видео, чтобы отправить его перед заданием
            first_video_before_task = None
            if (day == 30 or str(day) == "30") and media_list:
                # Ищем первое видео в списке медиа
                for i, media_item in enumerate(media_list):
                    if media_item.get("type") == "video":
                        first_video_before_task = media_item
                        # Удаляем его из основного списка медиа
                        media_list = media_list[:i] + media_list[i+1:]
                        logger.info(f"   📹 Извлечено первое видео для урока 30, осталось медиа: {len(media_list)}")
                        break
            
            # Инициализируем индекс медиа для распределения
            # Пересчитываем media_count после возможного извлечения видео для урока 0 или 30
            media_index = 0
            media_count = len(media_list) if media_list else 0
            
            # Анимация: показываем, что бот работает
            await send_typing_action(self.bot, user.user_id, 0.6)
            
            # Формируем заголовок урока с анимационными эффектами
            lesson_message = (
                f"{create_premium_separator()}\n"
                f"✨ 📚 <b>{title}</b> 📚 ✨\n"
                f"{create_premium_separator()}\n\n"
            )
            
            # Отправляем заголовок урока
            await self.bot.send_message(user.user_id, lesson_message)
            await asyncio.sleep(0.5)  # Пауза для плавности
            
            # Для урока 0: отправляем видео с intro_text в caption сразу после заголовка
            lesson0_intro_sent_with_video = False
            if lesson0_video_with_intro and intro_text and not skip_intro:
                try:
                    await send_typing_action(self.bot, user.user_id, 0.4)
                    video_file_id = lesson0_video_with_intro.get("file_id")
                    video_file_path = lesson0_video_with_intro.get("path")
                    
                    # Центрированная подпись с intro_text
                    centered_caption = f"━━━━━━━━━━━━━━\n{intro_text}\n━━━━━━━━━━━━━━"
                    
                    if video_file_id:
                        await self.bot.send_video(user.user_id, video_file_id, caption=centered_caption)
                        logger.info(f"   ✅ Sent lesson 0 video with intro_text (file_id) for lesson {day}")
                    elif video_file_path:
                        from pathlib import Path
                        from aiogram.types import FSInputFile
                        import os
                        
                        # Определяем корень проекта
                        if not hasattr(self, '_project_root_cache'):
                            possible_roots = [
                                Path.cwd(),
                                Path(__file__).parent.parent,
                            ]
                            self._project_root_cache = None
                            for root in possible_roots:
                                if (root / "Photo" / "video_pic").exists() or (root / "Photo" / "video_pic_optimized").exists():
                                    self._project_root_cache = root
                                    break
                            if not self._project_root_cache:
                                self._project_root_cache = Path.cwd()
                        
                        normalized_path = video_file_path.replace('/', os.sep)
                        video_path = self._project_root_cache / normalized_path
                        if not video_path.exists():
                            video_path = Path(normalized_path)
                        
                        if video_path.exists():
                            video_file = FSInputFile(video_path)
                            await self.bot.send_video(user.user_id, video_file, caption=centered_caption)
                            logger.info(f"   ✅ Sent lesson 0 video with intro_text (file path: {video_path}) for lesson {day}")
                        else:
                            logger.error(f"   ❌ Lesson 0 video not found: {video_path.absolute()}")
                    else:
                        logger.error(f"   ❌ Lesson 0 video has no file_id or path")
                    
                    lesson0_intro_sent_with_video = True
                    await asyncio.sleep(0.6)
                except Exception as video_error:
                    logger.error(f"   ❌ Не удалось отправить видео урока 0 с intro_text: {video_error}", exc_info=True)
                    lesson0_intro_sent_with_video = False
            
            # ЛОГИКА РАЗМЕЩЕНИЯ МЕДИА:
            # Если медиа одно - размещаем сразу после заголовка
            # Если медиа несколько - распределяем по структуре урока
            # Исключение: для урока 0 видео уже отправлено с intro_text
            if media_count == 1 and not lesson0_video_with_intro:
                # Одно медиа - сразу после заголовка
                await self._send_media_item(user.user_id, media_list[0], day)
                logger.info(f"   ✅ Sent single media item after title for lesson {day}")
                media_index = 1  # Помечаем, что медиа отправлено
            elif media_count > 1 and not lesson0_video_with_intro:
                # Несколько медиа - распределяем по структуре урока
                # Первое медиа - сразу после заголовка
                await self._send_media_item(user.user_id, media_list[media_index], day)
                logger.info(f"   ✅ Sent media {media_index + 1}/{media_count} after title for lesson {day}")
                media_index += 1
            
            # Отправляем вводный текст отдельным сообщением, если есть (пропускаем для навигатора)
            # Для урока 0 intro_text уже отправлен с видео, поэтому пропускаем
            if intro_text and not skip_intro and not lesson0_intro_sent_with_video:
                # Анимация перед отправкой текста
                await send_typing_action(self.bot, user.user_id, 0.5)
                intro_message = f"{intro_text}\n\n{create_premium_separator()}\n\n"
                await self.bot.send_message(user.user_id, intro_message)
                logger.info(f"   Sent intro_text for lesson {day}")
                await asyncio.sleep(0.5)  # Пауза для плавности
                
                # Второе медиа - после intro_text (если есть несколько медиа)
                if media_count > 1 and media_index < media_count:
                    await self._send_media_item(user.user_id, media_list[media_index], day)
                    logger.info(f"   ✅ Sent media {media_index + 1}/{media_count} after intro_text for lesson {day}")
                    media_index += 1
            elif intro_text and skip_intro:
                logger.info(f"   Skipped intro_text for lesson {day} (navigator mode)")
            elif intro_text and lesson0_intro_sent_with_video:
                logger.info(f"   Skipped intro_text for lesson {day} (already sent with video)")
            
            # Отправляем "ОБО МНЕ" отдельным сообщением с фото (для урока 1) - сразу после intro_text (пропускаем для навигатора)
            about_me_text = lesson_data.get("about_me_text", "")
            about_me_photo_file_id = lesson_data.get("about_me_photo_file_id", "")
            about_me_photo_path = lesson_data.get("about_me_photo_path", "")
            
            logger.info(f"   Checking 'ОБО МНЕ' for lesson {day}: text={bool(about_me_text)}, file_id={bool(about_me_photo_file_id)}, path={bool(about_me_photo_path)}, skip={skip_about_me}")
            
            if about_me_text and not skip_about_me:
                await asyncio.sleep(0.5)  # Небольшая пауза
                
                # Флаг для отслеживания успешной отправки
                about_me_sent = False
                
                # Анимация перед отправкой фото
                await send_typing_action(self.bot, user.user_id, 0.4)
                
                # Пробуем отправить фото, если есть file_id (приоритет)
                if about_me_photo_file_id:
                    try:
                        # Центрированная подпись
                        centered_caption = f"━━━━━━━━━━━━━━\n{about_me_text}\n━━━━━━━━━━━━━━"
                        await self.bot.send_photo(
                            user.user_id,
                            about_me_photo_file_id,
                            caption=centered_caption
                        )
                        logger.info(f"   ✅ Sent 'ОБО МНЕ' photo (file_id) for lesson {day}")
                        about_me_sent = True
                    except Exception as photo_error:
                        logger.warning(f"   ⚠️ Не удалось отправить фото 'ОБО МНЕ' по file_id для урока {day}: {photo_error}")
                        # Если file_id не сработал, пробуем путь к файлу
                        if about_me_photo_path:
                            try:
                                from pathlib import Path
                                from aiogram.types import FSInputFile
                                photo_file = FSInputFile(Path(about_me_photo_path))
                                # Центрированная подпись
                                centered_caption = f"━━━━━━━━━━━━━━\n{about_me_text}\n━━━━━━━━━━━━━━"
                                await self.bot.send_photo(
                                    user.user_id,
                                    photo_file,
                                    caption=centered_caption
                                )
                                logger.info(f"   ✅ Sent 'ОБО МНЕ' photo (file path) for lesson {day}")
                                about_me_sent = True
                            except Exception as path_error:
                                logger.warning(f"   ⚠️ Не удалось отправить фото 'ОБО МНЕ' по пути для урока {day}: {path_error}")
                                # Отправляем только текст как fallback
                                await self.bot.send_message(user.user_id, about_me_text)
                                logger.info(f"   ✅ Sent 'ОБО МНЕ' text only (fallback) for lesson {day}")
                                about_me_sent = True
                        else:
                            # Отправляем только текст как fallback
                            await self.bot.send_message(user.user_id, about_me_text)
                            logger.info(f"   ✅ Sent 'ОБО МНЕ' text only (fallback) for lesson {day}")
                            about_me_sent = True
                # Если нет file_id, но есть путь к файлу
                elif about_me_photo_path and not about_me_sent:
                    try:
                        # Анимация перед отправкой фото
                        await send_typing_action(self.bot, user.user_id, 0.4)
                        from pathlib import Path
                        from aiogram.types import FSInputFile
                        photo_file = FSInputFile(Path(about_me_photo_path))
                        # Центрированная подпись
                        centered_caption = f"━━━━━━━━━━━━━━\n{about_me_text}\n━━━━━━━━━━━━━━"
                        await self.bot.send_photo(
                            user.user_id,
                            photo_file,
                            caption=centered_caption
                        )
                        logger.info(f"   ✅ Sent 'ОБО МНЕ' photo (file path) for lesson {day}")
                        about_me_sent = True
                    except Exception as path_error:
                        logger.warning(f"   ⚠️ Не удалось отправить фото 'ОБО МНЕ' по пути для урока {day}: {path_error}")
                        # Отправляем только текст как fallback
                        await self.bot.send_message(user.user_id, about_me_text)
                        logger.info(f"   ✅ Sent 'ОБО МНЕ' text only (fallback) for lesson {day}")
                        about_me_sent = True
                # Если нет фото вообще, отправляем только текст
                elif not about_me_sent:
                    await self.bot.send_message(user.user_id, about_me_text)
                    logger.info(f"   ✅ Sent 'ОБО МНЕ' text only for lesson {day}")
                    about_me_sent = True
            else:
                logger.warning(f"   ⚠️ No 'ОБО МНЕ' text found for lesson {day}")
            
            # Определяем, сколько медиа осталось для распределения по основному тексту
            remaining_media = media_count - media_index if media_count > media_index else 0
            
            # Если есть медиа для распределения по тексту, разбиваем текст на части
            if remaining_media > 0 and text:
                # Для урока 1: специальная логика - видео после текста "Наш корабль берёт курс"
                lesson1_video_placed = False
                lesson2_photo_placed = False
                
                # Для урока 2: специальная логика - картинка перед текстом "Кирпич нейтральный"
                if (day == 2 or str(day) == "2") and remaining_media == 1 and media_list:
                    # Ищем абзац с текстом "🧱 Кирпич\nнейтральный" или "Кирпич\nнейтральный"
                    paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
                    target_paragraph_index = None
                    
                    for i, paragraph in enumerate(paragraphs):
                        if ("🧱 Кирпич" in paragraph and "нейтральный" in paragraph) or \
                           ("Кирпич\nнейтральный" in paragraph) or \
                           ("🧱 Кирпич\nнейтральный" in paragraph):
                            target_paragraph_index = i
                            break
                    
                    if target_paragraph_index is not None:
                        # Отправляем все абзацы до целевого
                        for i in range(target_paragraph_index):
                            if paragraphs[i]:
                                await self.bot.send_message(user.user_id, paragraphs[i])
                                await asyncio.sleep(0.2)
                        
                        # Отправляем картинку перед целевым абзацем
                        await self._send_media_item(user.user_id, media_list[media_index], day)
                        logger.info(f"   ✅ Sent lesson 2 photo before target paragraph for lesson {day}")
                        media_index += 1
                        lesson2_photo_placed = True
                        await asyncio.sleep(0.3)
                        
                        # Отправляем целевой абзац после картинки
                        if paragraphs[target_paragraph_index]:
                            await self.bot.send_message(user.user_id, paragraphs[target_paragraph_index])
                            await asyncio.sleep(0.2)
                        
                        # Отправляем оставшиеся абзацы после целевого
                        for i in range(target_paragraph_index + 1, len(paragraphs)):
                            if paragraphs[i]:
                                await self.bot.send_message(user.user_id, paragraphs[i])
                                await asyncio.sleep(0.2)
                
                # Для урока 1: удаляем текст "Добро пожаловать на корвет" из основного текста, 
                # так как он будет отправлен с видео перед заданием
                if (day == 1 or str(day) == "1") and lesson1_video_media:
                    # Удаляем абзац с текстом "Добро пожаловать на корвет" из текста
                    paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
                    text_paragraphs = []
                    
                    for i, paragraph in enumerate(paragraphs):
                        if "Добро пожаловать на корвет" in paragraph:
                            # Текст будет отправлен с видео перед заданием, пропускаем его здесь
                            lesson1_video_placed = True
                            logger.info(f"   ✅ Removed 'Добро пожаловать' text from main text for lesson 1")
                        else:
                            text_paragraphs.append(paragraph)
                    
                    # Обновляем текст без абзаца "Добро пожаловать"
                    if text_paragraphs:
                        text = '\n\n'.join(text_paragraphs)
                
                # Если медиа урока 1 или 2 уже размещено, выходим из этой логики
                if lesson1_video_placed or lesson2_photo_placed:
                    # Отправляем оставшиеся медиа после последнего абзаца (если есть)
                    while media_index < media_count:
                        await self._send_media_item(user.user_id, media_list[media_index], day)
                        logger.info(f"   ✅ Sent remaining media {media_index + 1}/{media_count} after text for lesson {day}")
                        media_index += 1
                        await asyncio.sleep(0.3)
                else:
                    # Стандартная логика распределения медиа для всех остальных уроков
                    # Разбиваем текст на абзацы (по двойным переносам строк)
                    paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
                    
                    if len(paragraphs) > 0:
                        # Распределяем медиа равномерно по абзацам
                        # Вычисляем интервалы между медиа
                        if len(paragraphs) >= remaining_media:
                            # Если абзацев больше или равно медиа, распределяем равномерно
                            step = len(paragraphs) // (remaining_media + 1)
                            media_positions = [step * (i + 1) for i in range(remaining_media)]
                        else:
                            # Если абзацев меньше медиа, размещаем медиа после каждого абзаца
                            media_positions = list(range(1, len(paragraphs) + 1))[:remaining_media]
                        
                        # Отправляем абзацы с медиа между ними
                        for i, paragraph in enumerate(paragraphs):
                            # Отправляем абзац
                            if paragraph:
                                await self.bot.send_message(user.user_id, paragraph)
                                await asyncio.sleep(0.2)
                            
                            # Если наступила позиция для медиа, отправляем его
                            if (i + 1) in media_positions and media_index < media_count:
                                await self._send_media_item(user.user_id, media_list[media_index], day)
                                logger.info(f"   ✅ Sent media {media_index + 1}/{media_count} in text for lesson {day}")
                                media_index += 1
                                await asyncio.sleep(0.3)
                        
                        # Отправляем оставшиеся медиа после последнего абзаца (если есть)
                        while media_index < media_count:
                            await self._send_media_item(user.user_id, media_list[media_index], day)
                            logger.info(f"   ✅ Sent remaining media {media_index + 1}/{media_count} after text for lesson {day}")
                            media_index += 1
                            await asyncio.sleep(0.3)
                    else:
                        # Если нет абзацев, отправляем весь текст и медиа после него
                        if text.strip():
                            await self.bot.send_message(user.user_id, text)
                            await asyncio.sleep(0.3)
                        
                        # Отправляем все оставшиеся медиа
                        while media_index < media_count:
                            await self._send_media_item(user.user_id, media_list[media_index], day)
                            logger.info(f"   ✅ Sent remaining media {media_index + 1}/{media_count} after text for lesson {day}")
                            media_index += 1
                            await asyncio.sleep(0.3)
            else:
                # Если медиа нет или уже все отправлены, отправляем текст как обычно
                if text.strip():
                    # Анимация перед отправкой текста
                    await send_typing_action(self.bot, user.user_id, 0.5)
                    await self.bot.send_message(user.user_id, text)
                    await asyncio.sleep(0.5)  # Пауза для плавности
            
            # Для урока 19 отправляем кнопку "Показать все уровни" ПЕРЕД заданием
            if (day == 19 or str(day) == "19"):
                levels_images = lesson_data.get("levels_images", [])
                if levels_images:
                    # Анимация перед отправкой кнопки
                    await send_typing_action(self.bot, user.user_id, 0.4)
                    show_levels_keyboard = InlineKeyboardMarkup(inline_keyboard=[[
                        InlineKeyboardButton(
                            text="📊 Показать все уровни",
                            callback_data="lesson19_show_levels"
                        )
                    ]])
                    await self.bot.send_message(
                        user.user_id,
                        "📊 <b>Эмоциональные уровни</b>\n\nНажмите кнопку ниже, чтобы посмотреть все уровни:",
                        reply_markup=show_levels_keyboard,
                        parse_mode="HTML"
                    )
                    await asyncio.sleep(0.5)
                    logger.info(f"   ✅ Sent show levels button before task for lesson 19")
            
            # Для урока 1: отправляем видео с текстом ПЕРЕД заданием
            if (day == 1 or str(day) == "1") and lesson1_video_media:
                try:
                    # Анимация перед отправкой видео
                    await send_typing_action(self.bot, user.user_id, 0.5)
                    
                    # Текст для caption видео
                    video_caption = (
                        "⛵ Добро пожаловать на корвет, исследователи!\n\n"
                        "⛵🧭 Наш корабль берёт курс на новые горизонты. 🌊🗺️ "
                        "Но прежде чем отправиться, я задам вам первый ❓ вопрос. Даже три. ❓❓❓"
                    )
                    
                    # Отправляем видео с текстом
                    media_type = lesson1_video_media.get("type", "video")
                    file_id = lesson1_video_media.get("file_id")
                    file_path = lesson1_video_media.get("path")
                    
                    if file_id:
                        if media_type == "video":
                            # Отправляем видео без форсирования width/height:
                            # Telegram сам возьмёт реальные пропорции из файла и отрисует превью корректно.
                            await self._send_video_with_retry(
                                user.user_id,
                                file_id,
                                caption=video_caption,
                                supports_streaming=True
                            )
                        else:
                            await self.bot.send_photo(user.user_id, file_id, caption=video_caption)
                    elif file_path:
                        from pathlib import Path
                        from aiogram.types import FSInputFile
                        import os
                        
                        # Определяем корень проекта
                        project_root = None
                        possible_roots = [
                            Path.cwd(),
                            Path(__file__).parent.parent,
                        ]
                        for root in possible_roots:
                            if (root / "Photo" / "video_pic").exists() or (root / "Photo" / "video_pic_optimized").exists():
                                project_root = root
                                break
                        if not project_root:
                            project_root = Path.cwd()
                        
                        # Нормализуем путь
                        if os.path.isabs(file_path):
                            media_file = Path(file_path)
                        else:
                            media_file = project_root / file_path
                        
                        if media_file.exists():
                            media_input = FSInputFile(media_file)
                            if media_type == "video":
                                # Отправляем видео без форсирования width/height (см. выше)
                                await self._send_video_with_retry(
                                    user.user_id,
                                    media_input,
                                    caption=video_caption,
                                    supports_streaming=True
                                )
                            else:
                                await self.bot.send_photo(user.user_id, media_input, caption=video_caption)
                    
                    logger.info(f"   ✅ Sent lesson 1 video with text before task")
                    await asyncio.sleep(0.5)
                except Exception as video_error:
                    logger.warning(f"   ⚠️ Не удалось отправить видео урока 1 перед заданием: {video_error}")
            
            # Для урока 30 отправляем первое видео ПЕРЕД заданием
            if first_video_before_task:
                try:
                    # Анимация перед отправкой видео
                    await send_typing_action(self.bot, user.user_id, 0.5)
                    await self._send_media_item(user.user_id, first_video_before_task, day)
                    logger.info(f"   ✅ Sent first video before task for lesson 30")
                    await asyncio.sleep(0.5)
                except Exception as video_error:
                    logger.warning(f"   ⚠️ Не удалось отправить первое видео перед заданием для урока 30: {video_error}")
            
            # Формируем сообщение с заданием
            task_message = ""
            if task:
                # Анимация перед отправкой задания
                await send_typing_action(self.bot, user.user_id, 0.6)
                task_message = (
                    f"{create_premium_separator()}\n\n"
                    f"✨ 📝 <b>Задание:</b> 📝 ✨\n"
                    f"{create_premium_separator()}\n\n"
                    f"{task}\n\n"
                )
            
            # Отправляем задание, если есть
            if task_message:
                # Передаем day в lesson_data для создания клавиатуры
                lesson_data_with_day = lesson_data.copy()
                lesson_data_with_day["day_number"] = day
                logger.info(f"   📝 Creating keyboard for task message, day={day} (type={type(day).__name__})")
                keyboard = create_lesson_keyboard_from_json(lesson_data_with_day, user, Config.GENERAL_GROUP_ID)
                logger.info(f"   ✅ Keyboard created: {len(keyboard.inline_keyboard) if keyboard and hasattr(keyboard, 'inline_keyboard') else 0} button rows")
                if day == 30:
                    logger.info(f"   🎊 Lesson 30: Keyboard should contain FINAL MESSAGE button")
                
                # Для урока 21 добавляем кнопку "Скачать карточки"
                if day == 21 or str(day) == "21":
                    cards = lesson_data.get("cards", [])
                    logger.info(f"   🔍 Lesson 21 (with task): cards found={len(cards) if cards else 0}")
                    if cards:
                        # Создаем кнопку для скачивания карточек
                        download_button = [
                            InlineKeyboardButton(
                                text="📥 Скачать карточки",
                                callback_data="lesson21_download_cards"
                            )
                        ]
                        
                        # Добавляем кнопку к существующей клавиатуре
                        if keyboard and hasattr(keyboard, 'inline_keyboard') and keyboard.inline_keyboard:
                            keyboard.inline_keyboard.append(download_button)
                            logger.info(f"   ✅ Added download button to existing keyboard for lesson 21")
                        else:
                            # Если клавиатуры нет, создаем новую
                            keyboard = InlineKeyboardMarkup(inline_keyboard=[download_button])
                            logger.info(f"   ✅ Created new keyboard with download button for lesson 21")
                
                logger.info(f"   Sending task message to user {user.user_id}, day {day}")
                logger.info(f"   Task message length: {len(task_message)} characters")
                
                # Разбиваем длинные сообщения на части (лимит Telegram: 4096 символов)
                MAX_MESSAGE_LENGTH = 4000  # Оставляем запас
                if len(task_message) > MAX_MESSAGE_LENGTH:
                    # Разбиваем сообщение на части
                    message_parts = self._split_long_message(task_message, MAX_MESSAGE_LENGTH)
                    logger.info(f"   Task message split into {len(message_parts)} parts")
                    
                    # Отправляем все части кроме последней без клавиатуры
                    for i, part in enumerate(message_parts[:-1], 1):
                        # Пропускаем пустые части
                        if part and part.strip():
                            await self.bot.send_message(user.user_id, part)
                            await asyncio.sleep(0.3)  # Небольшая пауза между сообщениями
                            logger.info(f"   Sent task part {i}/{len(message_parts)}")
                        else:
                            logger.warning(f"   Skipped empty task part {i}/{len(message_parts)}")
                    
                    # Отправляем последнюю часть с клавиатурой (проверяем, что она не пустая)
                    last_part = message_parts[-1]
                    if last_part and last_part.strip():
                        await self.bot.send_message(user.user_id, last_part, reply_markup=keyboard)
                        logger.info(f"   Sent last task part {len(message_parts)}/{len(message_parts)} with keyboard")
                    else:
                        # Если последняя часть пустая, отправляем только клавиатуру с невидимым символом
                        logger.warning(f"   Last task part is empty, sending keyboard only")
                        await self.bot.send_message(user.user_id, "\u200B", reply_markup=keyboard)
                else:
                    # Если сообщение короткое, отправляем как есть
                    await self.bot.send_message(user.user_id, task_message, reply_markup=keyboard)
            else:
                # Если задания нет, отправляем только клавиатуру
                lesson_data_with_day = lesson_data.copy()
                lesson_data_with_day["day_number"] = day
                logger.info(f"   📝 Creating keyboard (no task), day={day} (type={type(day).__name__})")
                keyboard = create_lesson_keyboard_from_json(lesson_data_with_day, user, Config.GENERAL_GROUP_ID)
                logger.info(f"   ✅ Keyboard created: {len(keyboard.inline_keyboard) if keyboard and hasattr(keyboard, 'inline_keyboard') else 0} button rows")
                if day == 30:
                    logger.info(f"   🎊 Lesson 30: Keyboard should contain FINAL MESSAGE button")
                
                # Для урока 21 добавляем кнопку "Скачать карточки"
                cards = []
                if day == 21 or str(day) == "21":
                    cards = lesson_data.get("cards", [])
                    logger.info(f"   🔍 Lesson 21 (no task): cards found={len(cards) if cards else 0}, day={day}, type={type(day)}")
                    if cards:
                        # Создаем кнопку для скачивания карточек
                        download_button = [
                            InlineKeyboardButton(
                                text="📥 Скачать карточки",
                                callback_data="lesson21_download_cards"
                            )
                        ]
                        
                        # Всегда создаем новую клавиатуру для урока 21 с кнопкой скачивания
                        keyboard = InlineKeyboardMarkup(inline_keyboard=[download_button])
                        logger.info(f"   ✅ Created keyboard with download button for lesson 21")
                    else:
                        logger.warning(f"   ⚠️ No cards found for lesson 21")
                
                # Для урока 19 добавляем кнопку "Показать все уровни"
                levels_images = []
                if day == 19 or str(day) == "19":
                    levels_images = lesson_data.get("levels_images", [])
                    logger.info(f"   🔍 Lesson 19 (no task): levels_images found={len(levels_images) if levels_images else 0}, day={day}, type={type(day)}")
                    if levels_images:
                        # Создаем кнопку для показа всех уровней
                        show_levels_button = [
                            InlineKeyboardButton(
                                text="📊 Показать все уровни",
                                callback_data="lesson19_show_levels"
                            )
                        ]
                        
                        # Если клавиатуры нет, создаем новую, иначе добавляем к существующей
                        if keyboard and hasattr(keyboard, 'inline_keyboard') and keyboard.inline_keyboard and len(keyboard.inline_keyboard) > 0:
                            keyboard.inline_keyboard.append(show_levels_button)
                            logger.info(f"   ✅ Added show levels button to existing keyboard for lesson 19")
                        else:
                            keyboard = InlineKeyboardMarkup(inline_keyboard=[show_levels_button])
                            logger.info(f"   ✅ Created keyboard with show levels button for lesson 19")
                    else:
                        logger.warning(f"   ⚠️ No levels_images found for lesson 19")
                
                # Отправляем сообщение с клавиатурой
                # Для урока 21 всегда отправляем клавиатуру, если есть карточки
                if (day == 21 or str(day) == "21") and cards:
                    await self.bot.send_message(
                        user.user_id, 
                        "📥 <b>Карточки игры «Телепат»</b>\n\nНажмите кнопку ниже, чтобы скачать все карточки:",
                        reply_markup=keyboard,
                        parse_mode="HTML"
                    )
                    logger.info(f"   ✅ Sent message with download button for lesson 21")
                elif (day == 19 or str(day) == "19") and levels_images:
                    await self.bot.send_message(
                        user.user_id, 
                        "📊 <b>Эмоциональные уровни</b>\n\nНажмите кнопку ниже, чтобы посмотреть все уровни:",
                        reply_markup=keyboard,
                        parse_mode="HTML"
                    )
                    logger.info(f"   ✅ Sent message with show levels button for lesson 19")
                elif keyboard and hasattr(keyboard, 'inline_keyboard') and keyboard.inline_keyboard and len(keyboard.inline_keyboard) > 0:
                    await self.bot.send_message(user.user_id, "\u200B", reply_markup=keyboard)
                    logger.info(f"   ✅ Sent message with keyboard for lesson {day}")
                else:
                    # Если клавиатуры нет, отправляем только невидимый символ
                    await self.bot.send_message(user.user_id, "\u200B")
                    logger.info(f"   ℹ️ No keyboard to send for lesson {day}")
            
            # Всегда устанавливаем постоянную клавиатуру после отправки урока
            # Используем невидимый символ вместо пустого сообщения
            persistent_keyboard = self._create_persistent_keyboard()
            await self.bot.send_message(user.user_id, "\u200B", reply_markup=persistent_keyboard)
            
            # Отправляем follow_up_text в конце урока, если есть (для урока 30)
            # ВАЖНО: Для дня 30 это ОБЯЗАТЕЛЬНО должно быть отправлено
            logger.info(f"   🔍 [FOLLOW_UP] Starting follow_up check for lesson {day}")
            follow_up_text = lesson_data.get("follow_up_text", "")
            follow_up_photo_path = lesson_data.get("follow_up_photo_path", "")
            follow_up_photo_file_id = lesson_data.get("follow_up_photo_file_id", "")
            
            logger.info(f"   🔍 [FOLLOW_UP] Checking follow_up for lesson {day}:")
            logger.info(f"      - follow_up_text exists: {bool(follow_up_text)} (length: {len(follow_up_text) if follow_up_text else 0})")
            logger.info(f"      - follow_up_text preview: '{follow_up_text[:100] if follow_up_text else 'None'}...'")
            logger.info(f"      - follow_up_photo_path: '{follow_up_photo_path}'")
            logger.info(f"      - follow_up_photo_file_id exists: {bool(follow_up_photo_file_id)}")
            
            # Для урока 30 follow_up отправляется ПОСЛЕ задания, а не здесь
            # Явная проверка для дня 30 или если есть любой из компонентов (но не для дня 30)
            should_send_follow_up = (day != 30) and (follow_up_text or follow_up_photo_path or follow_up_photo_file_id)
            
            logger.info(f"   🔍 [FOLLOW_UP] should_send_follow_up = {should_send_follow_up} (day={day}, day==30={day==30})")
            
            if should_send_follow_up:
                logger.info(f"   ✅ Will send follow_up for lesson {day}")
                await asyncio.sleep(1)  # Небольшая пауза перед отправкой
                persistent_keyboard = self._create_persistent_keyboard()
                
                # Отправляем фото перед текстом, если есть
                photo_sent = False
                if follow_up_photo_file_id:
                    try:
                        # Анимация перед отправкой фото
                        await send_typing_action(self.bot, user.user_id, 0.5)
                        centered_caption = "━━━━━━━━━━━━━━"
                        await self.bot.send_photo(user.user_id, follow_up_photo_file_id, caption=centered_caption)
                        logger.info(f"   ✅ Sent follow_up photo (file_id) for lesson {day}")
                        photo_sent = True
                        await asyncio.sleep(0.7)  # Пауза для плавности
                    except Exception as photo_error:
                        logger.error(f"   ❌ Не удалось отправить follow_up photo (file_id) для урока {day}: {photo_error}", exc_info=True)
                
                elif follow_up_photo_path:
                    try:
                        from pathlib import Path
                        from aiogram.types import FSInputFile
                        import os
                        
                        # Нормализуем путь (заменяем прямые слеши на обратные для Windows)
                        normalized_path = follow_up_photo_path.replace('/', os.sep)
                        
                        # Пробуем относительный путь от текущей рабочей директории
                        photo_path = Path(normalized_path)
                        if not photo_path.exists():
                            # Пробуем от корня проекта (где находится run_all_bots.py)
                            project_root = Path.cwd()
                            photo_path = project_root / normalized_path
                        
                        logger.info(f"   📷 Trying to send follow_up photo from: {photo_path.absolute()} (exists: {photo_path.exists()})")
                        
                        if photo_path.exists():
                            # Анимация перед отправкой фото
                            await send_typing_action(self.bot, user.user_id, 0.5)
                            photo_file = FSInputFile(photo_path)
                            centered_caption = "━━━━━━━━━━━━━━"
                            await self.bot.send_photo(user.user_id, photo_file, caption=centered_caption)
                            logger.info(f"   ✅ Sent follow_up photo (file path: {photo_path}) for lesson {day}")
                            photo_sent = True
                            await asyncio.sleep(0.7)  # Пауза для плавности
                        else:
                            logger.error(f"   ❌ Follow-up photo not found: {photo_path.absolute()} (original path: {follow_up_photo_path})")
                            # Пробуем найти файл в других местах
                            possible_paths = [
                                Path("Photo/30/photo_5377557667917794132_y.jpg"),
                                Path("Photo/30/photo_5404715149857328372_y.jpg"),
                                Path("Photo/photo_5377557667917794132_y.jpg"),
                                Path("Photo/photo_5404715149857328372_y.jpg"),
                                Path.cwd() / "Photo" / "30" / "photo_5377557667917794132_y.jpg",
                                Path.cwd() / "Photo" / "30" / "photo_5404715149857328372_y.jpg",
                                Path.cwd() / "Photo" / "photo_5377557667917794132_y.jpg",
                                Path.cwd() / "Photo" / "photo_5404715149857328372_y.jpg",
                            ]
                            for possible_path in possible_paths:
                                if possible_path.exists():
                                    logger.info(f"   🔍 Found photo at alternative path: {possible_path.absolute()}")
                                    # Анимация перед отправкой фото
                                    await send_typing_action(self.bot, user.user_id, 0.5)
                                    photo_file = FSInputFile(possible_path)
                                    centered_caption = "━━━━━━━━━━━━━━"
                                    await self.bot.send_photo(user.user_id, photo_file, caption=centered_caption)
                                    logger.info(f"   ✅ Sent follow_up photo from alternative path for lesson {day}")
                                    photo_sent = True
                                    await asyncio.sleep(0.7)
                                    break
                    except Exception as photo_error:
                        logger.error(f"   ❌ Не удалось отправить follow_up photo (file path) для урока {day}: {photo_error}", exc_info=True)
                
                # Отправляем текст после фото (или без фото, если фото нет)
                if follow_up_text and follow_up_text.strip():
                    try:
                        # Анимация перед отправкой текста
                        await send_typing_action(self.bot, user.user_id, 0.7)
                        logger.info(f"   📤 Sending follow_up_text for lesson {day} (length: {len(follow_up_text)} chars)")
                        await self.bot.send_message(user.user_id, follow_up_text, reply_markup=persistent_keyboard)
                        logger.info(f"   ✅ Successfully sent follow_up_text for lesson {day}")
                    except Exception as text_error:
                        error_msg = str(text_error)
                        logger.error(f"   ❌ Error sending follow_up_text for lesson {day}: {error_msg}", exc_info=True)
                        # Фильтруем технические ошибки о пустых сообщениях
                        if "text must be non-empty" in error_msg or "message text is empty" in error_msg:
                            logger.warning(f"   ⚠️ Empty follow_up_text for lesson {day} (suppressed)")
                        else:
                            # Пробуем отправить еще раз без клавиатуры
                            try:
                                await self.bot.send_message(user.user_id, follow_up_text)
                                logger.info(f"   ✅ Sent follow_up_text without keyboard for lesson {day}")
                            except Exception as retry_error:
                                logger.error(f"   ❌ Retry also failed for lesson {day}: {retry_error}")
                elif not photo_sent:
                    # Если нет ни текста, ни фото, но мы должны что-то отправить
                    logger.warning(f"   ⚠️ No follow_up_text or photo to send for lesson {day}")
            else:
                # Для дня 30 финальное сообщение отправляется по кнопке, не автоматически
                logger.info(f"   ⚠️ No follow_up content for lesson {day}")
            
            logger.info(f"✅ Урок {day} отправлен пользователю {user.user_id}")
            
        except Exception as e:
            error_msg = str(e)
            # Фильтруем технические ошибки Telegram API о пустых сообщениях
            if "text must be non-empty" in error_msg or "message text is empty" in error_msg:
                logger.warning(f"⚠️ Empty message error for lesson {day}, user {user.user_id} (suppressed): {error_msg}")
            else:
                logger.error(f"❌ Ошибка при отправке урока пользователю {user.user_id}: {e}", exc_info=True)
                # Не пробрасываем ошибку дальше, чтобы не прерывать работу бота
    
    async def handle_assignment_text(self, message: Message):
        """Handle assignment text submission."""
        user_id = message.from_user.id
        user = await self.user_service.get_user(user_id)
        
        if not user or not user.has_access():
            return
        
        # Check if this is assignment submission context
        # Загружаем урок из JSON для текущего дня пользователя
        lesson_data = self.lesson_loader.get_lesson(user.current_day)
        if not lesson_data:
            # Если нет урока в JSON, проверяем через сервис
            lesson = await self.lesson_service.get_user_current_lesson(user)
            if not lesson or not lesson.has_assignment():
                return
        else:
            # Проверяем наличие задания в JSON
            task = self.lesson_loader.get_task_for_tariff(user.current_day, user.tariff)
            if not task:
                return
        
        # Check if user can receive feedback
        if not user.can_receive_feedback():
            upgrade_keyboard = create_upgrade_tariff_keyboard()
            await message.answer(
                "ℹ️ <b>Обратная связь не включена</b>\n\n"
                "📋 В вашем текущем тарифе (BASIC) задания не проверяются.\n\n"
                "✅ Вы можете выполнять задания для себя, "
                "но они не будут проверяться нашей командой 👥.\n\n"
                "⬆️ Для получения обратной связи обновитесь до тарифа FEEDBACK 💬.\n\n"
                "💬 Но вы можете обсудить задания в общем пространстве участников 👇",
                reply_markup=upgrade_keyboard
            )
            return
        
        # Получаем информацию об уроке для отправки админу (если еще не получено выше)
        if not lesson_data:
            lesson_data = self.lesson_loader.get_lesson(user.current_day)
        lesson_title = lesson_data.get("title", f"День {user.current_day}") if lesson_data else f"День {user.current_day}"
        
        # Submit assignment
        # Создаем временный объект Lesson для совместимости с сервисом
        from core.models import Lesson
        from datetime import datetime
        task = self.lesson_loader.get_task_for_tariff(user.current_day, user.tariff) if lesson_data else ""
        temp_lesson = Lesson(
            lesson_id=user.current_day,  # Используем day_number как lesson_id
            day_number=user.current_day,
            title=lesson_title,
            content_text="",
            assignment_text=task or "",
            image_url=None,
            video_url=None,
            created_at=datetime.now()
        )
        
        assignment = await self.assignment_service.submit_assignment(
            user=user,
            lesson=temp_lesson,
            submission_text=message.text
        )
        
        # Forward to admin
        admin_text = (
            f"📝 <b>Новое задание</b>\n\n"
            f"👤 Пользователь: {user.first_name} (@{user.username or 'Не указано'})\n"
            f"🆔 ID пользователя: {user.user_id}\n"
            f"📚 Урок: {lesson_title}\n"
            f"🔢 ID задания: {assignment.assignment_id}\n\n"
            f"✍️ <b>Ответ:</b>\n{message.text}"
        )
        
        await self.bot.send_message(
            Config.ADMIN_CHAT_ID,
            admin_text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=f"💬 Ответить пользователю",
                        callback_data=f"admin_reply:{assignment.assignment_id}"
                    )
                ]
            ])
        )
        
        persistent_keyboard = self._create_persistent_keyboard()
        await message.answer(
            "✅ <b>Задание отправлено!</b>\n\n"
            "📤 Ваше задание отправлено нашей команде на проверку 👥.\n"
            "⏳ Вы получите обратную связь в ближайшее время 💬.",
            reply_markup=persistent_keyboard
        )
        
        # Отправляем follow_up_text для урока 0 после отправки задания
        if user.current_day == 0:
            lesson_data = self.lesson_loader.get_lesson(0)
            if lesson_data and lesson_data.get("follow_up_text"):
                await asyncio.sleep(1)  # Небольшая пауза перед отправкой
                await message.answer(lesson_data["follow_up_text"], reply_markup=persistent_keyboard)
        
        # Для урока 30 финальное сообщение теперь отправляется по кнопке "ФИНАЛЬНОЕ СООБЩЕНИЕ", а не автоматически
    
    async def handle_assignment_media(self, message: Message):
        """Handle assignment media submission (photos, videos, documents)."""
        user_id = message.from_user.id
        user = await self.user_service.get_user(user_id)
        
        if not user or not user.has_access():
            return
        
        # Проверяем наличие задания в JSON
        lesson_data = self.lesson_loader.get_lesson(user.current_day)
        if not lesson_data:
            lesson = await self.lesson_service.get_user_current_lesson(user)
            if not lesson or not lesson.has_assignment():
                return
        else:
            task = self.lesson_loader.get_task_for_tariff(user.current_day, user.tariff)
            if not task:
                return
        
        if not user.can_receive_feedback():
            upgrade_keyboard = create_upgrade_tariff_keyboard()
            await message.answer(
                "ℹ️ <b>Ваши медиа отмечены</b>\n\n"
                "📋 Обратная связь не включена в ваш текущий тариф.\n\n"
                "✅ Вы можете выполнять задания для себя, "
                "но они не будут проверяться нашей командой 👥.\n\n"
                "⬆️ Для получения обратной связи обновитесь до тарифа FEEDBACK 💬.",
                reply_markup=upgrade_keyboard
            )
            return
        
        # Получаем информацию об уроке
        lesson_title = lesson_data.get("title", f"День {user.current_day}") if lesson_data else f"День {user.current_day}"
        
        # Collect media file IDs
        media_ids = []
        if message.photo:
            media_ids.append(f"photo:{message.photo[-1].file_id}")
        elif message.video:
            media_ids.append(f"video:{message.video.file_id}")
        elif message.document:
            media_ids.append(f"document:{message.document.file_id}")
        
        # Создаем временный объект Lesson для совместимости
        from core.models import Lesson
        from datetime import datetime
        task = self.lesson_loader.get_task_for_tariff(user.current_day, user.tariff) if lesson_data else ""
        temp_lesson = Lesson(
            lesson_id=user.current_day,  # Используем day_number как lesson_id (int)
            day_number=user.current_day,
            title=lesson_title,
            content_text="",
            assignment_text=task or "",
            image_url=None,
            video_url=None,
            created_at=datetime.now()
        )
        
        # Submit assignment
        assignment = await self.assignment_service.submit_assignment(
            user=user,
            lesson=temp_lesson,
            submission_text=message.caption or "[Медиа файл]",
            submission_media_ids=media_ids
        )
        
        # Forward to admin
        admin_text = (
            f"📝 <b>Новое задание (Медиа)</b>\n\n"
            f"👤 Пользователь: {user.first_name} (@{user.username or 'Не указано'})\n"
            f"🆔 ID пользователя: {user.user_id}\n"
            f"📚 Урок: {lesson_title}\n"
            f"🔢 ID задания: {assignment.assignment_id}"
        )
        
        if message.caption:
            admin_text += f"\n\n✍️ <b>Подпись:</b>\n{message.caption}"
        
        # Forward media to admin
        if message.photo:
            await self.bot.send_photo(Config.ADMIN_CHAT_ID, message.photo[-1].file_id, caption=admin_text)
        elif message.video:
            await self.bot.send_video(Config.ADMIN_CHAT_ID, message.video.file_id, caption=admin_text)
        elif message.document:
            await self.bot.send_document(Config.ADMIN_CHAT_ID, message.document.file_id, caption=admin_text)
        
        persistent_keyboard = self._create_persistent_keyboard()
        await message.answer(
            "✅ <b>Задание отправлено!</b>\n\n"
            "📤 Ваше задание отправлено нашей команде на проверку 👥.\n"
            "⏳ Вы получите обратную связь в ближайшее время 💬.",
            reply_markup=persistent_keyboard
        )
        
        # Отправляем follow_up_text для урока 0 после отправки задания (медиа)
        if user.current_day == 0:
            lesson_data = self.lesson_loader.get_lesson(0)
            if lesson_data and lesson_data.get("follow_up_text"):
                await asyncio.sleep(1)  # Небольшая пауза перед отправкой
                await message.answer(lesson_data["follow_up_text"], reply_markup=persistent_keyboard)
    
    async def handle_question_text(self, message: Message):
        """Handle question text submission."""
        user_id = message.from_user.id
        user = await self.user_service.get_user(user_id)
        
        if not user or not user.has_access():
            return
        
        # Проверяем тариф - вопросы доступны только для FEEDBACK, PREMIUM, PRACTIC тарифов
        if user.tariff not in [Tariff.FEEDBACK, Tariff.PREMIUM, Tariff.PRACTIC]:
            # Не отвечаем на сообщения от пользователей с базовым тарифом
            return
        
        # Проверяем, ожидаем ли мы вопрос от этого пользователя
        waiting_for_question = False
        lesson_day = user.current_day
        
        if hasattr(self, '_user_question_context') and user_id in self._user_question_context:
            context = self._user_question_context[user_id]
            if context.get('waiting_for_question'):
                waiting_for_question = True
                lesson_day = context.get('lesson_day', user.current_day)
                # Удаляем контекст после обработки
                del self._user_question_context[user_id]
        
        # Если не ожидаем вопрос, проверяем, не является ли это заданием
        if not waiting_for_question:
            lesson_data = self.lesson_loader.get_lesson(user.current_day)
            task = self.lesson_loader.get_task_for_tariff(user.current_day, user.tariff) if lesson_data else None
            
            if task:
                # Если есть задание, это может быть задание, а не вопрос
                return
        
        # Обрабатываем как вопрос
        lesson_id = lesson_day if lesson_day else None
        question_data = await self.question_service.create_question(
            user_id=user_id,
            lesson_id=lesson_id,
            question_text=message.text,
            context=f"День {lesson_day}" if lesson_day else None
        )
        
        # Форматируем вопрос для кураторов
        curator_message = await self.question_service.format_question_for_admin(question_data)
        
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
                                callback_data=f"curator_reply:{user_id}:{lesson_day}"
                            )
                        ]
                    ])
                )
                logger.info(f"✅ Question sent to curator group from user {user_id}")
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
                                    callback_data=f"curator_reply:{user_id}:{lesson_day}"
                                )
                            ]
                        ])
                    )
        else:
            logger.warning("⚠️ No curator group or admin chat configured!")
        
        persistent_keyboard = self._create_persistent_keyboard()
        persistent_keyboard = self._create_persistent_keyboard()
        await message.answer(
            "✅ <b>Вопрос отправлен!</b>\n\n"
            "📤 Ваш вопрос отправлен кураторам 👥.\n"
            "⏳ Мы ответим вам как можно скорее 💬.",
            reply_markup=persistent_keyboard
        )
    
    async def handle_admin_reply(self, callback: CallbackQuery):
        """Handle admin reply button click."""
        await callback.answer()
        
        assignment_id = int(callback.data.split(":")[1])
        assignment = await self.assignment_service.get_assignment(assignment_id)
        
        if not assignment:
            await callback.message.answer("❌ Assignment not found.")
            return
        
        await callback.message.answer(
            f"💬 <b>Reply to Assignment</b>\n\n"
            f"Assignment ID: {assignment_id}\n"
            f"User ID: {assignment.user_id}\n"
            f"Lesson: Day {assignment.day_number}\n\n"
            f"Reply to this message with your feedback."
        )
    
    async def handle_curator_reply(self, callback: CallbackQuery):
        """Handle curator reply button click for questions."""
        await callback.answer()
        
        try:
            # Парсим user_id и lesson_day из callback
            parts = callback.data.split(":")
            if len(parts) >= 3:
                user_id = int(parts[1])
                lesson_day = int(parts[2])
            else:
                await callback.message.answer("❌ Ошибка: неверный формат данных.")
                return
            
            await callback.message.answer(
                f"💬 <b>Ответ на вопрос</b>\n\n"
                f"👤 Пользователь ID: {user_id}\n"
                f"📚 Урок: День {lesson_day}\n\n"
                f"✍️ Ответьте на это сообщение с вашим ответом пользователю.\n\n"
                f"💡 Ответ будет отправлен анонимно от имени бота."
            )
        except Exception as e:
            logger.error(f"❌ Error in handle_curator_reply: {e}", exc_info=True)
            await callback.message.answer("❌ Ошибка при обработке запроса.")
    
    async def handle_curator_feedback(self, message: Message):
        """Handle curator feedback reply to question (anonymous response)."""
        if not message.reply_to_message:
            return
        
        reply_text = message.reply_to_message.text or message.reply_to_message.caption or ""
        
        # Проверяем, это ответ на вопрос или на задание
        # Если в сообщении есть "Новый вопрос" или "Вопрос:", это вопрос
        is_question = "❓" in reply_text or "Новый вопрос" in reply_text or "Вопрос:" in reply_text
        
        if is_question:
            # Это ответ на вопрос
            # Извлекаем user_id из сообщения
            user_id = None
            lesson_day = None
            
            if "🆔 ID:" in reply_text:
                try:
                    parts = reply_text.split("🆔 ID:")
                    if len(parts) > 1:
                        user_id_str = parts[1].split("\n")[0].strip()
                        user_id = int(user_id_str)
                except (ValueError, IndexError):
                    pass
            
            if "📚 Урок:" in reply_text:
                try:
                    parts = reply_text.split("📚 Урок:")
                    if len(parts) > 1:
                        lesson_str = parts[1].split("\n")[0].strip()
                        if "День" in lesson_str:
                            lesson_day = int(lesson_str.replace("День", "").strip())
                except (ValueError, IndexError):
                    pass
            
            # Или пытаемся извлечь из текста сообщения с кнопкой
            if not user_id and "👤 Пользователь ID:" in reply_text:
                try:
                    parts = reply_text.split("👤 Пользователь ID:")
                    if len(parts) > 1:
                        user_id_str = parts[1].split("\n")[0].strip()
                        user_id = int(user_id_str)
                except (ValueError, IndexError):
                    pass
            
            if not user_id:
                await message.answer("❌ Не удалось найти ID пользователя. Пожалуйста, ответьте на сообщение с вопросом.")
                return
            
            # Отправляем ответ пользователю анонимно
            answer_text = message.text or message.caption or ""
            if answer_text:
                user = await self.user_service.get_user(user_id)
                if user:
                    answer_message = (
                        f"💬 <b>Ответ на ваш вопрос</b>\n\n"
                    )
                    if lesson_day:
                        answer_message += f"📚 Урок: День {lesson_day}\n\n"
                    answer_message += f"{answer_text}"
                    
                    await self.bot.send_message(user.user_id, answer_message)
                    await message.answer("✅ Ответ отправлен пользователю анонимно.")
                else:
                    await message.answer("❌ Пользователь не найден.")
            else:
                await message.answer("❌ Ответ не может быть пустым.")
            return
        
        # Если это не вопрос, обрабатываем как задание (старая логика)
        # Extract assignment ID from replied message
        assignment_id = None
        if "Assignment ID:" in reply_text:
            try:
                # Extract number after "Assignment ID:"
                parts = reply_text.split("Assignment ID:")
                if len(parts) > 1:
                    assignment_id_str = parts[1].split("\n")[0].strip()
                    assignment_id = int(assignment_id_str)
            except (ValueError, IndexError):
                pass
        
        if not assignment_id:
            return  # Не задание и не вопрос, пропускаем
        
        assignment = await self.assignment_service.get_assignment(assignment_id)
        if not assignment:
            await message.answer("❌ Задание не найдено.")
            return

        # Авто-финал: после ответа по заданию 30
        should_send_final = (assignment.day_number == 30 and assignment.status != "feedback_sent")
        
        # Add feedback
        feedback_text = message.text or message.caption or ""
        await self.assignment_service.add_feedback(assignment_id, feedback_text)
        
        # Send feedback to user
        user = await self.user_service.get_user(assignment.user_id)
        if user:
            feedback_message = (
                f"💬 <b>Обратная связь по вашему заданию</b>\n\n"
                f"День {assignment.day_number}\n\n"
                f"{feedback_text}"
            )
            
            await self.bot.send_message(user.user_id, feedback_message)
            await self.assignment_service.mark_feedback_sent(assignment_id)

            # После обратной связи по дню 30 автоматически отправляем финальное сообщение
            if should_send_final and self.lesson_loader:
                try:
                    lesson30 = self.lesson_loader.get_lesson(30)
                    if lesson30:
                        await asyncio.sleep(0.8)
                        await self._send_lesson30_final_message_to_user(user_id=user.user_id, lesson_data=lesson30, send_keyboard=True)
                except Exception as e:
                    logger.error(f"   ❌ Failed to auto-send final message after feedback (user={user.user_id}): {e}", exc_info=True)
            
            await message.answer("✅ Обратная связь отправлена пользователю.")
        else:
            await message.answer("❌ Пользователь не найден.")
    
    async def handle_admin_feedback(self, message: Message):
        """Handle admin feedback reply to assignment (legacy handler for admin chat only)."""
        if not message.reply_to_message:
            return
        
        # Extract assignment ID from replied message
        reply_text = message.reply_to_message.text or message.reply_to_message.caption or ""
        
        # Try to find assignment ID in the message
        assignment_id = None
        if "Assignment ID:" in reply_text:
            try:
                # Extract number after "Assignment ID:"
                parts = reply_text.split("Assignment ID:")
                if len(parts) > 1:
                    assignment_id_str = parts[1].split("\n")[0].strip()
                    assignment_id = int(assignment_id_str)
            except (ValueError, IndexError):
                pass
        
        if not assignment_id:
            await message.answer("❌ Не удалось найти ID задания. Пожалуйста, ответьте на сообщение с заданием.")
            return
        
        assignment = await self.assignment_service.get_assignment(assignment_id)
        if not assignment:
            await message.answer("❌ Задание не найдено.")
            return

        # Авто-финал: после ответа по заданию 30
        should_send_final = (assignment.day_number == 30 and assignment.status != "feedback_sent")
        
        # Add feedback
        feedback_text = message.text or message.caption or ""
        await self.assignment_service.add_feedback(assignment_id, feedback_text)
        
        # Send feedback to user
        user = await self.user_service.get_user(assignment.user_id)
        if user:
            feedback_message = (
                f"💬 <b>Обратная связь по вашему заданию</b>\n\n"
                f"День {assignment.day_number}\n\n"
                f"{feedback_text}"
            )
            
            await self.bot.send_message(user.user_id, feedback_message)
            await self.assignment_service.mark_feedback_sent(assignment_id)

            # После обратной связи по дню 30 автоматически отправляем финальное сообщение
            if should_send_final and self.lesson_loader:
                try:
                    lesson30 = self.lesson_loader.get_lesson(30)
                    if lesson30:
                        await asyncio.sleep(0.8)
                        await self._send_lesson30_final_message_to_user(user_id=user.user_id, lesson_data=lesson30, send_keyboard=True)
                except Exception as e:
                    logger.error(f"   ❌ Failed to auto-send final message after admin feedback (user={user.user_id}): {e}", exc_info=True)
            
            await message.answer("✅ Обратная связь отправлена пользователю.")
        else:
            await message.answer("❌ Пользователь не найден.")
    
    async def deliver_lesson(self, user: User, lesson):
        """
        Deliver a lesson to a user.
        
        This is called by the scheduler when it's time to send a lesson.
        Использует данные из JSON файла.
        """
        try:
            # Проверяем день тишины
            if self.lesson_loader and self.lesson_loader.is_silent_day(user.current_day):
                logger.info(f"Day {user.current_day} is silent day for user {user.user_id}")
                # Пропускаем день, но не увеличиваем current_day
                return
            
            # Загружаем урок из JSON
            lesson_data = None
            if self.lesson_loader:
                lesson_data = self.lesson_loader.get_lesson(user.current_day)
            
            if lesson_data:
                # Отправляем урок из JSON
                await send_typing_action(self.bot, user.user_id, 0.8)
                await self._send_lesson_from_json(user, lesson_data, user.current_day)
            else:
                # Fallback на старый метод, если JSON нет
                lesson_text = format_lesson_message(lesson)
                keyboard = create_lesson_keyboard(lesson, Config.GENERAL_GROUP_ID, user)
                
                # Send lesson text
                await self.bot.send_message(user.user_id, lesson_text, reply_markup=keyboard)
                
                # Send image if available
                if lesson.image_url:
                    await self.bot.send_photo(user.user_id, lesson.image_url)
            
            logger.info(f"✅ Урок {user.current_day} отправлен пользователю {user.user_id}")
            
        except Exception as e:
            error_msg = str(e)
            # Фильтруем технические ошибки Telegram API о пустых сообщениях
            if "text must be non-empty" in error_msg or "message text is empty" in error_msg:
                logger.warning(f"⚠️ Empty message error for user {user.user_id} (suppressed): {error_msg}")
            else:
                logger.error(f"❌ Ошибка при отправке урока пользователю {user.user_id}: {e}", exc_info=True)
    
    async def handle_keyboard_navigator(self, message: Message):
        """Handle 'Навигатор' button from persistent keyboard."""
        await self._show_navigator(message.from_user.id, message)
    
    async def handle_keyboard_ask_question(self, message: Message):
        """Handle 'Задать вопрос' button from persistent keyboard."""
        user_id = message.from_user.id
        user = await self.user_service.get_user(user_id)
        
        persistent_keyboard = self._create_persistent_keyboard()
        
        if not user or not user.has_access():
            await message.answer("❌ У вас нет доступа к этому курсу.", reply_markup=persistent_keyboard)
            return
        
        # Проверяем тариф - вопросы доступны только для FEEDBACK, PREMIUM, PRACTIC тарифов
        if user.tariff not in [Tariff.FEEDBACK, Tariff.PREMIUM, Tariff.PRACTIC]:
            upgrade_keyboard = create_upgrade_tariff_keyboard()
            await message.answer(
                "ℹ️ <b>Вопросы доступны только для тарифа с обратной связью</b>\n\n"
                "📋 В вашем текущем тарифе (BASIC) функция задавать вопросы не включена.\n\n"
                "⬆️ Для возможности задавать вопросы обновитесь до тарифа FEEDBACK 💬.\n\n"
                "💬 Но вы можете обсудить вопросы в общем пространстве участников 👇",
                reply_markup=upgrade_keyboard
            )
            # Устанавливаем постоянную клавиатуру отдельным сообщением (используем невидимый символ)
            await message.answer("\u200B", reply_markup=persistent_keyboard)
            return
        
        # Сохраняем информацию о том, что пользователь задает вопрос
        if not hasattr(self, '_user_question_context'):
            self._user_question_context = {}
        self._user_question_context[user_id] = {
            'waiting_for_question': True,
            'lesson_id': user.current_day,
            'source': 'course_bot'
        }
        
        persistent_keyboard = self._create_persistent_keyboard()
        await message.answer(
            f"❓ <b>Задать вопрос</b>\n\n"
            f"✍️ Напишите ваш вопрос прямо здесь 👇\n\n"
            f"📤 Ваш вопрос будет отправлен куратору, и мы ответим вам как можно скорее ⚡\n\n"
            f"💡 <i>Можете задать вопрос по текущему уроку или по курсу в целом.</i>",
            reply_markup=persistent_keyboard
        )
    
    async def handle_keyboard_tariffs(self, message: Message):
        """Handle 'Тарифы' button from persistent keyboard - redirect to sales bot."""
        # Создаем deep link в sales bot для открытия тарифов
        sales_bot_username = "StartNowQ_bot"  # Имя sales bot
        deep_link = f"https://t.me/{sales_bot_username}?start=tariffs"
        
        persistent_keyboard = self._create_persistent_keyboard()
        await message.answer(
            "💎 <b>Тарифы курса</b>\n\n"
            "📋 Для просмотра и выбора тарифа перейдите в бот оплаты:\n\n"
            f"🤖 <a href='{deep_link}'>@StartNowQ_bot</a>\n\n"
            f"💡 <i>Нажмите на ссылку выше, чтобы открыть тарифы 👆</i>",
            disable_web_page_preview=False,
            reply_markup=persistent_keyboard
        )
    
    async def handle_keyboard_test(self, message: Message):
        """Handle 'Тест' button from persistent keyboard - show test lessons menu."""
        await self.handle_test_lessons(message)
    
    async def handle_keyboard_discussion(self, message: Message):
        """Handle 'Обсуждение' button from persistent keyboard - redirect to discussion group."""
        persistent_keyboard = self._create_persistent_keyboard()
        
        # Prefer configured invite link; fallback to group id/username heuristics.
        group_link = (self.community_service.get_group_invite_link(Config.GENERAL_GROUP_ID) or "").strip()
        if not group_link:
            # Additional fallback for numeric chat IDs (private groups): try `t.me/c/<id>/1`
            # Note: this opens the chat only if the user already has access; invite link is still preferred.
            general_group_id = (Config.GENERAL_GROUP_ID or "").strip()
            if general_group_id and (general_group_id.startswith("-") or general_group_id.lstrip("-").isdigit()):
                group_id_clean = str(general_group_id).replace("-100", "").replace("-", "")
                if group_id_clean.isdigit():
                    group_link = f"https://t.me/c/{group_id_clean}/1"
        
        if group_link:
            await message.answer(
                "💬 <b>Перейти к обсуждению</b>\n\n"
                "📚 Обсудите задания и вопросы с другими участниками курса:\n\n"
                f"👥 <a href='{group_link}'>Перейти в обсуждение</a>\n\n"
                "💡 <i>Если ссылка не открывается — напишите в поддержку, мы добавим вас вручную.</i>",
                disable_web_page_preview=False,
                reply_markup=persistent_keyboard
            )
        else:
            # Нет ссылки — даем понятный CTA, без “ошибки”
            await message.answer(
                "💬 <b>Обсуждение</b>\n\n"
                "Ссылка на чат обсуждения пока не настроена в конфигурации.\n\n"
                "Что можно сделать сейчас:\n"
                "1) Написать в поддержку / администратору\n"
                "2) Перейти в бота оплаты и выбрать тариф/апгрейд (если вы ещё не в группе)\n\n"
                "🤖 <a href='https://t.me/StartNowQ_bot'>@StartNowQ_bot</a>",
                disable_web_page_preview=False,
                reply_markup=persistent_keyboard
            )
    
    async def handle_keyboard_mentor(self, message: Message):
        """Handle 'Наставник' button from persistent keyboard - show mentor menu."""
        user_id = message.from_user.id
        user = await self.user_service.get_user(user_id)
        
        persistent_keyboard = self._create_persistent_keyboard()
        
        if not user or not user.has_access():
            await message.answer("❌ У вас нет доступа к этому курсу.", reply_markup=persistent_keyboard)
            return
        
        # Отправляем один анимированный эмодзи наставника
        await message.answer("👨‍🏫")
        
        # Создаем клавиатуру с выбором частоты напоминаний
        buttons = []
        row = []
        for i in range(6):  # 0-5
            text = f"{i}"
            if i == 0:
                text = "0 ❌"
            elif user.mentor_reminders == i:
                text = f"{i} ✅"
            
            row.append(InlineKeyboardButton(
                text=text,
                callback_data=f"mentor:set:{i}"
            ))
            
            # По 3 кнопки в ряд
            if len(row) == 3:
                buttons.append(row)
                row = []
        
        if row:
            buttons.append(row)
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
        
        # Определяем текущий статус
        if user.mentor_reminders == 0:
            status_text = "❌ Наставник уволен (напоминания отключены)"
        else:
            status_text = f"✅ Наставник напоминает {user.mentor_reminders} раз(а) в день"
        
        await message.answer(
            f"👨‍🏫 <b>НАСТАВНИК</b>\n\n"
            f"Текущий статус: {status_text}\n\n"
            f"Выберите частоту напоминаний:\n"
            f"• <b>0</b> — напоминания отключены\n"
            f"• <b>1-5</b> — количество напоминаний в день\n\n"
            f"Напоминания содержат задание текущего урока.",
            reply_markup=keyboard
        )
    
    async def handle_mentor_set_frequency(self, callback: CallbackQuery):
        """Handle mentor frequency selection callback."""
        try:
            await callback.answer()
        except:
            pass
        
        user_id = callback.from_user.id
        user = await self.user_service.get_user(user_id)
        
        if not user:
            await callback.message.answer("❌ У вас нет доступа.")
            return
        
        # Парсим выбранную частоту
        try:
            frequency = int(callback.data.split(":")[-1])
            if frequency < 0 or frequency > 5:
                raise ValueError("Frequency out of range")
        except (ValueError, IndexError):
            await callback.message.answer("❌ Ошибка: неверная частота.")
            return
        
        # Обновляем настройку пользователя
        user.mentor_reminders = frequency
        await self.db.update_user(user)
        
        # Формируем сообщение об изменении
        if frequency == 0:
            status_text = "❌ Наставник уволен\n\nНапоминания отключены."
        else:
            status_text = f"✅ Наставник настроен на {frequency} напоминание(й) в день\n\nВы будете получать напоминания с заданием текущего урока."
        
        persistent_keyboard = self._create_persistent_keyboard()
        await callback.message.answer(
            f"👨‍🏫 <b>НАСТАВНИК</b>\n\n{status_text}",
            reply_markup=persistent_keyboard
        )
        
        logger.info(f"User {user_id} set mentor reminders frequency to {frequency}")
    
    async def _send_mentor_reminder(self, user: User):
        """
        Отправляет напоминание от наставника пользователю.
        
        Args:
            user: Пользователь, которому нужно отправить напоминание
        """
        try:
            # Получаем задание текущего урока
            lesson_data = self.lesson_loader.get_lesson(user.current_day)
            if not lesson_data:
                logger.warning(f"   ⚠️ No lesson data for day {user.current_day}, skipping reminder")
                return
            
            # Получаем задание в зависимости от тарифа
            task = self.lesson_loader.get_task_for_tariff(user.current_day, user.tariff)
            if not task or not task.strip():
                logger.debug(f"   ⚠️ No task for lesson {user.current_day}, skipping reminder")
                return
            
            # Импортируем функцию для генерации напоминания
            from bots.mentor_reminders import get_mentor_reminder_text
            
            # Генерируем текст напоминания
            reminder_text = get_mentor_reminder_text(task)
            
            # Отправляем напоминание
            await self.bot.send_message(user.user_id, reminder_text)
            
            # Обновляем время последнего напоминания
            from datetime import datetime
            user.last_mentor_reminder = datetime.utcnow()
            await self.db.update_user(user)
            
            logger.info(f"   ✅ Mentor reminder sent to user {user.user_id} (day {user.current_day})")
            
        except Exception as e:
            logger.error(f"   ❌ Error sending mentor reminder to user {user.user_id}: {e}", exc_info=True)
    
    async def start(self):
        """Start the bot and scheduler."""
        await self.db.connect()
        
        # Initialize and start lesson scheduler
        self.scheduler = LessonScheduler(
            self.db,
            self.lesson_service,
            self.user_service,
            self.deliver_lesson
        )
        
        # Initialize and start mentor reminder scheduler
        self.mentor_scheduler = MentorReminderScheduler(
            self.db,
            self._send_mentor_reminder
        )
        
        # Start schedulers in background
        scheduler_task = asyncio.create_task(self.scheduler.start())
        mentor_scheduler_task = asyncio.create_task(self.mentor_scheduler.start())
        
        logger.info("Course Bot started")
        try:
            await self.dp.start_polling(self.bot, skip_updates=True)
        finally:
            if self.scheduler:
                self.scheduler.stop()
                scheduler_task.cancel()
            if self.mentor_scheduler:
                self.mentor_scheduler.stop()
                mentor_scheduler_task.cancel()
            await self.db.close()
            await self.bot.session.close()
    
    async def stop(self):
        """Stop the bot."""
        if self.scheduler:
            self.scheduler.stop()
        await self.db.close()
        await self.bot.session.close()


async def main():
    """Main entry point."""
    if not Config.validate():
        logger.error("❌ Неверная конфигурация. Проверьте файл .env")
        return
    
    bot = CourseBot()
    try:
        await bot.start()
    except KeyboardInterrupt:
        logger.info("Stopping bot...")
    finally:
        await bot.stop()


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
