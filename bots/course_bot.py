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
                    KeyboardButton(text="💎 Тарифы"),
                    KeyboardButton(text="🔍")
                ]
            ],
            resize_keyboard=True,
            persistent=True
        )
        return keyboard
    
    async def _ensure_persistent_keyboard(self, user_id: int):
        """Ensure persistent keyboard is always visible by sending it if needed."""
        try:
            persistent_keyboard = self._create_persistent_keyboard()
            # Используем невидимый символ вместо пустого сообщения
            await self.bot.send_message(user_id, "\u200B", reply_markup=persistent_keyboard)
        except Exception as e:
            logger.debug(f"Could not send persistent keyboard to {user_id}: {e}")
    
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
        
        # Обработчики для постоянных кнопок клавиатуры
        # ВАЖНО: Регистрируем ПЕРЕД общими обработчиками текста, чтобы они имели приоритет
        self.dp.message.register(self.handle_keyboard_navigator, F.text == "🧭")
        self.dp.message.register(self.handle_keyboard_ask_question, F.text == "❓")
        self.dp.message.register(self.handle_keyboard_tariffs, F.text == "💎 Тарифы")
        self.dp.message.register(self.handle_keyboard_test, F.text == "🔍")
        
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
            
            # Проверяем день тишины
            if self.lesson_loader.is_silent_day(user.current_day):
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
        
        # Загружаем урок из JSON
        lesson_data = self.lesson_loader.get_lesson(day)
        
        if not lesson_data:
            await callback.message.answer(
                f"❌ Урок для дня {day} не найден в базе данных.",
                reply_markup=persistent_keyboard
            )
            return
        
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
            logger.error(f"❌ Error sending test lesson {day}: {e}", exc_info=True)
            await callback.message.answer(f"❌ Ошибка при отправке урока {day}: {str(e)}", reply_markup=persistent_keyboard)
        finally:
            # Восстанавливаем original_day (не сохраняем в БД, это только для отображения)
            user.current_day = original_day
    
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
        
        # Загружаем урок из JSON
        lesson_data = self.lesson_loader.get_lesson(day)
        
        if not lesson_data:
            await callback.message.answer(
                f"❌ Урок для дня {day} не найден в базе данных.",
                reply_markup=persistent_keyboard
            )
            return
        
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
        import traceback
        logger.info(f"🔵 _send_lesson_from_json CALLED for day {day}, user {user.user_id}, skip_intro={skip_intro}, skip_about_me={skip_about_me}")
        logger.info(f"   Call stack: {''.join(traceback.format_stack()[-3:-1])}")
        
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
                    if intro_photo_file_id:
                        await self.bot.send_photo(user.user_id, intro_photo_file_id)
                        logger.info(f"   ✅ Sent intro photo (file_id) for lesson {day}")
                    elif intro_photo_path:
                        from pathlib import Path
                        from aiogram.types import FSInputFile
                        photo_file = FSInputFile(Path(intro_photo_path))
                        await self.bot.send_photo(user.user_id, photo_file)
                        logger.info(f"   ✅ Sent intro photo (file path) for lesson {day}")
                    await asyncio.sleep(0.5)  # Небольшая пауза после фото
                except Exception as photo_error:
                    logger.warning(f"   ⚠️ Не удалось отправить intro photo для урока {day}: {photo_error}")
            
            lesson_message = (
                f"{create_premium_separator()}\n"
                f"📚 <b>{title}</b>\n"
                f"{create_premium_separator()}\n\n"
            )
            
            # Отправляем вводный текст отдельным сообщением, если есть (пропускаем для навигатора)
            if intro_text and not skip_intro:
                intro_message = f"{intro_text}\n\n{create_premium_separator()}\n\n"
                await self.bot.send_message(user.user_id, intro_message)
                logger.info(f"   Sent intro_text for lesson {day}")
                await asyncio.sleep(0.3)  # Небольшая пауза
            elif intro_text and skip_intro:
                logger.info(f"   Skipped intro_text for lesson {day} (navigator mode)")
            
            # Отправляем "ОБО МНЕ" отдельным сообщением с фото (для урока 1) - сразу после intro_text (пропускаем для навигатора)
            about_me_text = lesson_data.get("about_me_text", "")
            about_me_photo_file_id = lesson_data.get("about_me_photo_file_id", "")
            about_me_photo_path = lesson_data.get("about_me_photo_path", "")
            
            logger.info(f"   Checking 'ОБО МНЕ' for lesson {day}: text={bool(about_me_text)}, file_id={bool(about_me_photo_file_id)}, path={bool(about_me_photo_path)}, skip={skip_about_me}")
            
            if about_me_text and not skip_about_me:
                await asyncio.sleep(0.5)  # Небольшая пауза
                
                # Флаг для отслеживания успешной отправки
                about_me_sent = False
                
                # Пробуем отправить фото, если есть file_id (приоритет)
                if about_me_photo_file_id:
                    try:
                        await self.bot.send_photo(
                            user.user_id,
                            about_me_photo_file_id,
                            caption=about_me_text
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
                                await self.bot.send_photo(
                                    user.user_id,
                                    photo_file,
                                    caption=about_me_text
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
                        from pathlib import Path
                        from aiogram.types import FSInputFile
                        photo_file = FSInputFile(Path(about_me_photo_path))
                        await self.bot.send_photo(
                            user.user_id,
                            photo_file,
                            caption=about_me_text
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
            
            # Добавляем основной текст
            lesson_message += f"{text}\n\n"
            
            # Добавляем задание, если есть
            if task:
                lesson_message += (
                    f"{create_premium_separator()}\n\n"
                    f"📝 <b>Задание:</b>\n"
                    f"{task}\n\n"
                )
            
            # Отправляем текст урока
            # Передаем day в lesson_data для создания клавиатуры
            lesson_data_with_day = lesson_data.copy()
            lesson_data_with_day["day_number"] = day
            keyboard = create_lesson_keyboard_from_json(lesson_data_with_day, user, Config.GENERAL_GROUP_ID)
            
            logger.info(f"   Sending lesson message to user {user.user_id}, day {day}")
            logger.info(f"   Message length: {len(lesson_message)} characters")
            
            # Проверяем, что lesson_message не пустой после всех манипуляций
            if not lesson_message or not lesson_message.strip():
                logger.error(f"   ❌ Empty lesson_message for day {day}, user {user.user_id}")
                persistent_keyboard = self._create_persistent_keyboard()
                await self.bot.send_message(user.user_id, "❌ Ошибка: урок не содержит текста.", reply_markup=persistent_keyboard)
                return
            
            # Разбиваем длинные сообщения на части (лимит Telegram: 4096 символов)
            MAX_MESSAGE_LENGTH = 4000  # Оставляем запас
            if len(lesson_message) > MAX_MESSAGE_LENGTH:
                # Разбиваем сообщение на части
                message_parts = self._split_long_message(lesson_message, MAX_MESSAGE_LENGTH)
                logger.info(f"   Message split into {len(message_parts)} parts")
                
                # Отправляем все части кроме последней без клавиатуры
                for i, part in enumerate(message_parts[:-1], 1):
                    # Пропускаем пустые части
                    if part and part.strip():
                        await self.bot.send_message(user.user_id, part)
                        await asyncio.sleep(0.3)  # Небольшая пауза между сообщениями
                        logger.info(f"   Sent part {i}/{len(message_parts)}")
                    else:
                        logger.warning(f"   Skipped empty part {i}/{len(message_parts)}")
                
                # Отправляем последнюю часть с клавиатурой (проверяем, что она не пустая)
                last_part = message_parts[-1]
                if last_part and last_part.strip():
                    await self.bot.send_message(user.user_id, last_part, reply_markup=keyboard)
                    logger.info(f"   Sent last part {len(message_parts)}/{len(message_parts)} with keyboard")
                else:
                    # Если последняя часть пустая, отправляем только клавиатуру с невидимым символом
                    logger.warning(f"   Last part is empty, sending keyboard only")
                    await self.bot.send_message(user.user_id, "\u200B", reply_markup=keyboard)
            else:
                # Если сообщение короткое, отправляем как есть
                await self.bot.send_message(user.user_id, lesson_message, reply_markup=keyboard)
            
            # Всегда устанавливаем постоянную клавиатуру после отправки урока
            # Используем невидимый символ вместо пустого сообщения
            persistent_keyboard = self._create_persistent_keyboard()
            await self.bot.send_message(user.user_id, "\u200B", reply_markup=persistent_keyboard)
            
            # Отправляем медиа, если есть
            media_list = lesson_data.get("media", [])
            for media_item in media_list[:5]:  # Ограничиваем количество
                media_type = media_item.get("type", "photo")
                file_path = media_item.get("path")
                file_id = media_item.get("file_id")
                
                try:
                    if media_type == "photo":
                        if file_id:
                            await self.bot.send_photo(user.user_id, file_id)
                        elif file_path:
                            from pathlib import Path
                            if Path(file_path).exists():
                                with open(file_path, "rb") as photo:
                                    await self.bot.send_photo(user.user_id, photo)
                    elif media_type == "video":
                        if file_id:
                            await self.bot.send_video(user.user_id, file_id)
                        elif file_path:
                            from pathlib import Path
                            if Path(file_path).exists():
                                with open(file_path, "rb") as video:
                                    await self.bot.send_video(user.user_id, video)
                except Exception as media_error:
                    logger.warning(f"Не удалось отправить медиа для урока {day}: {media_error}")
            
            # Отправляем follow_up_text в конце урока, если есть (для урока 30)
            follow_up_text = lesson_data.get("follow_up_text", "")
            follow_up_photo_path = lesson_data.get("follow_up_photo_path", "")
            follow_up_photo_file_id = lesson_data.get("follow_up_photo_file_id", "")
            
            logger.info(f"   Checking follow_up for lesson {day}: text={bool(follow_up_text)}, photo_path={follow_up_photo_path}, photo_file_id={bool(follow_up_photo_file_id)}")
            
            if follow_up_text or follow_up_photo_path or follow_up_photo_file_id:
                await asyncio.sleep(1)  # Небольшая пауза перед отправкой
                persistent_keyboard = self._create_persistent_keyboard()
                
                # Отправляем фото перед текстом, если есть
                if follow_up_photo_file_id or follow_up_photo_path:
                    try:
                        if follow_up_photo_file_id:
                            await self.bot.send_photo(user.user_id, follow_up_photo_file_id)
                            logger.info(f"   ✅ Sent follow_up photo (file_id) for lesson {day}")
                        elif follow_up_photo_path:
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
                            
                            logger.info(f"   Trying to send follow_up photo from: {photo_path} (exists: {photo_path.exists()})")
                            
                            if photo_path.exists():
                                photo_file = FSInputFile(photo_path)
                                await self.bot.send_photo(user.user_id, photo_file)
                                logger.info(f"   ✅ Sent follow_up photo (file path: {photo_path}) for lesson {day}")
                            else:
                                logger.error(f"   ❌ Follow-up photo not found: {photo_path} (original path: {follow_up_photo_path})")
                        await asyncio.sleep(0.5)  # Небольшая пауза после фото
                    except Exception as photo_error:
                        logger.error(f"   ❌ Не удалось отправить follow_up photo для урока {day}: {photo_error}", exc_info=True)
                
                # Отправляем текст после фото
                if follow_up_text:
                    try:
                        await self.bot.send_message(user.user_id, follow_up_text, reply_markup=persistent_keyboard)
                        logger.info(f"   ✅ Sent follow_up_text for lesson {day}")
                    except Exception as text_error:
                        logger.error(f"   ❌ Не удалось отправить follow_up_text для урока {day}: {text_error}", exc_info=True)
            else:
                logger.info(f"   ⚠️ No follow_up content for lesson {day}")
            
            logger.info(f"✅ Урок {day} отправлен пользователю {user.user_id}")
            
        except Exception as e:
            logger.error(f"❌ Ошибка при отправке урока пользователю {user.user_id}: {e}", exc_info=True)
            raise
    
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
    
    async def start(self):
        """Start the bot and scheduler."""
        await self.db.connect()
        
        # Initialize and start scheduler
        self.scheduler = LessonScheduler(
            self.db,
            self.lesson_service,
            self.user_service,
            self.deliver_lesson
        )
        
        # Start scheduler in background
        scheduler_task = asyncio.create_task(self.scheduler.start())
        
        logger.info("Course Bot started")
        try:
            await self.dp.start_polling(self.bot, skip_updates=True)
        finally:
            self.scheduler.stop()
            scheduler_task.cancel()
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
    asyncio.run(main())
