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
import html
from html import escape
import logging
import re
import sys
import aiohttp
import subprocess
import os
from pathlib import Path
from typing import Optional, Dict, Any
from urllib.parse import parse_qs, urlparse
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton, BufferedInputFile
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

from core.config import Config
from core.database import Database
from core.models import User, Tariff
from services.user_service import UserService
from services.lesson_service import LessonService
from services.lesson_loader import LessonLoader
from services.drive_content_sync import DriveContentSync
from services.assignment_service import AssignmentService
from services.community_service import CommunityService
from services.question_service import QuestionService
from utils.telegram_helpers import create_lesson_keyboard, format_lesson_message, create_lesson_keyboard_from_json, create_upgrade_tariff_keyboard
from utils.scheduler import LessonScheduler
from utils.mentor_scheduler import MentorReminderScheduler
from utils.premium_ui import send_typing_action
from utils.navigator import create_navigator_keyboard, format_navigator_message

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
# Reduce spam from noisy third-party libraries if necessary
logging.getLogger('asyncio').setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# Ширина мобильного экрана для медиа (в пикселях)
# Telegram автоматически масштабирует медиа под ширину экрана пользователя
# Используем стандартную ширину для мобильных устройств
MOBILE_SCREEN_WIDTH = 720  # Стандартная ширина для мобильных устройств в Telegram

# Разделитель для медиафайлов - визуально расширяет блок медиа
# Используется в caption медиафайлов для улучшения восприятия
# Короткая волнистая линия из 12 символов
MEDIA_SEPARATOR = "〰️" * 12

# Placeholder for keyboard-only messages (Telegram API rejects empty or zero-width text)
KEYBOARD_ONLY_PLACEHOLDER = " "


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

        # Per-user transient states for "send one message" flows
        self._user_question_context: dict[int, dict] = {}
        # Per-user states for time input
        self._user_time_input_context: dict[int, str] = {}  # user_id -> "lesson" | "reminder_start" | "reminder_end"
        self._user_assignment_context: dict[int, dict] = {}
        
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
                    KeyboardButton(text="💎"),
                    KeyboardButton(text="❓"),
                ],
                [
                    KeyboardButton(text="💬"),
                    KeyboardButton(text="👨‍🏫"),
                ],
            ],
            resize_keyboard=True,
            is_persistent=True
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
            await self.bot.send_message(user_id, KEYBOARD_ONLY_PLACEHOLDER, reply_markup=persistent_keyboard)
        except Exception as e:
            logger.debug(f"Could not send persistent keyboard to {user_id}: {e}")

    async def handle_sync_content(self, message: Message):
        """Admin command: sync lessons from Google Drive into /app/data/lessons.json and reload LessonLoader.
        
        Usage:
            /sync_content - обычная синхронизация
            /sync_content clean - очистить медиафайлы и пересинхронизировать
        """
        # Check that ADMIN_CHAT_ID is set (can be negative for groups, so check != 0)
        if Config.ADMIN_CHAT_ID == 0 or message.from_user.id != Config.ADMIN_CHAT_ID:
            return

        # Проверяем, есть ли параметр "clean"
        command_args = message.text.split()[1:] if message.text else []
        clean_media = len(command_args) > 0 and command_args[0].lower() in ["clean", "очистить", "clear"]
        
        if clean_media:
            await message.answer("🧹 Очищаю медиафайлы и синхронизирую контент из Google Drive…")
        else:
            await message.answer("🔄 Синхронизирую контент из Google Drive…")
        
        syncer = DriveContentSync()
        try:
            result = await asyncio.to_thread(syncer.sync_now, clean_media=clean_media)
        except Exception as e:
            await message.answer(f"❌ Sync failed: <code>{e}</code>")
            return

        # Reload in-memory cache so new lessons take effect immediately
        try:
            logger.info("🔄 Reloading lesson_loader after sync...")
            self.lesson_loader.reload()
            # Verify that media_markers are loaded for day 0
            day0_data = self.lesson_loader.get_lesson(0)
            if day0_data:
                if "media_markers" in day0_data:
                    logger.info(f"✅ Day 0 media_markers after reload: {len(day0_data.get('media_markers', {}))} markers")
                    logger.info(f"   📎 Marker keys: {list(day0_data.get('media_markers', {}).keys())}")
                else:
                    logger.warning(f"⚠️ Day 0 media_markers NOT FOUND after reload! Available keys: {list(day0_data.keys())}")
            else:
                logger.warning(f"⚠️ Day 0 lesson_data is None after reload!")
        except Exception as e:
            logger.error(f"❌ Failed to reload lesson_loader: {e}", exc_info=True)

        warn_text = ""
        if result.warnings:
            shown = result.warnings[:10]
            warn_text = "\n\n⚠️ Предупреждения:\n" + "\n".join(f"• {w}" for w in shown)
            if len(result.warnings) > 10:
                warn_text += f"\n…и ещё {len(result.warnings) - 10}"

        clean_info = ""
        if clean_media:
            clean_info = "\n🧹 Медиафайлы очищены и загружены заново.\n"

        await message.answer(
            f"✅ Синхронизация завершена.{clean_info}\n\n"
            f"📚 Обновлено уроков: <b>{result.days_synced}</b>\n"
            f"📦 Блоков всего: <b>{result.total_blocks}</b>\n"
            f"📎 Медиафайлов всего: <b>{result.total_media_files}</b>\n"
            f"⬇️ Медиафайлов загружено: <b>{result.media_files_downloaded}</b>\n"
            f"📁 Путь к урокам: <code>{result.lessons_path}</code>"
            f"{warn_text}"
        )
    
    async def _send_video_with_retry(self, user_id: int, video, caption: str = None, 
                                     width: int = None, height: int = None, 
                                     supports_streaming: bool = True, max_retries: int = 3,
                                     protect_content: bool = False):
        """
        Отправляет видео с повторными попытками и оптимизированными параметрами.
        Автоматически сжимает видео, если оно превышает лимит 50 МБ.
        Автоматически масштабирует под ширину мобильного экрана, если размеры не указаны.
        
        Args:
            user_id: ID пользователя
            video: file_id или FSInputFile
            caption: Подпись к видео
            width: Ширина видео (если None, используется MOBILE_SCREEN_WIDTH)
            height: Высота видео (если None, вычисляется пропорционально)
            supports_streaming: Поддержка стриминга
            max_retries: Максимальное количество попыток
        """
        # Если width не указан, используем ширину мобильного экрана
        # Если height не указан, но width указан, вычисляем height пропорционально
        # Если оба не указаны, используем ширину мобильного экрана и не указываем height
        # (Telegram автоматически масштабирует)
        if width is None:
            width = MOBILE_SCREEN_WIDTH
            # Не указываем height, чтобы Telegram автоматически масштабировал пропорционально
            height = None
        
        # Если это FSInputFile, проверяем размер и сжимаем при необходимости
        video_to_send = video
        original_video_path = None
        from aiogram.types import FSInputFile
        if isinstance(video, FSInputFile):
            # Получаем путь к файлу из FSInputFile
            # FSInputFile может иметь атрибут path или filename
            video_path = None
            if hasattr(video, 'path'):
                video_path = Path(video.path)
            elif hasattr(video, 'filename'):
                video_path = Path(video.filename)
            elif hasattr(video, '_path'):
                video_path = Path(video._path)
            
            # Сохраняем оригинальный путь для использования в обработчике ошибок
            original_video_path = video_path
            
            if video_path and video_path.exists():
                compressed_path = await self._compress_video_if_needed(video_path)
                if compressed_path:
                    video_to_send = FSInputFile(compressed_path)
                    logger.info(f"   📹 Using compressed video: {compressed_path.name}")
        
        # Добавляем разделитель к caption для визуального расширения блока медиа
        caption_with_separator = self._add_media_separator(caption)
        
        for attempt in range(max_retries):
            try:
                # Увеличиваем таймаут для больших файлов
                request_timeout = 300 if attempt == 0 else 600  # 5 минут, затем 10 минут
                
                # Отправляем видео с указанными параметрами
                # Если height не указан, Telegram автоматически масштабирует пропорционально width
                await self.bot.send_video(
                    user_id,
                    video_to_send,
                    caption=caption_with_separator,
                    width=width,
                    height=height,
                    supports_streaming=supports_streaming,
                    request_timeout=request_timeout,
                    protect_content=protect_content
                )
                logger.info(f"   ✅ Видео успешно отправлено (попытка {attempt + 1})")
                return
            except Exception as e:
                error_msg = str(e).lower()
                if "entity too large" in error_msg or "file too large" in error_msg:
                    # Если даже после сжатия файл слишком большой, пытаемся отправить ссылку на Google Drive
                    logger.error(f"   ❌ Video still too large after compression: {e}")
                    # Пытаемся найти file_id из исходного видео или media_markers
                    drive_file_id = None
                    if original_video_path:
                        # Пытаемся найти file_id из имени файла или пути
                        # Имя файла может содержать file_id (например, из media_markers)
                        file_name = original_video_path.name
                        # Ищем паттерн file_id в пути или имени файла
                        # Обычно file_id находится в пути как часть структуры папок
                        import re
                        # Пытаемся извлечь file_id из пути (например, data/content_media/day_01/001_.mp4)
                        # или из media_markers, если они доступны
                        logger.warning(f"   ⚠️ Cannot send large video: {original_video_path}")
                        logger.warning(f"   ⚠️ No Drive link available for this video")
                    raise
                elif attempt < max_retries - 1:
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
        self.dp.message.register(self.handle_sync_content, Command("sync_content"))
        # ВРЕМЕННАЯ КНОПКА ДЛЯ ПРОВЕРКИ УРОКОВ
        self.dp.message.register(self.handle_test_lessons, Command("test_lessons"))
        # НАВИГАТОР КУРСА
        self.dp.message.register(self.handle_navigator, Command("navigator"))
        
        logger.info("✅ Course bot handlers registered:")
        logger.info(f"   - /start -> handle_start")
        logger.info(f"   - /lesson -> handle_current_lesson")
        logger.info(f"   - /progress -> handle_progress")
        logger.info(f"   - /sync_content -> handle_sync_content")
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
        self.dp.callback_query.register(self.handle_questions_list, F.data == "questions:list")
        self.dp.callback_query.register(self.handle_question_view, F.data.startswith("question:view:"))
        self.dp.callback_query.register(self.handle_question_answer, F.data.startswith("question:answer:"))
        self.dp.callback_query.register(self.handle_lesson21_card, F.data.startswith("lesson21_card:"))
        self.dp.callback_query.register(self.handle_lesson21_download_cards, F.data == "lesson21_download_cards")
        self.dp.callback_query.register(self.handle_lesson19_show_levels, F.data == "lesson19_show_levels")
        self.dp.callback_query.register(self.handle_final_message, F.data == "lesson30_final_message")
        
        # Обработчики для постоянных кнопок клавиатуры
        # ВАЖНО: Регистрируем ПЕРЕД общими обработчиками текста, чтобы они имели приоритет
        self.dp.message.register(self.handle_keyboard_navigator, (F.text == "🧭") | (F.text == "🧿"))
        self.dp.message.register(self.handle_keyboard_ask_question, (F.text == "❔") | (F.text == "❓") | (F.text == "🔵"))
        self.dp.message.register(self.handle_keyboard_tariffs, (F.text == "💎") | (F.text == "💙"))
        self.dp.message.register(self.handle_keyboard_submit_assignment, F.text == "📝")
        # Кнопка 🔍 была тестовой и удалена из постоянной клавиатуры
        self.dp.message.register(self.handle_keyboard_discussion, (F.text == "💬") | (F.text == "🟦"))
        self.dp.message.register(self.handle_keyboard_mentor, F.text == "👨‍🏫")
        
        # Обработчики для настройки наставника
        self.dp.callback_query.register(self.handle_mentor_set_frequency, F.data.startswith("mentor:set:"))
        self.dp.callback_query.register(self.handle_mentor_settings, F.data.startswith("mentor:settings:"))
        self.dp.callback_query.register(self.handle_mentor_time_set, F.data.startswith("mentor:time:"))
        
        # Обработчик ввода времени для настройки наставника (перед общими обработчиками)
        self.dp.message.register(self.handle_time_input, F.text & ~F.command)
        
        # Общие обработчики сообщений (после команд!)
        # ВАЖНО: Используем F.text & ~F.command чтобы НЕ перехватывать команды
        # IMPORTANT: register specific handlers first; fallbacks last.
        self.dp.message.register(self.handle_assignment_media, F.photo | F.video | F.document | F.voice)
        self.dp.message.register(self.handle_question_voice, F.voice)
        self.dp.message.register(self.handle_assignment_text, F.text & ~F.command)
        self.dp.message.register(self.handle_question_text, F.text & ~F.command)
        # Обработчик ответов на вопросы в ПУП (должен быть перед общими обработчиками)
        self.dp.message.register(self.handle_curator_feedback, F.text & ~F.command & F.reply_to_message)
        self.dp.message.register(self.handle_unclassified_voice, F.voice)
        self.dp.message.register(self.handle_unclassified_media, F.photo | F.video | F.document)
        self.dp.message.register(self.handle_unclassified_text, F.text & ~F.command)
        
        # Ответы кураторов/админов через course-bot отключены:
        # вопросы и задания обрабатываются только через ПУП (admin-bot).
    
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
            # Проверяем, заблокирован ли пользователь
            if user.is_blocked:
                await message.answer(
                    "🚫 <b>Ваш доступ к курсу заблокирован</b>\n\n"
                    "Обратитесь в поддержку для выяснения причин.",
                    reply_markup=persistent_keyboard,
                    parse_mode="HTML"
                )
            else:
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
            f"Добро пожаловать в курс, {user_name}!\n\n"
            f"День {user.current_day} из {Config.COURSE_DURATION_DAYS}\n"
            f"Тариф: {user.tariff.value.upper()}\n\n"
            f"Используйте /lesson для просмотра текущего урока.",
            reply_markup=persistent_keyboard
        )
        
        # Log session start (non-blocking, don't fail if DB not ready)
        try:
            from datetime import datetime
            # Ensure DB is connected before logging
            await self.db._ensure_connection()
            await self.db.log_user_session(user_id, "course", datetime.utcnow())
            await self.db.log_user_activity(user_id, "course", "start", "main")
        except Exception as e:
            # Don't fail the request if logging fails
            logger.debug(f"Failed to log user activity (non-critical): {e}")
    
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
                    f"Сегодня день тишины (День {user.current_day}).\n\n"
                    f"Отдыхайте и переваривайте полученные знания!",
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
            f"<b>Ваш прогресс</b>\n\n"
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
            # Убираем разделители - caption должен браться из данных карточки или быть None
            caption = None
            if file_id:
                await self.bot.send_photo(user_id, file_id, caption=caption, protect_content=True)
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
                        # Обрабатываем изображение для мобильных устройств
                        resized_path = await self._resize_image_for_mobile(card_file)
                        image_path_to_use = resized_path if resized_path else card_file
                        photo_file = FSInputFile(image_path_to_use)
                        # Убираем разделители - caption должен браться из данных карточки или быть None
                        caption = None
                        await self.bot.send_photo(user_id, photo_file, caption=caption, protect_content=True)
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
                                # Обрабатываем изображение для мобильных устройств
                                resized_path = await self._resize_image_for_mobile(card_file)
                                image_path_to_use = resized_path if resized_path else card_file
                                photo_file = FSInputFile(image_path_to_use)
                                media_group.append(
                                    InputMediaPhoto(
                                        media=photo_file
                                    )
                                )
                
                if media_group:
                    # Отправляем медиа-группу (защищенную от копирования)
                    await self.bot.send_media_group(user_id, media_group, protect_content=True)
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
            await callback.answer("Загружаю уровни...")
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
                            # Обрабатываем изображение для мобильных устройств
                            resized_path = await self._resize_image_for_mobile(image_file)
                            image_path_to_use = resized_path if resized_path else image_file
                            photo_file = FSInputFile(image_path_to_use)
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
                            await self.bot.send_photo(user_id, media_item.media, protect_content=True)
                        else:
                            # FSInputFile
                            await self.bot.send_photo(user_id, media_item.media, protect_content=True)
                        total_sent += 1
                    else:
                        # Медиа-группа (2-10 изображений) (защищенная от копирования)
                        await self.bot.send_media_group(user_id, media_group, protect_content=True)
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
                                await self.bot.send_photo(user_id, media_item.media, protect_content=True)
                            else:
                                await self.bot.send_photo(user_id, media_item.media, protect_content=True)
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
                            await self.bot.send_photo(user_id, file_id, protect_content=True)
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
                                # Обрабатываем изображение для мобильных устройств
                                resized_path = await self._resize_image_for_mobile(image_file)
                                image_path_to_use = resized_path if resized_path else image_file
                                photo_file = FSInputFile(image_path_to_use)
                                await self.bot.send_photo(user_id, photo_file, protect_content=True)
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
                        # Если нет текста, используем только разделитель для визуального расширения блока медиа
                        caption_text = None
                        remaining_text = None
                    
                    # Добавляем разделитель к caption для визуального расширения блока медиа
                    caption_with_separator = self._add_media_separator(caption_text)
                    await self.bot.send_photo(user_id, follow_up_photo_file_id, caption=caption_with_separator, reply_markup=persistent_keyboard if not remaining_text else None, protect_content=True)
                    logger.info(f"   ✅ Sent final message photo with text (file_id) for lesson 30")
                    photo_sent = True
                    
                    # Если есть остаток текста, отправляем его отдельным сообщением
                    if remaining_text:
                        await asyncio.sleep(0.5)
                        await self._safe_send_message(user_id, remaining_text, reply_markup=persistent_keyboard, protect_content=True)
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
                        # Обрабатываем изображение для мобильных устройств
                        resized_path = await self._resize_image_for_mobile(photo_path)
                        image_path_to_use = resized_path if resized_path else photo_path
                        photo_file = FSInputFile(image_path_to_use)
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
                            # Убираем разделители - если нет текста, caption = None
                            caption_text = None
                            remaining_text = None
                        
                        # Добавляем разделитель к caption для визуального расширения блока медиа
                        caption_with_separator = self._add_media_separator(caption_text)
                        await self.bot.send_photo(user_id, photo_file, caption=caption_with_separator, reply_markup=persistent_keyboard if not remaining_text else None, protect_content=True)
                        logger.info(f"   ✅ Sent final message photo with text (file path: {photo_path}) for lesson 30")
                        photo_sent = True
                        
                        # Если есть остаток текста, отправляем его отдельным сообщением
                        if remaining_text:
                            await asyncio.sleep(0.5)
                            await self._safe_send_message(user_id, remaining_text, reply_markup=persistent_keyboard, protect_content=True)
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
                    await self._safe_send_message(user_id, follow_up_text, reply_markup=persistent_keyboard, protect_content=True)
                    logger.info(f"   ✅ Sent final message text (no photo) for lesson 30")
                except Exception as text_error:
                    error_msg = str(text_error)
                    logger.error(f"   ❌ Error sending final message text for lesson 30: {error_msg}", exc_info=True)
                    # Пробуем отправить еще раз без клавиатуры
                    try:
                        await self._safe_send_message(user_id, follow_up_text, protect_content=True)
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
                return None, None
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
                        await self._safe_send_message(user_id, part, protect_content=True)
                        await asyncio.sleep(0.3)
                last_part = parts[-1]
                if last_part and last_part.strip():
                    await self._safe_send_message(user_id, last_part, reply_markup=persistent_keyboard, protect_content=True)
                elif persistent_keyboard:
                    await self.bot.send_message(user_id, KEYBOARD_ONLY_PLACEHOLDER, reply_markup=persistent_keyboard)
            else:
                await self._safe_send_message(user_id, text, reply_markup=persistent_keyboard, protect_content=True)

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
                    # Обрабатываем изображение для мобильных устройств
                    resized_path = await self._resize_image_for_mobile(photo_path)
                    image_path_to_use = resized_path if resized_path else photo_path
                    await self.bot.send_photo(
                        user_id,
                        FSInputFile(image_path_to_use),
                        caption=caption,
                        reply_markup=persistent_keyboard if (send_keyboard and not remaining) else None,
                        protect_content=True
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
                return None, None
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
                        await self._safe_send_message(user_id, part, protect_content=True)
                        await asyncio.sleep(0.3)
                last_part = parts[-1]
                if last_part and last_part.strip():
                    await self._safe_send_message(user_id, last_part, reply_markup=persistent_keyboard, protect_content=True)
                elif persistent_keyboard:
                    await self.bot.send_message(user_id, KEYBOARD_ONLY_PLACEHOLDER, reply_markup=persistent_keyboard)
            else:
                await self._safe_send_message(user_id, text, reply_markup=persistent_keyboard, protect_content=True)

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
                    # Обрабатываем изображение для мобильных устройств
                    resized_path = await self._resize_image_for_mobile(photo_path)
                    image_path_to_use = resized_path if resized_path else photo_path
                    await self.bot.send_photo(
                        user_id,
                        FSInputFile(image_path_to_use),
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
        
        # Фильтруем уроки: показываем только те, которые уже были доставлены пользователю
        # Показываем уроки от 0 до current_day включительно (т.е. уже пришедшие уроки)
        available_lessons = {}
        for day_str, lesson_data in all_lessons.items():
            try:
                day = int(day_str)
                # Показываем только уроки, которые уже были доставлены (от 0 до current_day)
                if day <= user.current_day:
                    available_lessons[day_str] = lesson_data
            except (ValueError, TypeError):
                # Пропускаем нечисловые ключи
                continue
        
        # Создаем клавиатуру навигатора только с доступными уроками
        keyboard = create_navigator_keyboard(available_lessons, user.current_day)
        navigator_text = format_navigator_message()
        
        # Отправляем сообщение в зависимости от типа объекта
        persistent_keyboard = self._create_persistent_keyboard()
        if isinstance(message_or_callback, CallbackQuery):
            await message_or_callback.message.answer(navigator_text, reply_markup=keyboard)
            # Устанавливаем постоянную клавиатуру отдельным сообщением (используем пробел)
            try:
                await message_or_callback.message.answer(" ", reply_markup=persistent_keyboard)
            except Exception as e:
                logger.warning(f"   ⚠️ Failed to send persistent keyboard: {e}")
        else:
            # Для Message используем message.answer()
            await message_or_callback.answer(navigator_text, reply_markup=keyboard)
            # Устанавливаем постоянную клавиатуру отдельным сообщением (используем пробел)
            try:
                await message_or_callback.answer(" ", reply_markup=persistent_keyboard)
            except Exception as e:
                logger.warning(f"   ⚠️ Failed to send persistent keyboard: {e}")
        
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
        
        # В навигаторе показываем ПОЛНУЮ версию урока (intro/about/task/media).
        await send_typing_action(self.bot, user_id, 0.8)
        await self._send_lesson_from_json(user, lesson_data, day, skip_intro=False, skip_about_me=False)
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
        # Log activity
        try:
            await self.db.log_user_activity(callback.from_user.id, "course", "submit_assignment_click", "assignments")
        except Exception:
            pass
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
        
        # Best-effort lesson metadata load (submission should work even if JSON is missing)
        lesson_data = None
        try:
            lesson_data = self.lesson_loader.get_lesson(day_from_callback) if self.lesson_loader else None
        except Exception:
            lesson_data = None

        # IMPORTANT: once user clicks "submit assignment", stop mentor reminders for this day.
        # This matches product requirement: reminders continue until the user starts submission flow.
        try:
            await self.db.mark_assignment_intent(user_id, day_from_callback)
        except Exception as e:
            logger.warning(f"   ⚠️ Could not mark assignment intent for user={user_id} day={day_from_callback}: {e}")
        
        # Получаем информацию об уроке (best-effort)
        lesson_title = lesson_data.get("title", f"День {day_from_callback}") if lesson_data else f"День {day_from_callback}"
        safe_lesson_title = html.escape(lesson_title)

        # Start "one message" assignment capture flow
        self._user_assignment_context[user_id] = {
            "lesson_day": day_from_callback,
            "waiting_for_assignment": True,
        }
        
        await callback.message.answer(
            f"<b>Отправить задание для {safe_lesson_title}</b>\n\n"
            "Отправьте ответ <b>одним сообщением</b>: текстом, фото, видео, документом или голосовым.\n\n"
            "<i>Чтобы добавить ещё — нажмите 📝 и отправьте дополнение.</i>"
        )
    
    async def handle_ask_question(self, callback: CallbackQuery):
        """Handle question button click - immediately ready to receive question."""
        await callback.answer()
        
        user_id = callback.from_user.id
        user = await self.user_service.get_user(user_id)
        
        if not user or not user.has_access():
            await callback.message.answer("❌ У вас нет доступа к этому курсу.")
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
        
        # Start "one message" question capture flow
        self._user_question_context[user_id] = {
            "lesson_day": day_from_callback,
            "waiting_for_question": True,
        }
        
        await callback.message.answer(
            f"<b>Задать вопрос</b>\n\n"
            f"Отправьте вопрос по <b>Дню {day_from_callback}</b> <b>одним сообщением</b> (можно голосовым).\n\n"
            f"Сообщение уйдёт кураторам, они ответят вам прямо сюда.\n\n"
            f"<i>Совет: чем конкретнее вопрос, тем быстрее вы получите ответ.</i>"
        )
    
    async def _resize_image_for_mobile(self, image_path: Path) -> Optional[Path]:
        """
        Изменяет размер изображения для мобильных устройств (ширина = MOBILE_SCREEN_WIDTH).
        Сохраняет пропорции изображения.
        
        Args:
            image_path: Путь к исходному изображению
        
        Returns:
            Path к обработанному изображению, или None если обработка не требуется или не удалась
        """
        try:
            from PIL import Image
        except ImportError:
            logger.warning("   ⚠️ PIL/Pillow not available, skipping image resize")
            return None
        
        if not image_path.exists():
            logger.warning(f"   ⚠️ Image file not found: {image_path}")
            return None
        
        try:
            # Открываем изображение
            with Image.open(image_path) as img:
                original_width, original_height = img.size
                
                # Если изображение уже меньше или равно ширине мобильного экрана, не обрабатываем
                if original_width <= MOBILE_SCREEN_WIDTH:
                    logger.debug(f"   ✅ Image size OK: {original_width}x{original_height} (width <= {MOBILE_SCREEN_WIDTH})")
                    return None
                
                # Вычисляем новую высоту с сохранением пропорций
                aspect_ratio = original_height / original_width
                new_width = MOBILE_SCREEN_WIDTH
                new_height = int(MOBILE_SCREEN_WIDTH * aspect_ratio)
                
                logger.info(f"   📷 Resizing image: {original_width}x{original_height} -> {new_width}x{new_height}")
                
                # Изменяем размер изображения с высоким качеством
                resized_img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
                
                # Создаем путь для обработанного изображения
                resized_dir = image_path.parent / "resized_mobile"
                resized_dir.mkdir(exist_ok=True)
                resized_path = resized_dir / f"mobile_{image_path.name}"
                
                # Сохраняем обработанное изображение
                # Сохраняем в том же формате, что и оригинал
                resized_img.save(resized_path, quality=95, optimize=True)
                
                logger.info(f"   ✅ Image resized and saved: {resized_path.name}")
                return resized_path
                
        except Exception as e:
            logger.error(f"   ❌ Error resizing image {image_path}: {e}", exc_info=True)
            return None
    
    async def _compress_video_if_needed(self, video_path: Path, max_size_mb: float = 45.0) -> Optional[Path]:
        """
        Сжимает видео, если оно превышает максимальный размер.
        
        Args:
            video_path: Путь к исходному видео
            max_size_mb: Максимальный размер в МБ (по умолчанию 45 МБ, чтобы быть ниже лимита 50 МБ)
        
        Returns:
            Path к сжатому видео, или None если сжатие не требуется или не удалось
        """
        if not video_path.exists():
            logger.warning(f"   ⚠️ Video file not found: {video_path}")
            return None
        
        # Проверяем размер файла
        file_size_mb = video_path.stat().st_size / (1024 * 1024)
        
        if file_size_mb <= max_size_mb:
            logger.info(f"   ✅ Video size OK: {file_size_mb:.2f} MB (limit: {max_size_mb} MB)")
            return None
        
        logger.info(f"   📹 Video too large: {file_size_mb:.2f} MB, compressing to {max_size_mb} MB...")
        
        # Создаем путь для сжатого видео
        compressed_dir = video_path.parent / "compressed"
        compressed_dir.mkdir(exist_ok=True)
        compressed_path = compressed_dir / f"compressed_{video_path.name}"
        
        # Если сжатое видео уже существует и актуально, используем его
        if compressed_path.exists():
            compressed_size_mb = compressed_path.stat().st_size / (1024 * 1024)
            if compressed_size_mb <= max_size_mb:
                logger.info(f"   ✅ Using existing compressed video: {compressed_size_mb:.2f} MB")
                return compressed_path
        
        # Проверяем наличие FFmpeg
        try:
            subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True, timeout=5)
        except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
            logger.error(f"   ❌ FFmpeg not found or not working. Cannot compress video.")
            return None
        
        try:
            # Вычисляем битрейт для достижения целевого размера
            # Примерная формула: bitrate = (target_size_mb * 8) / duration_seconds
            # Для безопасности используем консервативный битрейт
            target_bitrate = "2000k"  # 2 Мбит/с - хорошее качество для большинства видео
            
            # Команда FFmpeg для сжатия
            # Используем H.264 кодек с оптимизацией для веб-потоков
            cmd = [
                "ffmpeg",
                "-i", str(video_path),
                "-c:v", "libx264",
                "-preset", "medium",  # Баланс между скоростью и качеством
                "-crf", "28",  # Качество (18-28, чем больше, тем меньше размер)
                "-maxrate", target_bitrate,
                "-bufsize", f"{int(target_bitrate[:-1]) * 2}k",
                "-c:a", "aac",
                "-b:a", "128k",  # Аудио битрейт
                "-movflags", "+faststart",  # Оптимизация для стриминга
                "-y",  # Перезаписать существующий файл
                str(compressed_path)
            ]
            
            logger.info(f"   🔄 Compressing video: {video_path.name} -> {compressed_path.name}")
            logger.info(f"   📹 Command: {' '.join(cmd)}")
            
            # Запускаем сжатие
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            stdout, stderr = await process.communicate()
            
            if process.returncode != 0:
                logger.error(f"   ❌ FFmpeg compression failed: {stderr.decode()}")
                return None
            
            # Проверяем размер сжатого файла
            if compressed_path.exists():
                compressed_size_mb = compressed_path.stat().st_size / (1024 * 1024)
                logger.info(f"   ✅ Video compressed: {file_size_mb:.2f} MB -> {compressed_size_mb:.2f} MB")
                
                # Если все еще слишком большое, пробуем более агрессивное сжатие
                if compressed_size_mb > max_size_mb:
                    logger.warning(f"   ⚠️ Compressed video still too large: {compressed_size_mb:.2f} MB")
                    # Пробуем более агрессивное сжатие
                    cmd_aggressive = [
                        "ffmpeg",
                        "-i", str(video_path),
                        "-c:v", "libx264",
                        "-preset", "fast",
                        "-crf", "32",  # Более высокое значение = меньше размер
                        "-maxrate", "1500k",
                        "-bufsize", "3000k",
                        "-vf", "scale='min(1280,iw)':'min(720,ih)':force_original_aspect_ratio=decrease",  # Ограничиваем разрешение
                        "-c:a", "aac",
                        "-b:a", "96k",
                        "-movflags", "+faststart",
                        "-y",
                        str(compressed_path)
                    ]
                    
                    logger.info(f"   🔄 Trying aggressive compression...")
                    process2 = await asyncio.create_subprocess_exec(
                        *cmd_aggressive,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE
                    )
                    
                    stdout2, stderr2 = await process2.communicate()
                    
                    if process2.returncode == 0 and compressed_path.exists():
                        compressed_size_mb = compressed_path.stat().st_size / (1024 * 1024)
                        logger.info(f"   ✅ Aggressive compression result: {compressed_size_mb:.2f} MB")
                
                if compressed_size_mb <= max_size_mb:
                    return compressed_path
                else:
                    logger.warning(f"   ⚠️ Video still too large after compression: {compressed_size_mb:.2f} MB")
                    return None
            
            return None
            
        except Exception as e:
            logger.error(f"   ❌ Error compressing video: {e}", exc_info=True)
            return None
    
    @staticmethod
    def _add_media_separator(caption: Optional[str] = None) -> Optional[str]:
        """
        Добавляет визуальный разделитель к caption медиафайла для расширения блока.
        
        Args:
            caption: Исходный caption (может быть None)
        
        Returns:
            caption с добавленным разделителем внизу, или только разделитель если caption был None
        """
        if caption:
            # Если есть caption, добавляем разделитель внизу
            return f"{caption}\n\n{MEDIA_SEPARATOR}"
        else:
            # Если caption нет, возвращаем только разделитель
            return MEDIA_SEPARATOR
    
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
            
            # Добавляем разделитель к caption для визуального расширения блока медиа
            original_caption = media_item.get("caption")  # Берем caption из данных медиа, если есть
            caption = self._add_media_separator(original_caption)
            
            # Используем file_id если есть (самый быстрый способ)
            if file_id:
                if media_type == "photo":
                    await self.bot.send_photo(user_id, file_id, caption=caption, protect_content=True)
                elif media_type == "video":
                    # Используем ширину мобильного экрана для правильного отображения на мобильных устройствах
                    # Telegram автоматически масштабирует высоту пропорционально
                    await self.bot.send_video(
                        user_id, 
                        file_id, 
                        caption=caption, 
                        width=MOBILE_SCREEN_WIDTH,
                        supports_streaming=True, 
                        protect_content=True
                    )
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
                video_path_to_use = None
                
                for test_path in possible_paths:
                    if test_path.exists() and test_path.is_file():
                        if media_type == "video":
                            # Для видео проверяем размер и сжимаем при необходимости
                            compressed_path = await self._compress_video_if_needed(test_path)
                            video_path_to_use = compressed_path if compressed_path else test_path
                            media_file = FSInputFile(video_path_to_use)
                        else:
                            # Для изображений изменяем размер для мобильных устройств
                            resized_path = await self._resize_image_for_mobile(test_path)
                            image_path_to_use = resized_path if resized_path else test_path
                            media_file = FSInputFile(image_path_to_use)
                        break
                
                if media_file:
                    # Добавляем разделитель к caption для визуального расширения блока медиа
                    original_caption = media_item.get("caption")  # Берем caption из данных медиа, если есть
                    caption = self._add_media_separator(original_caption)
                    if media_type == "photo":
                        await self.bot.send_photo(user_id, media_file, caption=caption, protect_content=True)
                    elif media_type == "video":
                        # Используем ширину мобильного экрана для правильного отображения на мобильных устройствах
                        # Telegram автоматически масштабирует высоту пропорционально
                        try:
                            await self.bot.send_video(
                                user_id, 
                                media_file, 
                                caption=caption, 
                                width=MOBILE_SCREEN_WIDTH,
                                supports_streaming=True, 
                                protect_content=True
                            )
                        except Exception as video_error:
                            error_msg = str(video_error).lower()
                            if "entity too large" in error_msg or "file too large" in error_msg:
                                # Если даже после сжатия файл слишком большой, отправляем ссылку на Google Drive
                                original_file_id = media_item.get("file_id")
                                if original_file_id:
                                    drive_link = f"https://drive.google.com/file/d/{original_file_id}/view"
                                    await self._safe_send_message(
                                        user_id,
                                        f"📹 <b>Видео слишком большое для прямой отправки</b>\n\n"
                                        f"Ссылка на видео:\n"
                                        f"<a href=\"{drive_link}\">Открыть видео в Google Drive</a>"
                                    )
                                    logger.info(f"   ✅ Sent Google Drive link for large video after compression attempt")
                                    return True
                                else:
                                    logger.error(f"   ❌ Video still too large after compression and no Drive link available")
                                    raise
                            else:
                                raise
                    await asyncio.sleep(0.2)  # Минимальная пауза для стабильности
                    return True
        except Exception as e:
            # Ошибка на одном медиа не прерывает урок
            logger.debug(f"   ⚠️ Медиа не отправлено для урока {day}: {e}")
            return False
        
        return False

    _URL_RE = re.compile(r"(https?://[^\s<>\"]+)", re.IGNORECASE)

    @staticmethod
    def _clean_url(url: str) -> str:
        if not url:
            return ""
        u = url.strip()
        while u and u[-1] in ")]}>,.!?":
            u = u[:-1]
        return u

    @staticmethod
    def _youtube_video_id(url: str) -> Optional[str]:
        try:
            p = urlparse(url)
            host = (p.netloc or "").lower()
            path = (p.path or "").strip("/")

            if "youtu.be" in host:
                vid = path.split("/")[0] if path else ""
                return vid or None

            if "youtube.com" in host:
                if path.startswith("watch"):
                    q = parse_qs(p.query or "")
                    vid = (q.get("v") or [None])[0]
                    return vid or None
                if path.startswith("embed/") or path.startswith("shorts/"):
                    parts = path.split("/")
                    vid = parts[1] if len(parts) > 1 else None
                    return vid or None
        except Exception:
            return None
        return None

    @staticmethod
    def _is_direct_image_url(url: str) -> bool:
        try:
            p = urlparse(url)
            ext = (Path(p.path).suffix or "").lower()
            return ext in {".jpg", ".jpeg", ".png", ".webp", ".gif"}
        except Exception:
            return False

    @staticmethod
    def _is_direct_video_url(url: str) -> bool:
        try:
            p = urlparse(url)
            ext = (Path(p.path).suffix or "").lower()
            return ext in {".mp4", ".mov", ".webm"}
        except Exception:
            return False

    async def _download_media_from_url(
        self,
        session: aiohttp.ClientSession,
        url: str,
        *,
        timeout_s: float = 20.0,
        max_image_bytes: int = 12 * 1024 * 1024,
        max_video_bytes: int = 45 * 1024 * 1024,
    ) -> tuple[str, bytes, str, str]:
        """
        Download media (image/video) from URL into memory with strict caps.

        Returns: (kind, data, content_type, filename), where kind is "image" or "video".
        """
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("Unsupported URL scheme")

        req_timeout = aiohttp.ClientTimeout(total=timeout_s)
        async with session.get(url, allow_redirects=True, timeout=req_timeout) as resp:
            resp.raise_for_status()

            content_type = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            filename = Path(urlparse(str(resp.url)).path).name or Path(parsed.path).name or "file"

            if content_type.startswith("image/"):
                cap = int(max_image_bytes)
                kind = "image"
            elif content_type.startswith("video/"):
                cap = int(max_video_bytes)
                kind = "video"
            else:
                # Don't waste bandwidth on html/text/etc.
                raise ValueError("Not a media URL")

            size_hdr = resp.headers.get("Content-Length")
            if size_hdr and size_hdr.isdigit() and int(size_hdr) > cap:
                raise ValueError("File too large")

            buf = bytearray()
            async for chunk in resp.content.iter_chunked(256 * 1024):
                if not chunk:
                    continue
                buf.extend(chunk)
                if len(buf) > cap:
                    raise ValueError("File too large")

            data = bytes(buf)
            if not data:
                raise ValueError("Empty download")

            return kind, data, content_type, filename

    def _format_text_for_display(self, text: str) -> str:
        """
        Форматирует текст для отображения: убирает лишние отступы, делает все ровно и строго,
        хорошо читается. Сохраняет структуру (заголовки, вопросы, итоги).
        """
        if not text:
            return text
        
        # Разбиваем на строки
        lines = text.split('\n')
        formatted_lines = []
        prev_line_empty = False
        
        for i, line in enumerate(lines):
            stripped = line.strip()
            current_line_empty = not stripped
            
            # Если строка пустая
            if current_line_empty:
                # Убираем множественные пустые строки подряд (максимум 1)
                if not prev_line_empty:
                    formatted_lines.append('')
                prev_line_empty = True
                continue
            
            prev_line_empty = False
            
            # Убираем все отступы в начале строки - все строки выравниваем по левому краю
            formatted_line = stripped
            
            # Определяем тип строки для правильной расстановки пустых строк
            is_heading = (
                stripped.startswith('📘') or
                (stripped.startswith('💠') and '#' in stripped) or
                stripped.startswith('Тактика №') or
                (stripped.startswith('День ') and len(stripped.split()) <= 3)
            )
            
            is_question = stripped.startswith('❔') or stripped.startswith('?')
            is_summary = stripped.startswith('=>') or stripped.startswith('Выход на')
            
            # Добавляем пустую строку перед заголовками и итогами для лучшей читаемости
            if is_heading or is_summary:
                if formatted_lines and formatted_lines[-1].strip():
                    formatted_lines.append('')
            
            formatted_lines.append(formatted_line)
        
        # Убираем лишние пустые строки в начале и конце
        while formatted_lines and not formatted_lines[0].strip():
            formatted_lines.pop(0)
        while formatted_lines and not formatted_lines[-1].strip():
            formatted_lines.pop()
        
        # Финальная проверка: убираем множественные пустые строки подряд (максимум 1)
        result_lines = []
        prev_was_empty = False
        for line in formatted_lines:
            is_empty = not line.strip()
            if is_empty:
                if not prev_was_empty:
                    result_lines.append('')
                prev_was_empty = True
            else:
                result_lines.append(line)
                prev_was_empty = False
        
        return '\n'.join(result_lines)

    async def _send_previews_from_text(
        self,
        user_id: int,
        text: str,
        *,
        seen: Optional[set[str]] = None,
        limit: int = 6,
        day: Optional[int] = None,
    ):
        """
        Отправляет только медиа из URL в тексте, НЕ отправляет сам текст.
        Возвращает множество отправленных URL для последующего удаления из текста.
        
        Args:
            user_id: ID пользователя
            text: Текст с URL
            seen: Множество уже обработанных URL
            limit: Максимальное количество медиа для отправки
            day: Номер урока (для специальной обработки урока 11 - YouTube ссылки остаются в тексте)
        """
        if not text:
            return set()

        if seen is None:
            seen = set()

        # Находим все URL в тексте с их позициями
        url_positions = []
        for match in self._URL_RE.finditer(text):
            url = self._clean_url(match.group(0))
            if url and url not in seen:
                url_positions.append({
                    'url': url,
                    'start': match.start(),
                    'end': match.end(),
                    'line': self._get_line_for_position(text, match.start())
                })
        
        if not url_positions:
            # Нет URL, ничего не отправляем
            return set()

        # Сортируем по позиции в тексте
        url_positions.sort(key=lambda x: x['start'])

        # Отправляем только медиа, не отправляем текст
        sent_urls = set()
        headers = {"User-Agent": "Mozilla/5.0"}
        connector = aiohttp.TCPConnector(limit=4, ttl_dns_cache=300)
        async with aiohttp.ClientSession(headers=headers, connector=connector) as session:
            for url_info in url_positions[:limit]:  # Ограничиваем количество медиа
                url = url_info['url']
                line = url_info.get('line', '')
                
                if url in sent_urls:
                    continue
                
                caption = url
                # Use a short per-line caption when it looks like a "label: link" format.
                if line and len(line) <= 180 and (":" in line or "фрагмент" in line.lower()):
                    caption = line
                if len(caption) > 900:
                    caption = caption[:900] + "…"

                vid = self._youtube_video_id(url)
                if vid:
                    # Для урока 11: YouTube ссылки не отправляем отдельно, оставляем в тексте как гиперссылки
                    if day == 11 or str(day) == "11":
                        # Пропускаем YouTube ссылки для урока 11 - они останутся в тексте как гиперссылки
                        logger.info(f"   ⏭️ Skipping YouTube link for lesson 11: {url} (will remain in text as hyperlink)")
                        continue
                    
                    try:
                        # For YouTube (and similar pages), let Telegram build a native link preview
                        message_text = line if (line and url in line and len(line) <= 900) else url
                        await self.bot.send_message(
                            user_id,
                            message_text,
                            disable_web_page_preview=False,
                            parse_mode=None,
                            protect_content=True
                        )
                        sent_urls.add(url)
                        seen.add(url)
                    except Exception:
                        try:
                            await self.bot.send_message(
                                user_id,
                                url,
                                disable_web_page_preview=False,
                                parse_mode=None,
                                protect_content=True
                            )
                            sent_urls.add(url)
                            seen.add(url)
                        except Exception:
                            pass
                    continue

                if self._is_direct_image_url(url):
                    try:
                        kind, data, content_type, filename = await self._download_media_from_url(
                            session, url, timeout_s=20.0
                        )
                        if kind != "image":
                            raise ValueError("Not an image")
                        if not filename.lower().endswith((".jpg", ".jpeg", ".png", ".webp", ".gif")):
                            filename = "image.png" if content_type == "image/png" else "image.jpg"
                        photo = BufferedInputFile(data, filename=filename)
                        await self.bot.send_photo(
                            user_id, 
                            photo, 
                            caption=(caption if caption != url else None), 
                            protect_content=True
                        )
                        sent_urls.add(url)
                        seen.add(url)
                    except Exception:
                        pass
                    continue

                if self._is_direct_video_url(url):
                    try:
                        kind, data, content_type, filename = await self._download_media_from_url(
                            session, url, timeout_s=35.0
                        )
                        if kind != "video":
                            raise ValueError("Not a video")
                        if not filename.lower().endswith((".mp4", ".mov", ".webm")):
                            filename = "video.mp4"
                        video = BufferedInputFile(data, filename=filename)
                        try:
                            await self.bot.send_video(
                                user_id,
                                video,
                                caption=(caption if caption != url else None),
                                width=MOBILE_SCREEN_WIDTH,
                                supports_streaming=True,
                                protect_content=True
                            )
                        except Exception:
                            await self.bot.send_document(
                                user_id, video, caption=(caption if caption != url else None), protect_content=True
                            )
                        sent_urls.add(url)
                        seen.add(url)
                    except Exception:
                        pass
                    continue

                # Generic media URLs: download once, decide by Content-Type
                try:
                    kind, data, content_type, filename = await self._download_media_from_url(
                        session, url, timeout_s=25.0
                    )
                    if kind == "image":
                        photo = BufferedInputFile(data, filename=(filename or "image.jpg"))
                        await self.bot.send_photo(
                            user_id, 
                            photo, 
                            caption=(caption if caption != url else None), 
                            protect_content=True
                        )
                        sent_urls.add(url)
                        seen.add(url)
                    elif kind == "video":
                        video = BufferedInputFile(data, filename=(filename or "video.mp4"))
                        try:
                            await self.bot.send_video(
                                user_id,
                                video,
                                caption=(caption if caption != url else None),
                                width=MOBILE_SCREEN_WIDTH,
                                supports_streaming=True,
                                protect_content=True
                            )
                        except Exception:
                            await self.bot.send_document(
                                user_id, video, caption=(caption if caption != url else None), protect_content=True
                            )
                        sent_urls.add(url)
                        seen.add(url)
                    else:
                        await self.bot.send_message(
                            user_id,
                            url,
                            disable_web_page_preview=False,
                            parse_mode=None,
                            protect_content=True
                        )
                        sent_urls.add(url)
                        seen.add(url)
                except Exception:
                    pass
        
        return sent_urls

    def _get_line_for_position(self, text: str, position: int) -> str:
        """Получает строку текста, содержащую указанную позицию."""
        lines = text.splitlines()
        current_pos = 0
        for line in lines:
            line_end = current_pos + len(line) + 1  # +1 for newline
            if current_pos <= position < line_end:
                return line.strip()
            current_pos = line_end
        return ""

    def _collect_preview_urls(self, text: str, *, seen: Optional[set[str]] = None, limit: int = 6) -> list[str]:
        """
        Collect URLs that would be sent as preview blocks.
        Keeps ordering and respects the same limit/seen logic.
        """
        if not text:
            return []
        if seen is None:
            seen = set()

        candidates: list[tuple[str, str]] = []
        for raw_line in (text or "").splitlines():
            line = (raw_line or "").strip()
            if not line:
                continue
            for u in self._URL_RE.findall(line):
                url = self._clean_url(u)
                if not url:
                    continue
                candidates.append((url, line))

        if not candidates:
            urls = [self._clean_url(u) for u in self._URL_RE.findall(text or "")]
            candidates = [(u, u) for u in urls if u]

        out: list[str] = []
        for url, _line in candidates:
            if url in seen:
                continue
            out.append(url)
            if len(out) >= int(limit):
                break
        return out

    def _strip_url_only_lines(self, text: str, urls: set[str]) -> str:
        """
        Remove lines that contain only a URL (to avoid duplicate link + preview blocks).
        Also removes URLs from lines that end with a URL (common pattern: "text https://...")
        """
        if not text or not urls:
            return text
        cleaned: list[str] = []
        for raw_line in text.splitlines():
            line = (raw_line or "").strip()
            if not line:
                cleaned.append(raw_line)
                continue
            # Check if line is exactly a URL
            if line in urls:
                continue
            # Check if line ends with a URL (remove the URL part)
            line_cleaned = line
            for url in urls:
                # Remove URL if it's at the end of the line (with optional whitespace)
                if line.rstrip().endswith(url):
                    # Remove URL and any trailing whitespace before it
                    line_cleaned = line[:line.rfind(url)].rstrip()
                    break
                # Also check if URL is at the start of the line
                if line.lstrip().startswith(url):
                    # Remove URL and any leading whitespace after it
                    remaining = line[line.find(url) + len(url):].lstrip()
                    line_cleaned = remaining if remaining else ""
                    break
            # Only add non-empty lines
            if line_cleaned and line_cleaned.strip():
                cleaned.append(line_cleaned)
        return "\n".join(cleaned)
    # Assignment headings sometimes come with a leading emoji/icon, e.g. "🔗 #Задание 28".
    # We allow optional non-word prefix before the Markdown heading markers.
    _ASSIGNMENT_HEADING_RE = re.compile(
        r"^\s*(?:[^\w#]*\s*)?(?:#{1,6}\s*)?(?:[⏺️●\-–—]?\s*)?задание\b",
        re.IGNORECASE,
    )

    async def _send_text_with_inline_media(self, user_id: int, text: str, media_markers: Dict[str, Dict[str, Any]], day: int, keyboard: Optional[InlineKeyboardMarkup] = None) -> set:
        """
        Отправляет текст с встроенными медиа-файлами в местах маркеров.
        
        Args:
            user_id: ID пользователя
            text: Текст урока с маркерами вида [MEDIA_fileid_index]
            media_markers: Словарь маркеров -> информация о медиа
            day: Номер дня урока
            keyboard: Опциональная клавиатура, которая будет добавлена к последнему сообщению
        
        Returns:
            set: Множество кортежей (file_id, normalized_path, filename) для отправленных медиа
        """
        from pathlib import Path  # Импортируем Path для использования в функции
        logger.info(f"   📎 _send_text_with_inline_media called for user {user_id}, day {day}")
        logger.info(f"   📎 Text length: {len(text)}, media_markers count: {len(media_markers) if media_markers else 0}")
        
        sent_media_keys = set()  # Отслеживаем отправленные медиа
        
        if not text:
            logger.warning(f"   ⚠️ Empty text provided to _send_text_with_inline_media")
            return sent_media_keys
        
        # Находим все маркеры в тексте
        import re
        marker_pattern = r'\[(MEDIA_[a-zA-Z0-9_-]+)\]'
        markers = re.findall(marker_pattern, text)
        
        logger.info(f"   📎 Found {len(markers)} markers in text: {markers}")
        if media_markers:
            logger.info(f"   📎 Available media_markers keys: {list(media_markers.keys())}")
        
        if not markers:
            # Нет маркеров, отправляем текст как есть
            logger.info(f"   📎 No markers found, sending text as-is")
            if keyboard:
                # Если есть клавиатура, отправляем текст с клавиатурой (с водяным знаком)
                await self._safe_send_message(user_id, text, reply_markup=keyboard, protect_content=True)
            else:
                await self._safe_send_message(user_id, text, protect_content=True)
            return sent_media_keys
        
        # Разбиваем текст на части по маркерам
        # re.split с группой в паттерне возвращает список: [text_before, marker, text_after, marker, ...]
        parts = re.split(marker_pattern, text)
        
        logger.info(f"   📎 Split text into {len(parts)} parts")
        
        # Отслеживаем последнюю отправленную часть (текст или медиа) для добавления клавиатуры
        last_sent_message_id = None
        keyboard_attached = False  # Флаг, указывающий, была ли клавиатура уже прикреплена
        
        for i, part in enumerate(parts):
            if not part.strip():
                continue
            
            # Проверяем, является ли часть маркером
            # В re.split с группой в паттерне маркеры находятся в нечетных позициях (1, 3, 5...)
            is_marker = (i % 2 == 1) and part in markers
            
            if is_marker:
                if part not in media_markers:
                    logger.error(f"   ❌ Marker {part} found in text but not in media_markers!")
                    logger.error(f"   ❌ Available media_markers: {list(media_markers.keys()) if media_markers else 'None'}")
                    logger.error(f"   ❌ This means the marker was not created during sync or media_markers are missing from lesson_data")
                    # Пропускаем маркер, не отправляем текст с маркером
                    continue
                
                # Отправляем медиа-файл
                media_info = media_markers[part]
                media_type = media_info.get("type")
                media_path = media_info.get("path")
                
                try:
                    logger.info(f"   📎 Processing media marker {part} (type: {media_type}, path: {media_path})")
                    # Сначала проверяем, есть ли сохраненный file_id в базе
                    cached_file_id = await self.db.get_media_file_id(part, day)
                    
                    if cached_file_id:
                        # Используем сохраненный file_id (файл уже в контексте Telegram)
                        logger.info(f"   💾 Found cached file_id for marker {part}, using it")
                        try:
                            # Проверяем, является ли это последней частью
                            is_last = (i == len(parts) - 1) or (i == len(parts) - 2 and not parts[i + 1].strip() if i + 1 < len(parts) else True)
                            
                            if media_type == "photo":
                                # Добавляем разделитель к caption для визуального расширения блока медиа
                                caption = self._add_media_separator()
                                sent_message = await self.bot.send_photo(
                                    user_id, 
                                    cached_file_id,
                                    caption=caption,
                                    reply_markup=keyboard if (is_last and keyboard and not keyboard_attached) else None,
                                    protect_content=True
                                )
                                if is_last and keyboard and not keyboard_attached:
                                    keyboard_attached = True
                                logger.info(f"   ✅ Sent inline photo from cache (file_id) for marker {part}, lesson {day}")
                            elif media_type == "video":
                                # Добавляем разделитель к caption для визуального расширения блока медиа
                                caption = self._add_media_separator()
                                sent_message = await self.bot.send_video(
                                    user_id, 
                                    cached_file_id,
                                    caption=caption,
                                    width=MOBILE_SCREEN_WIDTH,
                                    reply_markup=keyboard if (is_last and keyboard and not keyboard_attached) else None
                                )
                                if is_last and keyboard and not keyboard_attached:
                                    keyboard_attached = True
                                logger.info(f"   ✅ Sent inline video from cache (file_id) for marker {part}, lesson {day}")
                            
                            if sent_message:
                                last_sent_message_id = sent_message.message_id
                                # Отслеживаем отправленное медиа
                                normalized_path = str(Path(media_path)).replace('\\', '/') if media_path else ""
                                filename = Path(media_path).name if media_path else ""
                                sent_media_keys.add((str(cached_file_id), normalized_path, filename))
                        except Exception as cache_error:
                            # Если file_id недействителен, загружаем файл заново
                            logger.warning(f"   ⚠️ Cached file_id invalid for {part}, re-uploading: {cache_error}")
                            cached_file_id = None
                    
                    if not cached_file_id:
                        # Загружаем файл с диска и сохраняем file_id
                        from aiogram.types import FSInputFile
                        
                        # Определяем абсолютный путь к файлу
                        # media_path может быть относительным (от project_root) или абсолютным
                        file_path = Path(media_path)
                        if not file_path.is_absolute():
                            # Если путь относительный, делаем его абсолютным относительно рабочей директории
                            file_path = Path.cwd() / media_path
                        
                        # Проверяем существование файла
                        if not file_path.exists():
                            logger.error(f"   ❌ Media file not found: {file_path} (original path: {media_path})")
                            logger.error(f"   ❌ Current working directory: {Path.cwd()}")
                            logger.error(f"   ❌ Media info: {media_info}")
                            # Пытаемся найти файл в альтернативных местах
                            alt_paths = [
                                Path.cwd() / "media" / media_path,
                                Path("/app") / media_path,
                                Path("/app/media") / media_path,
                            ]
                            found = False
                            for alt_path in alt_paths:
                                if alt_path.exists():
                                    file_path = alt_path
                                    logger.info(f"   ✅ Found media file at alternative path: {file_path}")
                                    found = True
                                    break
                            
                            if not found:
                                logger.error(f"   ❌ Could not find media file {media_path} in any location")
                                # Отправляем текстовое сообщение об ошибке вместо файла
                                await self._safe_send_message(
                                    user_id, 
                                    f"⚠️ Не удалось загрузить медиа-файл: {media_info.get('name', 'файл')}"
                                )
                                # Пропускаем этот маркер, продолжаем с текстом
                                continue
                        
                        try:
                            if media_type == "photo":
                                # Обрабатываем изображение для мобильных устройств
                                resized_path = await self._resize_image_for_mobile(file_path)
                                image_path_to_use = resized_path if resized_path else file_path
                                photo_file = FSInputFile(image_path_to_use)
                                # Если это последняя часть и есть клавиатура, добавляем её к фото
                                is_last = (i == len(parts) - 1) or (i == len(parts) - 2 and not parts[i + 1].strip() if i + 1 < len(parts) else True)
                                # Добавляем разделитель к caption для визуального расширения блока медиа
                                caption = self._add_media_separator()
                                sent_message = await self.bot.send_photo(
                                    user_id, 
                                    photo_file,
                                    caption=caption,
                                    reply_markup=keyboard if (is_last and keyboard and not keyboard_attached) else None,
                                    protect_content=True
                                )
                                if is_last and keyboard and not keyboard_attached:
                                    keyboard_attached = True
                                last_sent_message_id = sent_message.message_id
                                # Сохраняем file_id для фото (может быть список, берем самое большое)
                                if sent_message.photo:
                                    file_id = sent_message.photo[-1].file_id
                                    await self.db.save_media_file_id(part, day, media_type, file_id)
                                    # Отслеживаем отправленное медиа
                                    normalized_path = str(Path(media_path)).replace('\\', '/') if media_path else ""
                                    filename = Path(media_path).name if media_path else ""
                                    sent_media_keys.add((str(file_id), normalized_path, filename))
                                    logger.info(f"   ✅ Sent inline photo and cached file_id for marker {part}, lesson {day}")
                            elif media_type == "video":
                                # Проверяем размер и сжимаем видео при необходимости
                                compressed_path = await self._compress_video_if_needed(file_path)
                                video_path_to_use = compressed_path if compressed_path else file_path
                                
                                # Если это последняя часть и есть клавиатура, добавляем её к видео
                                is_last = (i == len(parts) - 1) or (i == len(parts) - 2 and not parts[i + 1].strip() if i + 1 < len(parts) else True)
                                video_file = FSInputFile(video_path_to_use)
                                # Добавляем разделитель к caption для визуального расширения блока медиа
                                caption = self._add_media_separator()
                                sent_message = await self.bot.send_video(
                                    user_id, 
                                    video_file,
                                    caption=caption,
                                    width=MOBILE_SCREEN_WIDTH,
                                    reply_markup=keyboard if (is_last and keyboard and not keyboard_attached) else None,
                                    protect_content=True
                                )
                                if is_last and keyboard and not keyboard_attached:
                                    keyboard_attached = True
                                last_sent_message_id = sent_message.message_id
                                # Сохраняем file_id для видео
                                if sent_message.video:
                                    file_id = sent_message.video.file_id
                                    await self.db.save_media_file_id(part, day, media_type, file_id)
                                    # Отслеживаем отправленное медиа
                                    normalized_path = str(Path(media_path)).replace('\\', '/') if media_path else ""
                                    filename = Path(media_path).name if media_path else ""
                                    sent_media_keys.add((str(file_id), normalized_path, filename))
                                    logger.info(f"   ✅ Sent inline video and cached file_id for marker {part}, lesson {day}")
                        except Exception as send_error:
                            error_msg = str(send_error).lower()
                            if "entity too large" in error_msg or "file too large" in error_msg:
                                # Если даже после сжатия файл слишком большой, отправляем ссылку на Google Drive
                                original_file_id = media_info.get("file_id")
                                if original_file_id:
                                    drive_link = f"https://drive.google.com/file/d/{original_file_id}/view"
                                    await self._safe_send_message(
                                        user_id,
                                        f"📹 <b>Видео слишком большое для прямой отправки</b>\n\n"
                                        f"Ссылка на видео:\n"
                                        f"<a href=\"{drive_link}\">Открыть видео в Google Drive</a>"
                                    )
                                    logger.info(f"   ✅ Sent Google Drive link for large video after compression attempt")
                                else:
                                    logger.error(f"   ❌ Video still too large after compression and no Drive link available")
                                    await self._safe_send_message(
                                        user_id,
                                        f"⚠️ Видео слишком большое для отправки. "
                                        f"Максимальный размер: 50 МБ."
                                    )
                            else:
                                logger.error(f"   ❌ Error sending media file {file_path}: {send_error}", exc_info=True)
                                raise
                    
                    await asyncio.sleep(0.3)
                except Exception as e:
                    logger.warning(f"   ⚠️ Failed to send inline media from marker {part}: {e}")
            else:
                # Отправляем текстовую часть
                if part.strip():
                    # Если это последняя часть и есть клавиатура, добавляем её к тексту
                    is_last = i == len(parts) - 1
                    if is_last and keyboard and not keyboard_attached:
                        sent_message = await self.bot.send_message(
                            user_id, 
                            part.strip(), 
                            reply_markup=keyboard,
                            disable_web_page_preview=True,
                            protect_content=True
                        )
                        last_sent_message_id = sent_message.message_id
                        keyboard_attached = True
                    else:
                        await self._safe_send_message(user_id, part.strip(), protect_content=True)
                    await asyncio.sleep(0.2)
        
        # Если клавиатура не была добавлена к последнему сообщению, добавляем её отдельным сообщением
        # ТОЛЬКО если она не была уже прикреплена
        if keyboard and not keyboard_attached:
            if last_sent_message_id:
                try:
                    # Пытаемся отредактировать последнее сообщение, чтобы добавить клавиатуру
                    await self.bot.edit_message_reply_markup(
                        chat_id=user_id,
                        message_id=last_sent_message_id,
                        reply_markup=keyboard
                    )
                    logger.info(f"   ✅ Added keyboard to last message (message_id: {last_sent_message_id})")
                    keyboard_attached = True
                except Exception as e:
                    # Если не удалось отредактировать (например, это медиа без caption), отправляем клавиатуру отдельно
                    logger.warning(f"   ⚠️ Could not edit last message to add keyboard: {e}, sending separately")
                    await self._safe_send_message(
                        user_id,
                        "📝 <b>Задание</b>",
                        reply_markup=keyboard,
                        protect_content=True,
                        parse_mode="HTML"
                    )
                    keyboard_attached = True
            else:
                # Если не было отправлено ни одного сообщения, отправляем клавиатуру отдельно
                logger.warning(f"   ⚠️ No messages sent, sending keyboard separately")
                await self._safe_send_message(
                    user_id,
                    "📝 <b>Задание</b>",
                    reply_markup=keyboard,
                    protect_content=True,
                    parse_mode="HTML"
                )
                keyboard_attached = True
        
        return sent_media_keys
    
    def _split_assignment_from_text(self, text: str) -> tuple[str, str]:
        """
        Split a combined lesson text into (lesson_text, assignment_text).

        We treat a Markdown heading like "#Задание" (or variants) as the start of
        the assignment block.
        """
        if not text:
            return "", ""

        lines = (text or "").splitlines()
        for idx, raw in enumerate(lines):
            line = (raw or "").strip()
            if not line:
                continue
            if not self._ASSIGNMENT_HEADING_RE.match(line):
                continue

            # If there is a short "Задание:" line just before the heading, include it.
            start_idx = idx
            if idx > 0:
                prev = (lines[idx - 1] or "").strip().lower()
                if prev in {"задание", "задание:", "задание."}:
                    start_idx = idx - 1

            lesson_part = "\n".join(lines[:start_idx]).strip()
            assignment_part = "\n".join(lines[start_idx:]).strip()
            return lesson_part, assignment_part

        return (text or "").strip(), ""
    
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
    
    def _add_watermark(self, text: str, user_id: int) -> str:
        """
        Добавляет невидимый водяной знак с ID пользователя в конец текста.
        Используется для отслеживания утечек контента.
        
        Args:
            text: Исходный текст
            user_id: ID пользователя для водяного знака
            
        Returns:
            Текст с невидимым водяным знаком
        """
        # Используем невидимые символы Unicode для встраивания ID
        # Zero-width space (U+200B) и Zero-width non-joiner (U+200C) для кодирования
        watermark = ""
        user_id_str = str(user_id)
        
        # Кодируем ID пользователя в невидимые символы
        # Используем комбинацию zero-width символов для создания уникального маркера
        for digit in user_id_str:
            # Каждая цифра кодируется комбинацией zero-width символов
            zero_width_chars = [
                "\u200B",  # Zero-width space
                "\u200C",  # Zero-width non-joiner
                "\u200D",  # Zero-width joiner
                "\uFEFF",  # Zero-width no-break space
            ]
            watermark += zero_width_chars[int(digit) % len(zero_width_chars)]
        
        # Добавляем маркер начала и конца водяного знака
        watermark = "\u200B\u200C" + watermark + "\u200D\uFEFF"
        
        # Добавляем водяной знак в конец текста (невидимый для пользователя)
        return text + watermark
    
    async def _safe_send_message(self, chat_id: int, text: str, reply_markup=None, protect_content: bool = False, **kwargs):
        """
        Безопасная отправка сообщения с проверкой на пустой текст.
        Фильтрует технические ошибки Telegram API.
        Форматирует текст: сохраняет пробелы и отступы, увеличивает отступы между строками.
        
        Args:
            chat_id: ID чата (в личных чатах совпадает с user_id)
            text: Текст сообщения
            reply_markup: Клавиатура для сообщения
            protect_content: Если True, защищает сообщение от копирования и пересылки,
                           и добавляет невидимый водяной знак с ID пользователя
        """
        MAX_MESSAGE_LENGTH = 4000  # запас относительно лимита Telegram (4096)
        # В уроках/заданиях ссылки должны показываться отдельными превью-блоками,
        # поэтому у текстовых сообщений по умолчанию выключаем web-page preview.
        kwargs.setdefault("disable_web_page_preview", True)
        # Защита от копирования для контента уроков и заданий
        if protect_content:
            kwargs["protect_content"] = True
            # Добавляем невидимый водяной знак с ID пользователя для отслеживания утечек
            # В личных чатах chat_id = user_id
            text = self._add_watermark(text, chat_id)

        if not text or not text.strip():
            logger.warning(f"⚠️ Attempted to send empty message to {chat_id}, using placeholder")
            text = KEYBOARD_ONLY_PLACEHOLDER
        else:
            # Форматируем текст: сохраняем пробелы и отступы, увеличиваем отступы между строками
            text = self._format_text_for_display(text)
        
        try:
            # Plain text only: split proactively to avoid Telegram "message is too long".
            if not kwargs.get("parse_mode") and len(text) > MAX_MESSAGE_LENGTH:
                parts = self._split_long_message(text, MAX_MESSAGE_LENGTH)
                for part in parts[:-1]:
                    if part and part.strip():
                        await self.bot.send_message(chat_id, part, **kwargs)
                        await asyncio.sleep(0.2)
                last_part = parts[-1] if parts else ""
                if last_part and last_part.strip():
                    return await self.bot.send_message(chat_id, last_part, reply_markup=reply_markup, **kwargs)
                elif reply_markup:
                    return await self.bot.send_message(chat_id, KEYBOARD_ONLY_PLACEHOLDER, reply_markup=reply_markup, **kwargs)
                return None

            return await self.bot.send_message(chat_id, text, reply_markup=reply_markup, **kwargs)
        except Exception as e:
            error_msg = str(e)
            # Фильтруем технические ошибки о пустых сообщениях
            if "text must be non-empty" in error_msg or "message text is empty" in error_msg:
                logger.warning(f"⚠️ Empty message error suppressed for {chat_id}: {error_msg}")
            elif ("message is too long" in error_msg or "MESSAGE_TOO_LONG" in error_msg) and not kwargs.get("parse_mode"):
                parts = self._split_long_message(text, MAX_MESSAGE_LENGTH)
                for part in parts[:-1]:
                    if part and part.strip():
                        await self.bot.send_message(chat_id, part, **kwargs)
                        await asyncio.sleep(0.2)
                last_part = parts[-1] if parts else ""
                if last_part and last_part.strip():
                    return await self.bot.send_message(chat_id, last_part, reply_markup=reply_markup, **kwargs)
                elif reply_markup:
                    return await self.bot.send_message(chat_id, KEYBOARD_ONLY_PLACEHOLDER, reply_markup=reply_markup, **kwargs)
                return None
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
        from pathlib import Path  # Импортируем Path для использования в функции
        # Тяжёлое логирование стека сильно замедляет отправку уроков и раздувает логи.
        # Оставляем подробности только на DEBUG.
        logger.info(f"🔵 _send_lesson_from_json CALLED for day {day}, user {user.user_id}, skip_intro={skip_intro}, skip_about_me={skip_about_me}")
        if logger.isEnabledFor(logging.DEBUG):
            import traceback
            logger.debug(f"Call stack: {''.join(traceback.format_stack()[-3:-1])}")
        
        try:
            if day is None:
                day = user.current_day

            link_preview_seen: set[str] = set()
             
            title = lesson_data.get("title", f"День {day}")
            # Get lesson text - can be string (single post) or list (multiple posts)
            lesson_text_raw = lesson_data.get("text", "")
            
            # Convert to list if it's a string (backward compatible)
            if isinstance(lesson_text_raw, str):
                lesson_posts = [lesson_text_raw] if lesson_text_raw else []
            elif isinstance(lesson_text_raw, list):
                lesson_posts = lesson_text_raw
            else:
                lesson_posts = []
            
            # Получаем маркеры медиа для встроенной вставки
            # DEBUG: Log all keys in lesson_data to see what's available
            logger.info(f"   🔍 DEBUG: lesson_data keys for day {day}: {list(lesson_data.keys())}")
            if "media_markers" in lesson_data:
                media_markers_raw = lesson_data.get("media_markers")
                logger.info(f"   🔍 DEBUG: media_markers type: {type(media_markers_raw)}, value: {media_markers_raw}")
            else:
                logger.warning(f"   🔍 DEBUG: 'media_markers' key NOT FOUND in lesson_data!")
                # Try to check if it's in the raw JSON by reloading
                logger.warning(f"   🔍 DEBUG: Attempting to reload lesson_loader and check again...")
                self.lesson_loader.reload()
                lesson_data_reloaded = self.lesson_loader.get_lesson(day)
                if lesson_data_reloaded and "media_markers" in lesson_data_reloaded:
                    logger.warning(f"   🔍 DEBUG: After reload, media_markers found! Updating lesson_data...")
                    lesson_data = lesson_data_reloaded
                else:
                    logger.warning(f"   🔍 DEBUG: After reload, media_markers still NOT found in lesson_data!")
            
            media_markers = lesson_data.get("media_markers", {})
            logger.info(f"   📎 Media markers in lesson_data for day {day}: {len(media_markers) if media_markers else 0} markers")
            if media_markers:
                logger.info(f"   📎 Media markers keys: {list(media_markers.keys())}")
                for marker_id, marker_info in media_markers.items():
                    logger.info(f"   📎   - {marker_id}: type={marker_info.get('type')}, path={marker_info.get('path')}, name={marker_info.get('name')}")
            else:
                logger.warning(f"   ⚠️ No media_markers found in lesson_data for day {day}!")
                logger.warning(f"   ⚠️ Available keys in lesson_data: {list(lesson_data.keys())}")

            # Some sources put the assignment inside the main text. Extract it so it becomes a separate block.
            # Also remove square bracket markers like [POST], [ДОПОЛНЕНИЕ] - they are only for block separation
            extracted_task_from_posts = ""
            if lesson_posts:
                normalized_posts: list[str] = []
                original_count = len(lesson_posts)
                for i, post in enumerate(lesson_posts):
                    if not isinstance(post, str) or not post.strip():
                        logger.debug(f"   ⏭️ Skipping empty post {i} for day {day}")
                        continue
                    # Remove block-separator markers, but keep media markers for inline insertion.
                    block_marker_re = r'^\s*\[(?:POST\d*|POST|ДОПОЛНЕНИЕ|BLOCK|БЛОК)\]\s*$'
                    cleaned_post = re.sub(block_marker_re, '', post, flags=re.MULTILINE | re.IGNORECASE)
                    cleaned_post = re.sub(r'^\s*(?:---POST---|---)\s*$', '', cleaned_post, flags=re.MULTILINE | re.IGNORECASE)
                    # Keep original spacing between paragraphs; don't collapse empty lines.
                    if not cleaned_post.strip():
                        logger.warning(f"   ⚠️ Post {i} for day {day} became empty after marker removal (original length: {len(post)} chars)")
                        continue
                    if not extracted_task_from_posts:
                        lesson_part, task_part = self._split_assignment_from_text(cleaned_post)
                        if task_part:
                            extracted_task_from_posts = task_part
                            if lesson_part:
                                normalized_posts.append(lesson_part)
                                logger.debug(f"   ✅ Split post {i} for day {day} into lesson part ({len(lesson_part)} chars) and task part ({len(task_part)} chars)")
                            else:
                                logger.debug(f"   ℹ️ Post {i} for day {day} was entirely task, extracted as task")
                            continue
                    normalized_posts.append(cleaned_post)
                    logger.debug(f"   ✅ Added post {i} for day {day} to normalized_posts ({len(cleaned_post)} chars)")
                lesson_posts = normalized_posts
                if len(lesson_posts) != original_count:
                    logger.info(f"   📊 Post normalization for day {day}: {original_count} -> {len(lesson_posts)} posts (removed {original_count - len(lesson_posts)} empty posts)")

            # For backward compatibility, keep 'text' as first post for existing code
            text = lesson_posts[0] if lesson_posts else ""

            # Получаем задание в зависимости от тарифа (fallback to extracted task when needed)
            task = self.lesson_loader.get_task_for_tariff(day, user.tariff)
            if not task and extracted_task_from_posts:
                task = extracted_task_from_posts
            
            # Формируем сообщение урока
            # Проверяем, есть ли вводный текст (intro_text) - для урока 22
            intro_text = lesson_data.get("intro_text", "")
            about_me_text = lesson_data.get("about_me_text", "")

            intro_text_raw = intro_text
            about_me_text_raw = about_me_text
            lesson_posts_raw = list(lesson_posts)

            # If extra blocks are already present inside the main lesson text, don't send them separately.
            # This avoids "double text" when content sources accidentally duplicate intro/about sections.
            lesson_full_text = "\n\n".join([p for p in lesson_posts_raw if isinstance(p, str) and p.strip()])
            if about_me_text and about_me_text.strip():
                about_me_stripped = about_me_text.strip()
                if about_me_stripped in lesson_full_text:
                    logger.info(f"   ⏭️ Skipping about_me_text for day {day}: already present in main text")
                    about_me_text = ""
            if intro_text and intro_text.strip():
                intro_stripped = intro_text.strip()
                if intro_stripped in lesson_full_text:
                    logger.info(f"   ⏭️ Skipping intro_text for day {day}: already present in main text")
                    intro_text = ""

            # Собираем combined_text_raw для отправки медиа из URL
            # URL будут удалены из текста после отправки медиа
            combined_text_raw = "\n\n".join(
                [
                    (intro_text_raw or ""),
                    (about_me_text_raw or ""),
                    "\n\n".join([p for p in lesson_posts_raw if isinstance(p, str) and p.strip()]),
                ]
            )
            
            # Проверяем, есть ли фото для начала урока (для урока 30)
            intro_photo_file_id = lesson_data.get("intro_photo_file_id", "")
            intro_photo_path = lesson_data.get("intro_photo_path", "")
            
            # Отправляем фото в начале урока, если есть (для урока 30)
            if intro_photo_file_id or intro_photo_path:
                try:
                    # Анимация перед отправкой фото
                    await send_typing_action(self.bot, user.user_id, 0.4)
                    # Убираем разделители - caption должен браться из данных или быть None
                    caption = None
                    
                    if intro_photo_file_id:
                        await self.bot.send_photo(user.user_id, intro_photo_file_id, caption=caption, protect_content=True)
                        logger.info(f"   ✅ Sent intro photo (file_id) for lesson {day}")
                    elif intro_photo_path:
                        from pathlib import Path
                        from aiogram.types import FSInputFile
                        photo_path = Path(intro_photo_path)
                        # Обрабатываем изображение для мобильных устройств
                        resized_path = await self._resize_image_for_mobile(photo_path)
                        image_path_to_use = resized_path if resized_path else photo_path
                        photo_file = FSInputFile(image_path_to_use)
                        await self.bot.send_photo(user.user_id, photo_file, caption=caption, protect_content=True)
                        logger.info(f"   ✅ Sent intro photo (file path) for lesson {day}")
                    await asyncio.sleep(0.6)  # Пауза для плавности
                except Exception as photo_error:
                    logger.warning(f"   ⚠️ Не удалось отправить intro photo для урока {day}: {photo_error}")
            
            # Получаем список медиа для урока
            media_list = lesson_data.get("media", [])

            # De-duplicate media entries coming from content sources (can contain repeats).
            # Keep stable order; prefer file_id identity when available, otherwise path.
            if media_list:
                seen_media_keys: set[tuple[str, str, str]] = set()
                unique_media: list[dict] = []
                for m in media_list:
                    if not isinstance(m, dict):
                        continue
                    m_type = str(m.get("type") or "")
                    m_fid = str(m.get("file_id") or "")
                    m_path = str(m.get("path") or "")
                    key = (m_type, m_fid, m_path)
                    if key in seen_media_keys:
                        continue
                    seen_media_keys.add(key)
                    unique_media.append(m)
                if len(unique_media) != len(media_list):
                    logger.info(f"   🧹 De-duplicated media_list for day {day}: {len(media_list)} -> {len(unique_media)}")
                media_list = unique_media

            # Если медиа встроено в текст через маркеры [MEDIA_...], НЕ отправляем его отдельно из media_list
            # Все медиа должно отправляться строго в тех местах, где указана ссылка в тексте
            try:
                inline_marker_file_ids: set[str] = set()
                inline_marker_paths: set[str] = set()
                has_inline_markers = False
                
                if media_markers:
                    haystacks = []
                    if intro_text:
                        haystacks.append(intro_text)
                    if about_me_text:
                        haystacks.append(about_me_text)
                    if task:
                        haystacks.append(task)
                    haystacks.extend([p for p in lesson_posts if isinstance(p, str) and p])

                    for marker_id, marker_info in media_markers.items():
                        token = f"[{marker_id}]"
                        if any(token in h for h in haystacks):
                            has_inline_markers = True
                            # Отслеживаем как по file_id, так и по path для надежности
                            fid = marker_info.get("file_id")
                            if fid:
                                inline_marker_file_ids.add(str(fid))
                            path = marker_info.get("path")
                            if path:
                                # Нормализуем путь для сравнения
                                normalized_path = str(Path(path)).replace('\\', '/')
                                inline_marker_paths.add(normalized_path)
                                # Также добавляем имя файла для дополнительной проверки
                                file_name = Path(path).name
                                if file_name:
                                    inline_marker_paths.add(file_name)

                # Если есть inline маркеры в тексте, полностью очищаем media_list
                # Все медиа должно отправляться только inline в тексте, где указаны ссылки
                if has_inline_markers and media_list:
                    before = len(media_list)
                    filtered_media = []
                    for m in media_list:
                        m_fid = str(m.get("file_id") or "")
                        m_path = str(m.get("path") or "")
                        m_name = Path(m_path).name if m_path else ""
                        
                        # Проверяем по file_id
                        if m_fid and m_fid in inline_marker_file_ids:
                            logger.debug(f"   🧹 Skipping media (file_id match with inline marker): {m_path or m_name}")
                            continue
                        
                        # Проверяем по path
                        if m_path:
                            normalized_m_path = str(Path(m_path)).replace('\\', '/')
                            if normalized_m_path in inline_marker_paths:
                                logger.debug(f"   🧹 Skipping media (path match with inline marker): {m_path}")
                                continue
                        
                        # Проверяем по имени файла
                        if m_name and m_name in inline_marker_paths:
                            logger.debug(f"   🧹 Skipping media (filename match with inline marker): {m_name}")
                            continue
                        
                        # Если медиа не найдено в inline маркерах, оставляем его для отдельной отправки
                        # (на случай, если оно не встроено в текст)
                        filtered_media.append(m)
                    
                    media_list = filtered_media
                    removed = before - len(media_list)
                    if removed:
                        logger.info(f"   🧹 Removed {removed} media items already referenced via inline markers for day {day}")
                    if has_inline_markers and len(media_list) == 0:
                        logger.info(f"   ✅ All media for day {day} is embedded inline in text blocks, no separate media_list items")
            except Exception as e:
                logger.debug(f"Could not filter media_list by inline markers for day {day}: {e}")
            
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
            
            # Формируем заголовок урока - только текст из Google Doc, без эмодзи и разделителей
            lesson_message = f"<b>{title}</b>\n\n"
            
            # Отправляем заголовок урока (защищен от копирования с водяным знаком)
            await self._safe_send_message(user.user_id, lesson_message, protect_content=True, parse_mode="HTML")
            await asyncio.sleep(0.5)  # Пауза для плавности
            
            # Для дня 0 отправляем предупреждение о защите контента
            if day == 0 or str(day) == "0":
                protection_warning = (
                    "🔒 <b>Защита контента</b>\n\n"
                    "Весь контент курса защищен от копирования, пересылки и скриншотов.\n\n"
                    "⚠️ <b>Запрещено:</b>\n"
                    "• Делать скриншоты уроков и заданий\n"
                    "• Пересылать материалы курса\n"
                    "• Копировать текст уроков\n"
                    "• Распространять контент в интернете\n\n"
                    "Нарушение этих правил может привести к блокировке доступа к курсу."
                )
                await self.bot.send_message(
                    user.user_id, 
                    protection_warning, 
                    protect_content=False  # Это предупреждение не защищаем, чтобы его можно было прочитать
                )
                await asyncio.sleep(0.5)  # Пауза для плавности
            
            # Для урока 0: отправляем видео с intro_text в caption сразу после заголовка
            lesson0_intro_sent_with_video = False
            if lesson0_video_with_intro and intro_text and not skip_intro:
                try:
                    await send_typing_action(self.bot, user.user_id, 0.4)
                    video_file_id = lesson0_video_with_intro.get("file_id")
                    video_file_path = lesson0_video_with_intro.get("path")
                    
                    # Подпись с intro_text + разделитель для визуального расширения блока медиа
                    original_caption = intro_text if intro_text else None
                    caption = self._add_media_separator(original_caption)
                    
                    if video_file_id:
                        await self.bot.send_video(
                            user.user_id, 
                            video_file_id, 
                            caption=caption, 
                            width=MOBILE_SCREEN_WIDTH,
                            protect_content=True
                        )
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
                            # Используем тот же caption с разделителем
                            await self.bot.send_video(
                                user.user_id, 
                                video_file, 
                                caption=caption, 
                                width=MOBILE_SCREEN_WIDTH,
                                protect_content=True
                            )
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
            # Если медиа встроено в текст через маркеры [MEDIA_...], НЕ отправляем его отдельно
            # Медиа должно отправляться строго в тех местах, где указана ссылка в тексте
            has_inline_media_markers = False
            if media_markers:
                # Проверяем, есть ли маркеры в тексте
                haystacks = []
                if intro_text:
                    haystacks.append(intro_text)
                if about_me_text:
                    haystacks.append(about_me_text)
                if task:
                    haystacks.append(task)
                haystacks.extend([p for p in lesson_posts if isinstance(p, str) and p])
                
                for marker_id in media_markers.keys():
                    token = f"[{marker_id}]"
                    if any(token in h for h in haystacks):
                        has_inline_media_markers = True
                        break
            
            # Сохраняем флаг для использования в других местах функции
            self._has_inline_media_markers = has_inline_media_markers
            
            # Если медиа встроено в текст через маркеры, НЕ отправляем его отдельно из media_list
            # Медиа будет отправлено inline в тексте, где указаны ссылки
            if has_inline_media_markers:
                logger.info(f"   ✅ Media for day {day} is embedded inline in text blocks, skipping separate media_list sending")
                # Очищаем media_list, так как все медиа должно отправляться только inline в тексте
                # Медиа, которое не встроено в текст, уже отфильтровано выше
                original_media_count = len(media_list)
                media_list = []
                media_count = 0
                media_index = 0
                logger.info(f"   🧹 Cleared {original_media_count} media items from media_list (all embedded inline in text)")
            elif media_count == 1 and not lesson0_video_with_intro and day != 1:
                # Одно медиа - сразу после заголовка (кроме дня 1, где видео отправляется перед заданием)
                # Только если медиа НЕ встроено в текст через маркеры
                await self._send_media_item(user.user_id, media_list[0], day)
                logger.info(f"   ✅ Sent single media item after title for lesson {day}")
                media_index = 1  # Помечаем, что медиа отправлено
            elif media_count > 1 and not lesson0_video_with_intro and day != 1:
                # Несколько медиа - распределяем по структуре урока (кроме дня 1)
                # Первое медиа - сразу после заголовка
                # Только если медиа НЕ встроено в текст через маркеры
                await self._send_media_item(user.user_id, media_list[media_index], day)
                logger.info(f"   ✅ Sent media {media_index + 1}/{media_count} after title for lesson {day}")
                media_index += 1
            elif day == 1 and media_count > 0 and not has_inline_media_markers:
                # Для дня 1: медиа не отправляем сразу после заголовка, оно будет отправлено перед заданием
                # Только если медиа НЕ встроено в текст через маркеры
                logger.info(f"   ⏭️ Skipping media after title for lesson 1 (will be sent before assignment)")
            
            # Отправляем вводный текст отдельным сообщением, если есть (пропускаем для навигатора)
            # Для урока 0 intro_text уже отправлен с видео, поэтому пропускаем
            # ВАЖНО: Проверяем, не содержится ли intro_text уже в основном тексте (lesson_posts)
            # Если содержится, удаляем его из текста, чтобы не дублировать
            # Также удаляем intro_text из всех постов, если он там есть
            intro_text_sent_separately = False
            intro_text_in_main_text = False
            
            if intro_text and lesson_posts:
                # Проверяем, содержится ли intro_text в любом из постов
                intro_text_short = intro_text[:100] if len(intro_text) > 100 else intro_text
                intro_text_stripped = intro_text.strip()
                
                # Для урока 1: также проверяем и удаляем текст, который будет отправлен с видео
                if (day == 1 or str(day) == "1") and lesson1_video_media:
                    # Удаляем посты, содержащие текст, который будет отправлен с видео
                    intro_keywords = [
                        "Добро пожаловать на корвет",
                        "Привет вам, отважные исследователи",
                        "Наш корабль берёт курс",
                        "я задам вам первый вопрос"
                    ]
                    
                    for i, post in enumerate(lesson_posts):
                        if not isinstance(post, str) or not post.strip():
                            continue
                        
                        # Проверяем, содержит ли пост ключевые слова из intro текста
                        # ВАЖНО: Удаляем пост ТОЛЬКО если он полностью состоит из intro текста
                        if any(keyword in post for keyword in intro_keywords):
                            # Проверяем, что пост не содержит значительного дополнительного контента
                            post_stripped = post.strip()
                            intro_text_stripped = intro_text.strip() if intro_text else ""
                            
                            # Если пост содержит intro_text и дополнительный контент (более 100 символов), не удаляем
                            if intro_text_stripped and intro_text_stripped in post_stripped:
                                # Проверяем длину дополнительного контента
                                additional_content = post_stripped.replace(intro_text_stripped, "").strip()
                                if len(additional_content) > 100:
                                    logger.info(f"   ℹ️ Post {i} for day {day} contains intro text but also {len(additional_content)} chars of additional content, keeping it")
                                    continue
                            
                            intro_text_in_main_text = True
                            logger.warning(f"   ⚠️ Intro text found in post {i} for day {day}, will remove to prevent duplication")
                            lesson_posts[i] = ""  # Помечаем для удаления
                            logger.info(f"   ✅ Marked post {i} for removal (contains intro text for day {day})")
                
                # Проверяем все посты на наличие intro_text
                for i, post in enumerate(lesson_posts):
                    if not isinstance(post, str) or not post.strip():
                        continue
                    
                    # Проверяем, содержится ли intro_text в этом посте
                    if intro_text_short in post or (len(intro_text) < 200 and intro_text_stripped in post):
                        intro_text_in_main_text = True
                        logger.warning(f"   ⚠️ intro_text found in post {i} for day {day}, will remove from text to prevent duplication")
                        
                        # Удаляем intro_text из поста
                        if intro_text_stripped in post:
                            # Удаляем intro_text из начала поста, если он там есть
                            if post.startswith(intro_text_stripped):
                                post_cleaned = post[len(intro_text_stripped):].strip()
                            else:
                                # Пробуем найти и удалить intro_text из любого места
                                post_cleaned = post.replace(intro_text_stripped, "", 1).strip()
                            
                            # Удаляем лишние переносы строк в начале
                            post_cleaned = re.sub(r'^\n+', '', post_cleaned)
                            post_cleaned = re.sub(r'^\s*\n\s*\n', '\n\n', post_cleaned)
                            
                            if post_cleaned:
                                lesson_posts[i] = post_cleaned
                                logger.info(f"   ✅ Removed intro_text from post {i} for day {day}")
                            else:
                                # Если после удаления пост стал пустым, помечаем его для удаления
                                lesson_posts[i] = ""
                                logger.info(f"   ✅ Post {i} became empty after removing intro_text for day {day}")
                
                # Удаляем пустые посты, но логируем это для диагностики
                before_count = len(lesson_posts)
                lesson_posts = [p for p in lesson_posts if p and p.strip()]
                after_count = len(lesson_posts)
                if before_count != after_count:
                    logger.warning(f"   ⚠️ Removed {before_count - after_count} empty posts after intro_text removal for day {day} (kept {after_count} posts)")
                
                # Обновляем text
                if lesson_posts:
                    text = lesson_posts[0]
                else:
                    text = ""
            
            # Отправляем intro_text отдельно только если он НЕ содержится в основном тексте
            # Для урока 1: пропускаем intro_text, так как он будет отправлен с видео
            if intro_text and not skip_intro and not lesson0_intro_sent_with_video and not intro_text_in_main_text and not ((day == 1 or str(day) == "1") and lesson1_video_media):
                # Анимация перед отправкой текста
                await send_typing_action(self.bot, user.user_id, 0.5)
                # Текст берется как есть из Google Doc, без разделителей
                intro_message = intro_text
                await self._safe_send_message(user.user_id, intro_message, protect_content=True)
                intro_text_sent_separately = True
                logger.info(f"   Sent intro_text for lesson {day}")
                await asyncio.sleep(0.5)  # Пауза для плавности
                
                # Второе медиа - после intro_text (если есть несколько медиа)
                # Только если медиа НЕ встроено в текст через маркеры
                if media_count > 1 and media_index < media_count and not has_inline_media_markers:
                    await self._send_media_item(user.user_id, media_list[media_index], day)
                    logger.info(f"   ✅ Sent media {media_index + 1}/{media_count} after intro_text for lesson {day}")
                    media_index += 1
            elif intro_text and skip_intro:
                logger.info(f"   Skipped intro_text for lesson {day} (navigator mode)")
            elif intro_text and lesson0_intro_sent_with_video:
                logger.info(f"   Skipped intro_text for lesson {day} (already sent with video)")
            
            # Отправляем "ОБО МНЕ" отдельным сообщением с фото (для урока 1) - сразу после intro_text (пропускаем для навигатора)
            # ВАЖНО: Если картинка "ОБО МНЕ" встроена через media_markers в текст, не отправляем её отдельно
            about_me_photo_file_id = lesson_data.get("about_me_photo_file_id", "")
            about_me_photo_path = lesson_data.get("about_me_photo_path", "")

            # Avoid sending the same "about me" photo twice (once as generic media, once with the about_me block).
            if about_me_photo_file_id and media_list:
                before = len(media_list)
                media_list = [m for m in media_list if str(m.get("file_id") or "") != str(about_me_photo_file_id)]
                removed = before - len(media_list)
                if removed:
                    logger.info(f"   🧹 Removed {removed} media items matching about_me_photo_file_id for day {day}")
            if about_me_photo_path and media_list:
                before = len(media_list)
                media_list = [m for m in media_list if str(m.get("path") or "") != str(about_me_photo_path)]
                removed = before - len(media_list)
                if removed:
                    logger.info(f"   🧹 Removed {removed} media items matching about_me_photo_path for day {day}")
            
            # Проверяем, есть ли маркер для картинки "ОБО МНЕ" в тексте
            about_me_photo_in_text = False
            if about_me_photo_file_id and media_markers:
                # Ищем маркер, который соответствует этой картинке
                for marker_id, marker_info in media_markers.items():
                    if marker_info.get("file_id") == about_me_photo_file_id or marker_info.get("path") == about_me_photo_path:
                        # Проверяем, есть ли этот маркер в тексте
                        if any(f"[{marker_id}]" in post for post in lesson_posts) or f"[{marker_id}]" in text:
                            about_me_photo_in_text = True
                            logger.info(f"   ✅ 'ОБО МНЕ' photo found in text via marker {marker_id}, will be embedded inline")
                            break
            
            logger.info(f"   Checking 'ОБО МНЕ' for lesson {day}: text={bool(about_me_text)}, file_id={bool(about_me_photo_file_id)}, path={bool(about_me_photo_path)}, skip={skip_about_me}, in_text={about_me_photo_in_text}")
            
            # Отправляем "ОБО МНЕ" отдельно только если картинка НЕ встроена в текст через маркеры
            if about_me_text and not skip_about_me and not about_me_photo_in_text:
                await asyncio.sleep(0.5)  # Небольшая пауза
                
                # Флаг для отслеживания успешной отправки
                about_me_sent = False
                
                # Анимация перед отправкой фото
                await send_typing_action(self.bot, user.user_id, 0.4)
                
                # Пробуем отправить фото, если есть file_id (приоритет)
                if about_me_photo_file_id:
                    try:
                        # Подпись - текст из Google Doc + разделитель для визуального расширения блока медиа
                        original_caption = about_me_text if about_me_text else None
                        caption = self._add_media_separator(original_caption)
                        await self.bot.send_photo(
                            user.user_id,
                            about_me_photo_file_id,
                            caption=caption,
                            protect_content=True
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
                                photo_path = Path(about_me_photo_path)
                                # Обрабатываем изображение для мобильных устройств
                                resized_path = await self._resize_image_for_mobile(photo_path)
                                image_path_to_use = resized_path if resized_path else photo_path
                                photo_file = FSInputFile(image_path_to_use)
                                # Подпись - текст из Google Doc + разделитель для визуального расширения блока медиа
                                original_caption = about_me_text if about_me_text else None
                                caption = self._add_media_separator(original_caption)
                                await self.bot.send_photo(
                                    user.user_id,
                                    photo_file,
                                    caption=caption,
                                    protect_content=True
                                )
                                logger.info(f"   ✅ Sent 'ОБО МНЕ' photo (file path) for lesson {day}")
                                about_me_sent = True
                            except Exception as path_error:
                                logger.warning(f"   ⚠️ Не удалось отправить фото 'ОБО МНЕ' по пути для урока {day}: {path_error}")
                                # Отправляем только текст как fallback
                                await self._safe_send_message(user.user_id, about_me_text, protect_content=True)
                                logger.info(f"   ✅ Sent 'ОБО МНЕ' text only (fallback) for lesson {day}")
                                about_me_sent = True
                        else:
                            # Отправляем только текст как fallback
                            await self._safe_send_message(user.user_id, about_me_text, protect_content=True)
                            logger.info(f"   ✅ Sent 'ОБО МНЕ' text only (fallback) for lesson {day}")
                            about_me_sent = True
                # Если нет file_id, но есть путь к файлу
                elif about_me_photo_path and not about_me_sent:
                    try:
                        # Анимация перед отправкой фото
                        await send_typing_action(self.bot, user.user_id, 0.4)
                        from pathlib import Path
                        from aiogram.types import FSInputFile
                        photo_path = Path(about_me_photo_path)
                        # Обрабатываем изображение для мобильных устройств
                        resized_path = await self._resize_image_for_mobile(photo_path)
                        image_path_to_use = resized_path if resized_path else photo_path
                        photo_file = FSInputFile(image_path_to_use)
                        # Подпись - текст из Google Doc + разделитель для визуального расширения блока медиа
                        original_caption = about_me_text if about_me_text else None
                        caption = self._add_media_separator(original_caption)
                        await self.bot.send_photo(
                            user.user_id,
                            photo_file,
                            caption=caption,
                            protect_content=True
                        )
                        logger.info(f"   ✅ Sent 'ОБО МНЕ' photo (file path) for lesson {day}")
                        about_me_sent = True
                    except Exception as path_error:
                        logger.warning(f"   ⚠️ Не удалось отправить фото 'ОБО МНЕ' по пути для урока {day}: {path_error}")
                        # Отправляем только текст как fallback
                        await self._safe_send_message(user.user_id, about_me_text, protect_content=True)
                        logger.info(f"   ✅ Sent 'ОБО МНЕ' text only (fallback) for lesson {day}")
                        about_me_sent = True
                # Если нет фото вообще, отправляем только текст
                elif not about_me_sent:
                    await self._safe_send_message(user.user_id, about_me_text, protect_content=True)
                    logger.info(f"   ✅ Sent 'ОБО МНЕ' text only for lesson {day}")
                    about_me_sent = True
            else:
                logger.warning(f"   ⚠️ No 'ОБО МНЕ' text found for lesson {day}")
            
            # Определяем, сколько медиа осталось для распределения по основному тексту
            remaining_media = media_count - media_index if media_count > media_index else 0
            
            # Если есть медиа для распределения по тексту, разбиваем текст на части
            # Если урок разбит на несколько постов, работаем с первым постом для распределения медиа
            # Остальные посты отправятся после медиа
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
                                await self._safe_send_message(user.user_id, paragraphs[i], protect_content=True)
                                await asyncio.sleep(0.2)
                        
                        # Отправляем картинку перед целевым абзацем
                        # Только если медиа НЕ встроено в текст через маркеры
                        if not has_inline_media_markers:
                            await self._send_media_item(user.user_id, media_list[media_index], day)
                            logger.info(f"   ✅ Sent lesson 2 photo before target paragraph for lesson {day}")
                            media_index += 1
                            lesson2_photo_placed = True
                            await asyncio.sleep(0.3)
                        else:
                            logger.info(f"   ⏭️ Skipped lesson 2 photo (embedded inline in text) for lesson {day}")
                            lesson2_photo_placed = True
                        
                        # Отправляем целевой абзац после картинки
                        if paragraphs[target_paragraph_index]:
                            await self._safe_send_message(user.user_id, paragraphs[target_paragraph_index], protect_content=True)
                            await asyncio.sleep(0.2)
                        
                        # Отправляем оставшиеся абзацы после целевого
                        for i in range(target_paragraph_index + 1, len(paragraphs)):
                            if paragraphs[i]:
                                await self._safe_send_message(user.user_id, paragraphs[i], protect_content=True)
                                await asyncio.sleep(0.2)
                
                # Для урока 1: удаляем текст "Добро пожаловать на корвет" из основного текста, 
                # так как он будет отправлен с видео перед заданием
                # ВАЖНО: Сохраняем маркеры медиа в тексте, даже если удаляем intro текст
                if (day == 1 or str(day) == "1") and lesson1_video_media:
                    # Удаляем абзац с текстом "Добро пожаловать на корвет" из текста
                    # Но сохраняем маркеры медиа, если они есть
                    paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
                    text_paragraphs = []
                    
                    for i, paragraph in enumerate(paragraphs):
                        # Проверяем, содержит ли абзац текст, который будет отправлен с видео
                        # НО: если в абзаце есть маркеры медиа, сохраняем его (маркеры важнее)
                        has_media_marker = media_markers and any(f"[{marker}]" in paragraph for marker in media_markers.keys())
                        
                        if not has_media_marker and ("Добро пожаловать на корвет" in paragraph or 
                            "Привет вам, отважные исследователи" in paragraph or
                            "Наш корабль берёт курс" in paragraph):
                            # Текст будет отправлен с видео перед заданием, пропускаем его здесь
                            lesson1_video_placed = True
                            logger.info(f"   ✅ Removed intro text from main text for lesson 1 (paragraph {i}, no media markers)")
                        else:
                            if has_media_marker:
                                logger.info(f"   ✅ Kept paragraph {i} for lesson 1 (contains media markers)")
                            text_paragraphs.append(paragraph)
                    
                    # Обновляем текст без абзаца "Добро пожаловать"
                    if text_paragraphs:
                        text = '\n\n'.join(text_paragraphs)
                        logger.info(f"   ✅ Updated text for lesson 1, kept {len(text_paragraphs)} paragraphs")
                    else:
                        # Если весь текст был удален, делаем его пустым
                        text = ""
                        logger.info(f"   ✅ All intro text removed from main text for lesson 1")
                
                # Если медиа урока 1 или 2 уже размещено, отправляем текст с медиа-маркерами (если есть)
                if lesson1_video_placed or lesson2_photo_placed:
                    # Для урока 1 или 2: отправляем текст с медиа-маркерами, если они есть
                    # ВАЖНО: Проверяем, что текст не пустой и не содержит только пробелы
                    if text and text.strip() and text.strip() != "":
                        # Удаляем маркеры медиа из текста перед проверкой, так как они не должны быть видны пользователю
                        text_for_check = text
                        if media_markers:
                            for marker_id in media_markers.keys():
                                text_for_check = text_for_check.replace(f"[{marker_id}]", "")
                        
                        # Если после удаления маркеров текст не пустой, отправляем его
                        if text_for_check.strip():
                            # Проверяем, есть ли маркеры медиа в тексте для встроенной вставки
                            sent_media_from_markers = set()
                            if media_markers and any(f"[{marker}]" in text for marker in media_markers.keys()):
                                # Используем встроенную вставку медиа по маркерам
                                sent_media_from_markers = await self._send_text_with_inline_media(user.user_id, text, media_markers, day)
                                logger.info(f"   ✅ Sent lesson text with inline media markers for day {day} (after video/photo placement)")
                            else:
                                # Отправляем текст без медиа-маркеров
                                await self._safe_send_message(user.user_id, text, protect_content=True)
                                logger.info(f"   ✅ Sent lesson text for day {day} (after video/photo placement)")
                        else:
                            logger.info(f"   ⏭️ Skipped sending text for day {day} (empty after marker removal)")
                    else:
                        logger.info(f"   ⏭️ Skipped sending text for day {day} (text is empty)")
                    
                    # Отправляем оставшиеся медиа из списка, которые не были отправлены через маркеры
                    # Используем данные из _send_text_with_inline_media для точной фильтрации
                    sent_media_file_ids = set()
                    sent_media_paths = set()
                    sent_media_filenames = set()
                    for (fid, path, filename) in sent_media_from_markers:
                        if fid:
                            sent_media_file_ids.add(fid)
                        if path:
                            sent_media_paths.add(path)
                        if filename:
                            sent_media_filenames.add(filename)
                    
                    # Также добавляем медиа из media_markers для обратной совместимости
                    if media_markers:
                        for marker_id, marker_info in media_markers.items():
                            if f"[{marker_id}]" in text:
                                fid = marker_info.get("file_id")
                                if fid:
                                    sent_media_file_ids.add(str(fid))
                                path = marker_info.get("path")
                                if path:
                                    normalized_path = str(Path(path)).replace('\\', '/')
                                    sent_media_paths.add(normalized_path)
                                    sent_media_filenames.add(Path(path).name)
                    
                    while media_index < media_count:
                        media_item = media_list[media_index]
                        media_file_id = str(media_item.get("file_id") or "")
                        media_path = str(media_item.get("path") or "")
                        media_name = Path(media_path).name if media_path else ""
                        normalized_media_path = str(Path(media_path)).replace('\\', '/') if media_path else ""
                        
                        # Проверяем, не было ли это медиа уже отправлено через маркер (по file_id, path или имени)
                        already_sent = (
                            (media_file_id and media_file_id in sent_media_file_ids) or
                            (normalized_media_path and normalized_media_path in sent_media_paths) or
                            (media_name and media_name in sent_media_filenames)
                        )
                        
                        if not already_sent:
                            await self._send_media_item(user.user_id, media_item, day)
                            logger.info(f"   ✅ Sent remaining media {media_index + 1}/{media_count} after text for lesson {day}")
                        else:
                            logger.info(f"   ⏭️ Skipped media {media_index + 1}/{media_count} (already sent via marker) for lesson {day}")
                        media_index += 1
                        await asyncio.sleep(0.3)
                else:
                    # ВАЖНО: Если урок разбит на несколько постов (через маркеры [POST]), 
                    # каждый пост после [POST] отправляется как отдельный блок
                    # Медиа-маркеры вставляются в тот блок (пост), где они находятся в тексте
                    if len(lesson_posts) > 1:
                        # Собираем file_id и пути медиа, которые будут отправлены через маркеры в постах
                        sent_media_file_ids = set()
                        sent_media_paths = set()
                        sent_media_filenames = set()
                        all_sent_media_from_posts = set()
                        
                        # Обрабатываем все посты в цикле, включая первый
                        # Каждый пост отправляется как отдельный блок (после [POST])
                        for i, post_text in enumerate(lesson_posts):
                            if post_text and post_text.strip():
                                # Проверяем, есть ли медиа-маркеры в этом посте
                                # Если есть, медиа будет встроено в этот блок
                                if media_markers and any(f"[{marker}]" in post_text for marker in media_markers.keys()):
                                    # Используем встроенную вставку медиа по маркерам
                                    # Медиа вставляется в этот блок (пост) в местах, где находятся маркеры
                                    sent_media_from_post = await self._send_text_with_inline_media(user.user_id, post_text.strip(), media_markers, day)
                                    all_sent_media_from_posts.update(sent_media_from_post)
                                    logger.info(f"   ✅ Sent lesson post {i + 1}/{len(lesson_posts)} with inline media markers for day {day} (separate block after [POST])")
                                else:
                                    # Специальная обработка для урока 28: добавляем кнопку после определенного текста
                                    if day == 28 or str(day) == "28":
                                        target_text = "Впрочем, зачем обязательно расставаться? Если вам интересно и полезно — давайте продолжать!"
                                        if target_text in post_text:
                                            # Находим позицию текста и добавляем кнопку после него
                                            text_pos = post_text.find(target_text)
                                            if text_pos != -1:
                                                # Текст до целевого предложения
                                                before_text = post_text[:text_pos + len(target_text)].strip()
                                                # Текст после целевого предложения (если есть)
                                                after_text = post_text[text_pos + len(target_text):].strip()
                                                
                                                # Отправляем текст с кнопкой
                                                button_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                                                    [InlineKeyboardButton(
                                                        text="ИДУ НА ВТОРУЮ СТУПЕНЬ КУРСА",
                                                        url="https://t.me/StartNowQ_bot?start=second_level"
                                                    )]
                                                ])
                                                full_text = before_text
                                                if after_text:
                                                    full_text += "\n\n" + after_text
                                                await self.bot.send_message(
                                                    user.user_id,
                                                    full_text,
                                                    reply_markup=button_keyboard,
                                                    disable_web_page_preview=True
                                                )
                                                logger.info(f"   ✅ Sent lesson 28 with button after continuation text")
                                            else:
                                                await self._safe_send_message(user.user_id, post_text.strip())
                                        else:
                                            await self._safe_send_message(user.user_id, post_text.strip())
                                    # Специальная обработка для урока 30: добавляем кнопки после ссылки на main-hero
                                    elif day == 30 or str(day) == "30":
                                        main_hero_url = "https://sites.google.com/view/nikitinartem/education/main-hero"
                                        if main_hero_url in post_text:
                                            # Находим позицию ссылки
                                            url_pos = post_text.find(main_hero_url)
                                            if url_pos != -1:
                                                # Текст до ссылки (включая "Подробнее об этом: ")
                                                before_url = post_text[:url_pos + len(main_hero_url)].strip()
                                                # Текст после ссылки (если есть)
                                                after_url = post_text[url_pos + len(main_hero_url):].strip()
                                                
                                                # Отправляем текст с кнопками
                                                button_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                                                    [InlineKeyboardButton(
                                                        text="Вторая ступень курса",
                                                        url="https://t.me/StartNowQ_bot?start=second_level"
                                                    )],
                                                    [InlineKeyboardButton(
                                                        text="Главный герой",
                                                        url="https://t.me/StartNowQ_bot?start=offline"
                                                    )]
                                                ])
                                                full_text = before_url
                                                if after_url:
                                                    full_text += "\n\n" + after_url
                                                await self.bot.send_message(
                                                    user.user_id,
                                                    full_text,
                                                    reply_markup=button_keyboard,
                                                    disable_web_page_preview=True,
                                                    protect_content=True
                                                )
                                                logger.info(f"   ✅ Sent lesson 30 with buttons after main-hero link")
                                            else:
                                                await self._safe_send_message(user.user_id, post_text.strip(), protect_content=True)
                                        else:
                                            await self._safe_send_message(user.user_id, post_text.strip(), protect_content=True)
                                    else:
                                        # Отправляем пост без медиа-маркеров как отдельный блок
                                        await self._safe_send_message(user.user_id, post_text.strip(), protect_content=True)
                                    logger.info(f"   ✅ Sent lesson post {i + 1}/{len(lesson_posts)} for day {day} (separate block after [POST])")
                                
                                # Пауза между блоками (постами)
                                if i < len(lesson_posts) - 1:
                                    await asyncio.sleep(0.5)
                        
                        logger.info(f"   ✅ Sent {len(lesson_posts)} lesson posts as separate blocks (with [POST] markers) for day {day}")
                        
                        # Обновляем наборы отправленных медиа из данных _send_text_with_inline_media
                        for (fid, path, filename) in all_sent_media_from_posts:
                            if fid:
                                sent_media_file_ids.add(fid)
                            if path:
                                sent_media_paths.add(path)
                            if filename:
                                sent_media_filenames.add(filename)
                        
                        # Также добавляем медиа из media_markers для обратной совместимости
                        if media_markers:
                            for marker_id, marker_info in media_markers.items():
                                if any(f"[{marker_id}]" in post for post in lesson_posts):
                                    fid = marker_info.get("file_id")
                                    if fid:
                                        sent_media_file_ids.add(str(fid))
                                    path = marker_info.get("path")
                                    if path:
                                        normalized_path = str(Path(path)).replace('\\', '/')
                                        sent_media_paths.add(normalized_path)
                                        sent_media_filenames.add(Path(path).name)
                        
                        # Отправляем только те медиа из списка, которые НЕ были отправлены через маркеры в постах
                        while media_index < media_count:
                            media_item = media_list[media_index]
                            media_file_id = str(media_item.get("file_id") or "")
                            media_path = str(media_item.get("path") or "")
                            media_name = Path(media_path).name if media_path else ""
                            normalized_media_path = str(Path(media_path)).replace('\\', '/') if media_path else ""
                            
                            # Проверяем, не было ли это медиа уже отправлено через маркер (по file_id, path или имени)
                            already_sent = (
                                (media_file_id and media_file_id in sent_media_file_ids) or
                                (normalized_media_path and normalized_media_path in sent_media_paths) or
                                (media_name and media_name in sent_media_filenames)
                            )
                            
                            if not already_sent:
                                await self._send_media_item(user.user_id, media_item, day)
                                logger.info(f"   ✅ Sent remaining media {media_index + 1}/{media_count} after posts for lesson {day}")
                            else:
                                logger.info(f"   ⏭️ Skipped media {media_index + 1}/{media_count} (already sent via marker in post) for lesson {day}")
                            media_index += 1
                            await asyncio.sleep(0.3)
                    else:
                        # Если только один пост, обрабатываем его как обычно
                        # Проверяем, есть ли маркеры медиа в тексте для встроенной вставки
                        sent_media_from_text = set()
                        if media_markers and any(f"[{marker}]" in text for marker in media_markers.keys()):
                            # Используем встроенную вставку медиа по маркерам
                            sent_media_from_text = await self._send_text_with_inline_media(user.user_id, text, media_markers, day)
                            logger.info(f"   ✅ Sent lesson text with inline media markers for day {day}")
                            
                            # НЕ отправляем медиа из списка, если они уже отправлены через маркеры
                            # Используем данные из _send_text_with_inline_media для точной фильтрации
                            sent_media_file_ids = set()
                            sent_media_paths = set()
                            sent_media_filenames = set()
                            for (fid, path, filename) in sent_media_from_text:
                                if fid:
                                    sent_media_file_ids.add(fid)
                                if path:
                                    sent_media_paths.add(path)
                                if filename:
                                    sent_media_filenames.add(filename)
                            
                            # Также добавляем медиа из media_markers для обратной совместимости
                            for marker_id, marker_info in media_markers.items():
                                if f"[{marker_id}]" in text:
                                    fid = marker_info.get("file_id")
                                    if fid:
                                        sent_media_file_ids.add(str(fid))
                                    path = marker_info.get("path")
                                    if path:
                                        normalized_path = str(Path(path)).replace('\\', '/')
                                        sent_media_paths.add(normalized_path)
                                        sent_media_filenames.add(Path(path).name)
                            
                            # Отправляем только те медиа из списка, которые НЕ были отправлены через маркеры
                            while media_index < media_count:
                                media_item = media_list[media_index]
                                media_file_id = str(media_item.get("file_id") or "")
                                media_path = str(media_item.get("path") or "")
                                media_name = Path(media_path).name if media_path else ""
                                normalized_media_path = str(Path(media_path)).replace('\\', '/') if media_path else ""
                                
                                # Проверяем, не было ли это медиа уже отправлено через маркер (по file_id, path или имени)
                                already_sent = (
                                    (media_file_id and media_file_id in sent_media_file_ids) or
                                    (normalized_media_path and normalized_media_path in sent_media_paths) or
                                    (media_name and media_name in sent_media_filenames)
                                )
                                
                                if not already_sent:
                                    await self._send_media_item(user.user_id, media_item, day)
                                    logger.info(f"   ✅ Sent remaining media {media_index + 1}/{media_count} after inline media for lesson {day}")
                                else:
                                    logger.info(f"   ⏭️ Skipped media {media_index + 1}/{media_count} (already sent via marker) for lesson {day}")
                                media_index += 1
                                await asyncio.sleep(0.3)
                        else:
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
                                        await self._safe_send_message(user.user_id, paragraph)
                                        await asyncio.sleep(0.2)
                                    
                                    # Если наступила позиция для медиа, отправляем его
                                    if (i + 1) in media_positions and media_index < media_count:
                                        await self._send_media_item(user.user_id, media_list[media_index], day)
                                        logger.info(f"   ✅ Sent media {media_index + 1}/{media_count} in text for lesson {day}")
                                        media_index += 1
                                        await asyncio.sleep(0.3)
                                
                                # Отправляем оставшиеся медиа после последнего абзаца (если есть)
                                # ВАЖНО: Проверяем, не были ли медиа уже отправлены через маркеры
                                sent_media_file_ids = set()
                                sent_media_paths = set()
                                sent_media_filenames = set()
                                if media_markers:
                                    for marker_id, marker_info in media_markers.items():
                                        if f"[{marker_id}]" in text:
                                            fid = marker_info.get("file_id")
                                            if fid:
                                                sent_media_file_ids.add(str(fid))
                                            path = marker_info.get("path")
                                            if path:
                                                normalized_path = str(Path(path)).replace('\\', '/')
                                                sent_media_paths.add(normalized_path)
                                                sent_media_filenames.add(Path(path).name)
                    
                    while media_index < media_count:
                        media_item = media_list[media_index]
                        media_file_id = str(media_item.get("file_id") or "")
                        media_path = str(media_item.get("path") or "")
                        media_name = Path(media_path).name if media_path else ""
                        normalized_media_path = str(Path(media_path)).replace('\\', '/') if media_path else ""
                        
                        # Проверяем, не было ли это медиа уже отправлено через маркер (по file_id, path или имени)
                        already_sent = (
                            (media_file_id and media_file_id in sent_media_file_ids) or
                            (normalized_media_path and normalized_media_path in sent_media_paths) or
                            (media_name and media_name in sent_media_filenames)
                        )
                        
                        if not already_sent:
                            await self._send_media_item(user.user_id, media_item, day)
                            logger.info(f"   ✅ Sent remaining media {media_index + 1}/{media_count} after text for lesson {day}")
                        else:
                            logger.info(f"   ⏭️ Skipped media {media_index + 1}/{media_count} (already sent via marker) for lesson {day}")
                        media_index += 1
                        await asyncio.sleep(0.3)
                    else:
                        # Если нет абзацев (текст пустой или не разбивается на абзацы)
                                # ВАЖНО: Если урок разбит на несколько постов (через [POST]), 
                                # каждый пост отправляется как отдельный блок, медиа вставляется в соответствующий блок
                                if len(lesson_posts) > 1:
                                    # Собираем file_id и пути медиа, которые будут отправлены через маркеры в постах
                                    sent_media_file_ids = set()
                                    sent_media_paths = set()
                                    sent_media_filenames = set()
                                    if media_markers:
                                        for marker_id, marker_info in media_markers.items():
                                            if any(f"[{marker_id}]" in post for post in lesson_posts):
                                                fid = marker_info.get("file_id")
                                                if fid:
                                                    sent_media_file_ids.add(str(fid))
                                                path = marker_info.get("path")
                                                if path:
                                                    normalized_path = str(Path(path)).replace('\\', '/')
                                                    sent_media_paths.add(normalized_path)
                                                    sent_media_filenames.add(Path(path).name)
                                    
                                    # Обрабатываем все посты в цикле, включая первый
                                    # Каждый пост отправляется как отдельный блок (после [POST])
                                    for i, post_text in enumerate(lesson_posts):
                                        if post_text and post_text.strip():
                                            # Проверяем, есть ли медиа-маркеры в этом посте
                                            # Если есть, медиа будет встроено в этот блок
                                            if media_markers and any(f"[{marker}]" in post_text for marker in media_markers.keys()):
                                                await self._send_text_with_inline_media(user.user_id, post_text.strip(), media_markers, day)
                                                logger.info(f"   ✅ Sent lesson post {i + 1}/{len(lesson_posts)} with inline media markers for day {day} (separate block after [POST])")
                                            else:
                                                await self._safe_send_message(user.user_id, post_text.strip())
                                                logger.info(f"   ✅ Sent lesson post {i + 1}/{len(lesson_posts)} for day {day} (separate block after [POST])")
                                            
                                            # Пауза между блоками
                                            if i < len(lesson_posts) - 1:
                                                await asyncio.sleep(0.5)
                                    
                                    logger.info(f"   ✅ Sent {len(lesson_posts)} lesson posts as separate blocks (with [POST] markers) for day {day}")
                                    
                                    # Отправляем только те медиа из списка, которые НЕ были отправлены через маркеры в постах
                                    while media_index < media_count:
                                        media_item = media_list[media_index]
                                        media_file_id = str(media_item.get("file_id") or "")
                                        media_path = str(media_item.get("path") or "")
                                        media_name = Path(media_path).name if media_path else ""
                                        normalized_media_path = str(Path(media_path)).replace('\\', '/') if media_path else ""
                                        
                                        # Проверяем, не было ли это медиа уже отправлено через маркер (по file_id, path или имени)
                                        already_sent = (
                                            (media_file_id and media_file_id in sent_media_file_ids) or
                                            (normalized_media_path and normalized_media_path in sent_media_paths) or
                                            (media_name and media_name in sent_media_filenames)
                                        )
                                        
                                        if not already_sent:
                                            await self._send_media_item(user.user_id, media_item, day)
                                            logger.info(f"   ✅ Sent remaining media {media_index + 1}/{media_count} after posts for lesson {day}")
                                        else:
                                            logger.info(f"   ⏭️ Skipped media {media_index + 1}/{media_count} (already sent via marker in post) for lesson {day}")
                                        media_index += 1
                                        await asyncio.sleep(0.3)
                                elif text.strip():
                                    # Один пост - отправляем весь текст
                                    # ВАЖНО: Проверяем, не является ли этот текст дубликатом intro_text
                                    # Если intro_text уже отправлен отдельно, пропускаем текст, который содержит intro_text
                                    if intro_text_sent_separately and intro_text:
                                        intro_text_short = intro_text[:100] if len(intro_text) > 100 else intro_text
                                        intro_text_stripped = intro_text.strip()
                                        if intro_text_short in text or (len(intro_text) < 200 and intro_text_stripped in text):
                                            logger.warning(f"   ⚠️ Skipping text for day {day} - it contains intro_text which was already sent separately")
                                        else:
                                            # Проверяем маркеры в тексте
                                            if media_markers and any(f"[{marker}]" in text for marker in media_markers.keys()):
                                                await self._send_text_with_inline_media(user.user_id, text, media_markers, day)
                                                logger.info(f"   ✅ Sent text with inline media markers for day {day}")
                                            else:
                                                await self._safe_send_message(user.user_id, text)
                                            await asyncio.sleep(0.3)
                                    else:
                                        # Проверяем маркеры в тексте
                                        if media_markers and any(f"[{marker}]" in text for marker in media_markers.keys()):
                                            await self._send_text_with_inline_media(user.user_id, text, media_markers, day)
                                        else:
                                            await self._safe_send_message(user.user_id, text)
                                        await asyncio.sleep(0.3)
                                
                                # Отправляем все оставшиеся медиа
                                while media_index < media_count:
                                    await self._send_media_item(user.user_id, media_list[media_index], day)
                                    logger.info(f"   ✅ Sent remaining media {media_index + 1}/{media_count} after text for lesson {day}")
                                    media_index += 1
                                    await asyncio.sleep(0.3)
            else:
                # Если медиа нет или уже все отправлены, отправляем текст как обычно
                # ВАЖНО: Если урок разбит на несколько постов (через маркеры [POST]), 
                # каждый пост после [POST] отправляется как отдельный блок
                # Медиа-маркеры вставляются в тот блок (пост), где они находятся в тексте
                if len(lesson_posts) > 1:
                    # Собираем file_id и пути медиа, которые будут отправлены через маркеры в постах
                    sent_media_file_ids = set()
                    sent_media_paths = set()
                    sent_media_filenames = set()
                    if media_markers:
                        for marker_id, marker_info in media_markers.items():
                            if any(f"[{marker_id}]" in post for post in lesson_posts):
                                fid = marker_info.get("file_id")
                                if fid:
                                    sent_media_file_ids.add(str(fid))
                                path = marker_info.get("path")
                                if path:
                                    normalized_path = str(Path(path)).replace('\\', '/')
                                    sent_media_paths.add(normalized_path)
                                    sent_media_filenames.add(Path(path).name)
                    
                    # Обрабатываем все посты в цикле
                    # Каждый пост отправляется как отдельный блок (после [POST])
                    for i, post_text in enumerate(lesson_posts):
                        if post_text and post_text.strip():
                            # ВАЖНО: Проверяем, не является ли этот пост ТОЛЬКО intro_text (без дополнительного контента)
                            # Если intro_text уже отправлен отдельно, пропускаем пост ТОЛЬКО если он полностью состоит из intro_text
                            if intro_text_sent_separately and intro_text:
                                intro_text_stripped = intro_text.strip()
                                post_text_stripped = post_text.strip()
                                # Пропускаем только если пост полностью совпадает с intro_text (с небольшим допуском на форматирование)
                                if len(post_text_stripped) <= len(intro_text_stripped) * 1.1 and intro_text_stripped in post_text_stripped:
                                    # Проверяем, что пост не содержит дополнительного контента
                                    if len(post_text_stripped) - len(intro_text_stripped) < 50:  # Допускаем небольшую разницу
                                        logger.warning(f"   ⚠️ Skipping post {i} for day {day} - it is mostly intro_text which was already sent separately")
                                        continue
                                    else:
                                        logger.info(f"   ℹ️ Post {i} for day {day} contains intro_text but also additional content, sending it")
                            
                            # Анимация перед отправкой поста (только для первого)
                            if i == 0:
                                await send_typing_action(self.bot, user.user_id, 0.5)
                            
                            # Проверяем, есть ли медиа-маркеры в этом посте
                            # Если есть, медиа будет встроено в этот блок
                            if media_markers and any(f"[{marker}]" in post_text for marker in media_markers.keys()):
                                await self._send_text_with_inline_media(user.user_id, post_text.strip(), media_markers, day)
                                logger.info(f"   ✅ Sent lesson post {i + 1}/{len(lesson_posts)} with inline media markers for day {day} (separate block after [POST])")
                            else:
                                await self._safe_send_message(user.user_id, post_text.strip())
                                logger.info(f"   ✅ Sent lesson post {i + 1}/{len(lesson_posts)} for day {day} (separate block after [POST])")
                            
                            # Пауза между блоками (постами)
                            if i < len(lesson_posts) - 1:
                                await asyncio.sleep(0.5)
                    logger.info(f"   ✅ Sent {len(lesson_posts)} lesson posts as separate blocks (with [POST] markers) for day {day}")
                    
                    # Отправляем только те медиа из списка, которые НЕ были отправлены через маркеры
                    while media_index < media_count:
                        media_item = media_list[media_index]
                        media_file_id = str(media_item.get("file_id") or "")
                        media_path = str(media_item.get("path") or "")
                        media_name = Path(media_path).name if media_path else ""
                        normalized_media_path = str(Path(media_path)).replace('\\', '/') if media_path else ""
                        
                        # Проверяем, не было ли это медиа уже отправлено через маркер (по file_id, path или имени)
                        already_sent = (
                            (media_file_id and media_file_id in sent_media_file_ids) or
                            (normalized_media_path and normalized_media_path in sent_media_paths) or
                            (media_name and media_name in sent_media_filenames)
                        )
                        
                        if not already_sent:
                            await self._send_media_item(user.user_id, media_item, day)
                            logger.info(f"   ✅ Sent remaining media {media_index + 1}/{media_count} after posts for lesson {day}")
                        else:
                            logger.info(f"   ⏭️ Skipped media {media_index + 1}/{media_count} (already sent via marker) for lesson {day}")
                        media_index += 1
                        await asyncio.sleep(0.3)
                elif text.strip():
                    # Single post: send as before (backward compatible)
                    # Анимация перед отправкой текста
                    await send_typing_action(self.bot, user.user_id, 0.5)
                    # Проверяем маркеры в тексте
                    sent_media_file_ids = set()
                    sent_media_paths = set()
                    sent_media_filenames = set()
                    if media_markers and any(f"[{marker}]" in text for marker in media_markers.keys()):
                        await self._send_text_with_inline_media(user.user_id, text, media_markers, day)
                        # Собираем file_id и пути медиа, которые были отправлены через маркеры
                        for marker_id, marker_info in media_markers.items():
                            if f"[{marker_id}]" in text:
                                fid = marker_info.get("file_id")
                                if fid:
                                    sent_media_file_ids.add(str(fid))
                                path = marker_info.get("path")
                                if path:
                                    normalized_path = str(Path(path)).replace('\\', '/')
                                    sent_media_paths.add(normalized_path)
                                    sent_media_filenames.add(Path(path).name)
                    else:
                        await self._safe_send_message(user.user_id, text)
                    await asyncio.sleep(0.5)  # Пауза для плавности
                    
                    # Отправляем только те медиа из списка, которые НЕ были отправлены через маркеры
                    while media_index < media_count:
                        media_item = media_list[media_index]
                        media_file_id = str(media_item.get("file_id") or "")
                        media_path = str(media_item.get("path") or "")
                        media_name = Path(media_path).name if media_path else ""
                        normalized_media_path = str(Path(media_path)).replace('\\', '/') if media_path else ""
                        
                        # Проверяем, не было ли это медиа уже отправлено через маркер (по file_id, path или имени)
                        already_sent = (
                            (media_file_id and media_file_id in sent_media_file_ids) or
                            (normalized_media_path and normalized_media_path in sent_media_paths) or
                            (media_name and media_name in sent_media_filenames)
                        )
                        
                        if not already_sent:
                            await self._send_media_item(user.user_id, media_item, day)
                            logger.info(f"   ✅ Sent remaining media {media_index + 1}/{media_count} after text for lesson {day}")
                        else:
                            logger.info(f"   ⏭️ Skipped media {media_index + 1}/{media_count} (already sent via marker) for lesson {day}")
                        media_index += 1
                        await asyncio.sleep(0.3)
            
            # Для урока 19 отправляем кнопку "Показать все уровни" ПЕРЕД заданием
            if (day == 19 or str(day) == "19"):
                levels_images = lesson_data.get("levels_images", [])
                if levels_images:
                    # Анимация перед отправкой кнопки
                    await send_typing_action(self.bot, user.user_id, 0.4)
                    show_levels_keyboard = InlineKeyboardMarkup(inline_keyboard=[[
                        InlineKeyboardButton(
                            text="Показать все уровни",
                            callback_data="lesson19_show_levels"
                        )
                    ]])
                    await self.bot.send_message(
                        user.user_id,
                        "<b>Эмоциональные уровни</b>\n\nНажмите кнопку ниже, чтобы посмотреть все уровни:",
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
                    
                    # Do not hardcode caption text here: it causes duplicate text if the same lines
                    # are already present in the lesson body. If you need a caption, store it in
                    # lessons.json (e.g. lesson1_video_caption) and keep the body text unchanged.
                    original_video_caption = (lesson_data.get("lesson1_video_caption") or "").strip() or None
                    # Добавляем разделитель к caption для визуального расширения блока медиа
                    video_caption = self._add_media_separator(original_video_caption)
                    
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
                                supports_streaming=True,
                                protect_content=True
                            )
                        else:
                            await self.bot.send_photo(user.user_id, file_id, caption=video_caption, protect_content=True)
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
                    error_msg = str(video_error).lower()
                    if "entity too large" in error_msg or "file too large" in error_msg:
                        # Если видео слишком большое, отправляем ссылку на Google Drive
                        original_file_id = lesson1_video_media.get("file_id")
                        if original_file_id:
                            drive_link = f"https://drive.google.com/file/d/{original_file_id}/view"
                            await self._safe_send_message(
                                user.user_id,
                                f"📹 <b>Видео слишком большое для прямой отправки</b>\n\n"
                                f"Ссылка на видео:\n"
                                f"<a href=\"{drive_link}\">Открыть видео в Google Drive</a>"
                            )
                            logger.info(f"   ✅ Sent Google Drive link for large lesson 1 video")
                        else:
                            logger.warning(f"   ⚠️ Не удалось отправить видео урока 1 перед заданием: {video_error}")
                    else:
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
            
            # Если в тексте урока есть ссылки на видео/картинки — отправляем медиа из URL.
            # Медиа отправляется в том месте, где указана ссылка в тексте.
            # URL удаляются из текста перед отправкой, чтобы избежать дублирования.
            sent_preview_urls = set()
            try:
                combined_text = combined_text_raw
                sent_preview_urls = await self._send_previews_from_text(
                    user.user_id,
                    combined_text,
                    seen=link_preview_seen,
                    limit=6,
                )
                # Удаляем отправленные URL из текста перед отправкой
                if sent_preview_urls:
                    intro_text = self._strip_url_only_lines(intro_text, sent_preview_urls)
                    about_me_text = self._strip_url_only_lines(about_me_text, sent_preview_urls)
                    lesson_posts = [self._strip_url_only_lines(p, sent_preview_urls) for p in lesson_posts]
            except Exception as e:
                logger.warning(f"   ⚠️ Error sending previews from text: {e}")

            # Формируем сообщение с заданием - только текст из Google Doc, без эмодзи и разделителей
            task_message = ""
            if task:
                # Анимация перед отправкой задания
                await send_typing_action(self.bot, user.user_id, 0.6)
                # Avoid duplicating the "Задание" heading if it's already present in the task text.
                if re.match(r"^\s*(?:📝\s*)?задание\b", (task or ""), re.IGNORECASE):
                    task_message = (task or "").strip()
                else:
                    task_message = f"📝 Задание:\n\n{task}".strip()
            
            # Если в задании есть ссылки на медиа — отправляем медиа из URL.
            # URL удаляются из текста задания перед отправкой, чтобы избежать дублирования.
            task_sent_preview_urls = set()
            try:
                task_sent_preview_urls = await self._send_previews_from_text(
                    user.user_id,
                    task_message,
                    seen=link_preview_seen,
                    limit=6,
                    day=day,
                )
            except Exception as e:
                logger.warning(f"   ⚠️ Error sending task previews: {e}")
            
            # Удаляем отправленные URL из текста задания перед отправкой
            task_message_clean = task_message
            if task_sent_preview_urls:
                task_message_clean = self._strip_url_only_lines(task_message, task_sent_preview_urls)
            
            # Отправляем задание, если есть
            if task_message_clean:
                # Передаем day в lesson_data для создания клавиатуры
                lesson_data_with_day = lesson_data.copy()
                lesson_data_with_day["day_number"] = day
                # Ensure submit-assignment button appears (keyboard checks lesson_data["task*"])
                lesson_data_with_day["task"] = task
                logger.info(f"   📝 Creating keyboard for task message, day={day} (type={type(day).__name__})")
                keyboard = create_lesson_keyboard_from_json(lesson_data_with_day, user, Config.GENERAL_GROUP_ID)

                # Ensure submit-assignment button is present under the task message.
                submit_row = [
                    InlineKeyboardButton(
                        text=f"📝 Отправить ответ на задание №{int(day)}",
                        callback_data=f"assignment:submit:lesson_{int(day)}",
                    )
                ]
                try:
                    has_submit = False
                    if keyboard and hasattr(keyboard, "inline_keyboard"):
                        for row in (keyboard.inline_keyboard or []):
                            for btn in (row or []):
                                cb = getattr(btn, "callback_data", None)
                                if cb and str(cb).startswith("assignment:submit:"):
                                    has_submit = True
                                    break
                            if has_submit:
                                break

                    if not has_submit:
                        if not keyboard or not hasattr(keyboard, "inline_keyboard"):
                            keyboard = InlineKeyboardMarkup(inline_keyboard=[])
                        keyboard.inline_keyboard.insert(0, submit_row)
                except Exception:
                    keyboard = InlineKeyboardMarkup(inline_keyboard=[submit_row])
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
                        
                        # Добавляем кнопку к существующей клавиатуре (не затирая submit-кнопку)
                        if not keyboard or not hasattr(keyboard, "inline_keyboard"):
                            keyboard = InlineKeyboardMarkup(inline_keyboard=[submit_row])
                        keyboard.inline_keyboard.append(download_button)
                        logger.info(f"   ✅ Added download button to task keyboard for lesson 21")
                
                logger.info(f"   Sending task message to user {user.user_id}, day {day}")
                
                # Проверяем, есть ли маркеры медиа в задании
                task_sent_with_media = False
                if media_markers and any(f"[{marker}]" in task_message_clean for marker in media_markers.keys()):
                    # Используем встроенную вставку медиа по маркерам в задании
                    # ВАЖНО: Кнопка должна быть строго под последним блоком задания
                    # Отправляем задание с маркерами (без префикса "📝 Задание:")
                    task_text_without_prefix = task if task else ""
                    # Отправляем задание с медиа, передавая клавиатуру для последнего сообщения
                    await self._send_text_with_inline_media(user.user_id, task_text_without_prefix, media_markers, day, keyboard=keyboard)
                    logger.info(f"   ✅ Sent task with inline media markers for day {day} (keyboard attached to last message)")
                    task_sent_with_media = True
                
                if not task_sent_with_media:
                    logger.info(f"   Task message length: {len(task_message_clean)} characters")
                    
                    # Разбиваем длинные сообщения на части (лимит Telegram: 4096 символов)
                    MAX_MESSAGE_LENGTH = 4000  # Оставляем запас
                    if len(task_message_clean) > MAX_MESSAGE_LENGTH:
                        # Разбиваем сообщение на части
                        message_parts = self._split_long_message(task_message_clean, MAX_MESSAGE_LENGTH)
                        logger.info(f"   Task message split into {len(message_parts)} parts")

                        # Кнопка должна быть прямо "под заданием", поэтому:
                        # - находим последнюю НЕпустую часть
                        # - отправляем её с клавиатурой
                        last_non_empty_idx = None
                        for idx in range(len(message_parts) - 1, -1, -1):
                            if message_parts[idx] and message_parts[idx].strip():
                                last_non_empty_idx = idx
                                break

                        if last_non_empty_idx is None:
                            logger.warning("   Task message parts are empty after split; skipping task send")
                        else:
                            # Отправляем все части до последней непустой без клавиатуры
                            for i, part in enumerate(message_parts[:last_non_empty_idx], 1):
                                if part and part.strip():
                                    await self._safe_send_message(user.user_id, part, protect_content=True)
                                    await asyncio.sleep(0.3)  # Небольшая пауза между сообщениями
                                    logger.info(f"   Sent task part {i}/{len(message_parts)}")
                                else:
                                    logger.warning(f"   Skipped empty task part {i}/{len(message_parts)}")

                            # Отправляем последнюю непустую часть с клавиатурой
                            sent = await self.bot.send_message(
                                user.user_id,
                                message_parts[last_non_empty_idx],
                                reply_markup=keyboard,
                                disable_web_page_preview=True,
                                protect_content=True,
                            )
                            try:
                                await self.bot.edit_message_reply_markup(
                                    chat_id=user.user_id,
                                    message_id=sent.message_id,
                                    reply_markup=keyboard,
                                )
                            except Exception:
                                pass
                            logger.info(
                                f"   Sent task part {last_non_empty_idx + 1}/{len(message_parts)} with keyboard"
                            )
                    else:
                        # Если сообщение короткое, отправляем как есть (с водяным знаком)
                        # Используем _safe_send_message, который автоматически добавит водяной знак
                        sent = await self._safe_send_message(user.user_id, task_message_clean, reply_markup=keyboard, protect_content=True, parse_mode="HTML")
                        try:
                            await self.bot.edit_message_reply_markup(
                                chat_id=user.user_id,
                                message_id=sent.message_id,
                                reply_markup=keyboard,
                            )
                        except Exception:
                            pass

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
                                text="Показать все уровни",
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
                        "<b>Эмоциональные уровни</b>\n\nНажмите кнопку ниже, чтобы посмотреть все уровни:",
                        reply_markup=keyboard,
                        parse_mode="HTML"
                    )
                    logger.info(f"   ✅ Sent message with show levels button for lesson 19")
                elif keyboard and hasattr(keyboard, 'inline_keyboard') and keyboard.inline_keyboard and len(keyboard.inline_keyboard) > 0:
                    await self.bot.send_message(user.user_id, KEYBOARD_ONLY_PLACEHOLDER, reply_markup=keyboard)
                    logger.info(f"   ✅ Sent message with keyboard for lesson {day}")
                else:
                    await self.bot.send_message(user.user_id, KEYBOARD_ONLY_PLACEHOLDER)
                    logger.info(f"   ℹ️ No keyboard to send for lesson {day}")
            
            # Всегда устанавливаем постоянную клавиатуру после отправки урока
            persistent_keyboard = self._create_persistent_keyboard()
            await self.bot.send_message(user.user_id, KEYBOARD_ONLY_PLACEHOLDER, reply_markup=persistent_keyboard)
            
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
                        # Добавляем разделитель к caption для визуального расширения блока медиа
                        caption = self._add_media_separator()
                        await self.bot.send_photo(user.user_id, follow_up_photo_file_id, caption=caption)
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
                            # Обрабатываем изображение для мобильных устройств
                            resized_path = await self._resize_image_for_mobile(photo_path)
                            image_path_to_use = resized_path if resized_path else photo_path
                            photo_file = FSInputFile(image_path_to_use)
                            # Добавляем разделитель к caption для визуального расширения блока медиа
                            caption = self._add_media_separator()
                            await self.bot.send_photo(user.user_id, photo_file, caption=caption, protect_content=True)
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
                                    # Обрабатываем изображение для мобильных устройств
                                    resized_path = await self._resize_image_for_mobile(possible_path)
                                    image_path_to_use = resized_path if resized_path else possible_path
                                    photo_file = FSInputFile(image_path_to_use)
                                    # Добавляем разделитель к caption для визуального расширения блока медиа
                                    caption = self._add_media_separator()
                                    await self.bot.send_photo(user.user_id, photo_file, caption=caption)
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
                        await self._safe_send_message(user.user_id, follow_up_text, reply_markup=persistent_keyboard, protect_content=True)
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

        ctx = self._user_assignment_context.get(user_id) or {}
        if not ctx.get("waiting_for_assignment"):
            raise SkipHandler()

        user = await self.user_service.get_user(user_id)
        
        if not user or not user.has_access():
            raise SkipHandler()
        lesson_day = int(ctx.get("lesson_day") or user.current_day)
        self._user_assignment_context.pop(user_id, None)
        
        # Best-effort lesson/task lookup (submission should be accepted once user started the flow)
        lesson_data = None
        try:
            lesson_data = self.lesson_loader.get_lesson(lesson_day) if self.lesson_loader else None
        except Exception:
            lesson_data = None

        lesson_title = lesson_data.get("title", f"День {lesson_day}") if lesson_data else f"День {lesson_day}"
        task = self.lesson_loader.get_task_for_tariff(lesson_day, user.tariff) if (lesson_data and self.lesson_loader) else ""
        
        # Submit assignment
        # Создаем временный объект Lesson для совместимости с сервисом
        from core.models import Lesson
        from datetime import datetime
        temp_lesson = Lesson(
            lesson_id=lesson_day,  # Используем day_number как lesson_id
            day_number=lesson_day,
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
        
        # Log assignment submission
        try:
            await self.db.log_user_activity(user_id, "course", "assignment_submitted", f"lesson_{assignment.day_number}")
        except Exception:
            pass
        
        # Импортируем функцию для получения уведомления
        from bots.assignment_notifications import get_assignment_notification
        
        # Проверяем тариф: для FEEDBACK и PRACTIC отправляем кураторам, для BASIC - только уведомление
        needs_feedback = user.tariff in [Tariff.FEEDBACK, Tariff.PRACTIC]
        
        if needs_feedback:
            # Forward to admin (только для тарифов с обратной связью)
            safe_first_name = html.escape(user.first_name or "")
            safe_username = html.escape(user.username) if user.username else "\u041d\u0435 \u0443\u043a\u0430\u0437\u0430\u043d\u043e"
            safe_lesson_title = html.escape(lesson_title)
            safe_message_text = html.escape(message.text or "")
            admin_text = (
                f"<b>Новое задание</b>\n\n"
                f"👤 Пользователь: {safe_first_name} (@{safe_username})\n"
                f"🆔 ID пользователя: {user.user_id}\n"
                f"Урок: {safe_lesson_title}\n"
                f"🔢 ID задания: {assignment.assignment_id}\n\n"
                f"✍️ <b>Ответ:</b>\n{safe_message_text}"
            )
            
            # Send ONLY to PUP (admin bot)
            from utils.admin_helpers import is_admin_bot_configured, send_to_admin_bot
            if not is_admin_bot_configured():
                logger.error("Admin bot not configured (ADMIN_BOT_TOKEN / ADMIN_CHAT_ID). Cannot forward assignment.")
                await message.answer("❌ Не удалось отправить задание на проверку: канал кураторов не настроен.")
                return

            try:
                ok = await send_to_admin_bot(
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
                if not ok:
                    await message.answer("❌ Не удалось отправить задание кураторам. Попробуйте позже.")
                    return
            except Exception as e:
                logger.error(f"Error sending to admin bot: {e}", exc_info=True)
                await message.answer("❌ Не удалось отправить задание кураторам. Попробуйте позже.")
                return
        
        # Отправляем уведомление пользователю (для всех тарифов)
        persistent_keyboard = self._create_persistent_keyboard()
        notification_text = get_assignment_notification(lesson_day)
        
        if needs_feedback:
            # Для тарифов с обратной связью - уведомление + информация о кураторах
            await message.answer(
                f"{notification_text}\n\n"
                "📤 Ваше задание направлено кураторам 👥.\n"
                "⏳ Вы получите обратную связь в ближайшее время 💬.",
                reply_markup=persistent_keyboard
            )
        else:
            # Для BASIC тарифа - только уведомление
            await message.answer(
                notification_text,
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

        ctx = self._user_assignment_context.get(user_id) or {}
        if not ctx.get("waiting_for_assignment"):
            raise SkipHandler()

        user = await self.user_service.get_user(user_id)
        
        if not user or not user.has_access():
            raise SkipHandler()
        lesson_day = int(ctx.get("lesson_day") or user.current_day)
        self._user_assignment_context.pop(user_id, None)
        
        # Best-effort lesson/task lookup (submission should be accepted once user started the flow)
        lesson_data = None
        try:
            lesson_data = self.lesson_loader.get_lesson(lesson_day) if self.lesson_loader else None
        except Exception:
            lesson_data = None

        lesson_title = lesson_data.get("title", f"День {lesson_day}") if lesson_data else f"День {lesson_day}"
        task = self.lesson_loader.get_task_for_tariff(lesson_day, user.tariff) if (lesson_data and self.lesson_loader) else ""
        
        # Collect media file IDs
        media_ids = []
        if message.photo:
            media_ids.append(f"photo:{message.photo[-1].file_id}")
        elif message.video:
            media_ids.append(f"video:{message.video.file_id}")
        elif message.document:
            media_ids.append(f"document:{message.document.file_id}")
        elif message.voice:
            media_ids.append(f"voice:{message.voice.file_id}")
        
        # Создаем временный объект Lesson для совместимости
        from core.models import Lesson
        from datetime import datetime
        temp_lesson = Lesson(
            lesson_id=lesson_day,  # Используем day_number как lesson_id (int)
            day_number=lesson_day,
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
            submission_media_ids=[
                message.photo[-1].file_id if message.photo
                else message.video.file_id if message.video
                else message.document.file_id if message.document
                else message.voice.file_id if message.voice
                else None
            ]
        )
        
        # Log assignment submission
        try:
            await self.db.log_user_activity(user_id, "course", "assignment_submitted", f"lesson_{assignment.day_number}")
        except Exception:
            pass
        
        # Импортируем функцию для получения уведомления
        from bots.assignment_notifications import get_assignment_notification
        
        # Проверяем тариф: для FEEDBACK и PRACTIC отправляем кураторам, для BASIC - только уведомление
        needs_feedback = user.tariff in [Tariff.FEEDBACK, Tariff.PRACTIC]
        
        if needs_feedback:
            # Forward to admin (только для тарифов с обратной связью)
            safe_first_name = html.escape(user.first_name or "")
            safe_username = html.escape(user.username) if user.username else "\u041d\u0435 \u0443\u043a\u0430\u0437\u0430\u043d\u043e"
            safe_lesson_title = html.escape(lesson_title)
            safe_caption = html.escape(message.caption or "")

            admin_text = (
                f"<b>Новое задание (Медиа)</b>\n\n"
                f"👤 Пользователь: {safe_first_name} (@{safe_username})\n"
                f"🆔 ID пользователя: {user.user_id}\n"
                f"Урок: {safe_lesson_title}\n"
                f"🔢 ID задания: {assignment.assignment_id}"
            )
            
            if message.caption:
                admin_text += f"\n\n✍️ <b>Подпись:</b>\n{safe_caption}"
            
            # Forward media to admin
            from utils.admin_helpers import is_admin_bot_configured, send_to_admin_bot
            if not is_admin_bot_configured():
                logger.error("Admin bot not configured (ADMIN_BOT_TOKEN / ADMIN_CHAT_ID). Cannot forward assignment media.")
                await message.answer("❌ Не удалось отправить задание на проверку: канал кураторов не настроен.")
                return

            reply_kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💬 Ответить пользователю", callback_data=f"admin_reply:{assignment.assignment_id}")]
            ])

            try:
                if message.photo:
                    ok = await send_to_admin_bot(admin_text, photo_file_id=message.photo[-1].file_id, reply_markup=reply_kb)
                elif message.video:
                    ok = await send_to_admin_bot(admin_text, video_file_id=message.video.file_id, reply_markup=reply_kb)
                elif message.document:
                    ok = await send_to_admin_bot(admin_text, document_file_id=message.document.file_id, reply_markup=reply_kb)
                elif message.voice:
                    # Re-upload voice to PUP: file_id from course bot is not valid for admin bot token.
                    import io
                    buf = io.BytesIO()
                    await self.bot.download(message.voice, destination=buf)
                    ok = await send_to_admin_bot(admin_text, voice_bytes=buf.getvalue(), voice_filename="voice.ogg", reply_markup=reply_kb)
                else:
                    ok = False
                if not ok:
                    await message.answer("❌ Не удалось отправить задание кураторам. Попробуйте позже.")
                    return
            except Exception as e:
                logger.error(f"Error sending assignment media to admin bot: {e}", exc_info=True)
                await message.answer("❌ Не удалось отправить задание кураторам. Попробуйте позже.")
                return
        
        # Отправляем уведомление пользователю (для всех тарифов)
        persistent_keyboard = self._create_persistent_keyboard()
        notification_text = get_assignment_notification(lesson_day)
        
        if needs_feedback:
            # Для тарифов с обратной связью - уведомление + информация о кураторах
            await message.answer(
                f"{notification_text}\n\n"
                "📤 Ваше задание направлено кураторам 👥.\n"
                "⏳ Вы получите обратную связь в ближайшее время 💬.",
                reply_markup=persistent_keyboard
            )
        else:
            # Для BASIC тарифа - только уведомление
            await message.answer(
                notification_text,
                reply_markup=persistent_keyboard
            )
        
        # Отправляем follow_up_text для урока 0 после отправки задания (медиа)
        if lesson_day == 0:
            lesson_data = self.lesson_loader.get_lesson(0)
            if lesson_data and lesson_data.get("follow_up_text"):
                await asyncio.sleep(1)  # Небольшая пауза перед отправкой
                await message.answer(lesson_data["follow_up_text"], reply_markup=persistent_keyboard)
    
    async def handle_question_text(self, message: Message):
        """Handle question text submission."""
        user_id = message.from_user.id

        context = self._user_question_context.get(user_id) or {}
        if not context.get("waiting_for_question"):
            raise SkipHandler()

        # Получаем или создаем пользователя, если его нет
        user = await self.user_service.get_user(user_id)
        if not user:
            # Создаем пользователя автоматически, если его нет (может быть тестовый пользователь или новый)
            user = await self.user_service.get_or_create_user(
                user_id=user_id,
                username=message.from_user.username,
                first_name=message.from_user.first_name,
                last_name=message.from_user.last_name
            )
            logger.info(f"Auto-created user {user_id} when asking question")
        
        if not user.has_access():
            raise SkipHandler()

        lesson_day = int(context.get("lesson_day") or user.current_day)
        self._user_question_context.pop(user_id, None)
        
        # Обрабатываем как вопрос
        lesson_id = lesson_day if lesson_day else None
        question_data = await self.question_service.create_question(
            user_id=user_id,
            lesson_id=lesson_id,
            question_text=message.text,
            question_voice_file_id=None,
            context=f"День {lesson_day}" if lesson_day else None
        )
        
        # Log question activity
        try:
            await self.db.log_user_activity(user_id, "course", "question", f"lesson_{lesson_day}" if lesson_day else "general")
        except Exception:
            pass
        
        # Форматируем вопрос для ПУП (админ-бот)
        curator_message = await self.question_service.format_question_for_admin(question_data)
        
        # Send to PUP (admin bot) - same mechanism as assignments
        question_id = question_data.get("question_id")
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💬 Ответить пользователю",
                    callback_data=f"curator_reply:{question_id}"
                )
            ]
        ])
        
        # Отправляем в ПУП через админ-бот (как задания)
        from utils.admin_helpers import is_admin_bot_configured, send_to_admin_bot
        if not is_admin_bot_configured():
            logger.error("Admin bot not configured (ADMIN_BOT_TOKEN / ADMIN_CHAT_ID). Cannot forward question.")
            await message.answer("❌ Не удалось отправить вопрос: канал кураторов не настроен.")
            return

        try:
            ok = await send_to_admin_bot(
                curator_message,
                reply_markup=keyboard
            )
            if not ok:
                await message.answer("❌ Не удалось отправить вопрос кураторам. Попробуйте позже.")
                return
            
            logger.info(f"✅ Question sent to PUP (admin bot) from user {user_id}, question_id={question_id}")
        except Exception as e:
            logger.error(f"Error sending question to admin bot: {e}", exc_info=True)
            await message.answer("❌ Не удалось отправить вопрос кураторам. Попробуйте позже.")
            return
        
        persistent_keyboard = self._create_persistent_keyboard()
        await message.answer(
            "✅ <b>Вопрос отправлен!</b>\n\n"
            "📤 Ваш вопрос отправлен куратору.\n"
            "⏳ Мы ответим вам как можно скорее 💬.",
            reply_markup=persistent_keyboard
        )

    async def handle_question_voice(self, message: Message):
        """Handle voice question submission (when question flow is active)."""
        user_id = message.from_user.id

        context = self._user_question_context.get(user_id) or {}
        if not context.get("waiting_for_question"):
            raise SkipHandler()

        # Получаем или создаем пользователя, если его нет
        user = await self.user_service.get_user(user_id)
        if not user:
            # Создаем пользователя автоматически, если его нет
            user = await self.user_service.get_or_create_user(
                user_id=user_id,
                username=message.from_user.username,
                first_name=message.from_user.first_name,
                last_name=message.from_user.last_name
            )
            logger.info(f"Auto-created user {user_id} when asking voice question")
        
        if not user.has_access() or not message.voice:
            raise SkipHandler()

        lesson_day = int(context.get("lesson_day") or user.current_day)
        self._user_question_context.pop(user_id, None)

        question_data = await self.question_service.create_question(
            user_id=user_id,
            lesson_id=lesson_day,
            question_text=None,
            question_voice_file_id=message.voice.file_id if message.voice else None,
            context=f"День {lesson_day}",
        )
        curator_message = await self.question_service.format_question_for_admin(question_data)

        # Send to PUP (admin bot) - same mechanism as assignments
        question_id = question_data.get("question_id")
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💬 Ответить пользователю",
                    callback_data=f"curator_reply:{question_id}"
                )
            ]
        ])

        # Отправляем в ПУП через админ-бот (как задания)
        from utils.admin_helpers import is_admin_bot_configured, send_to_admin_bot
        if not is_admin_bot_configured():
            logger.error("Admin bot not configured (ADMIN_BOT_TOKEN / ADMIN_CHAT_ID). Cannot forward question.")
            await message.answer("❌ Не удалось отправить вопрос: канал кураторов не настроен.")
            return

        try:
            # Загружаем голосовое сообщение
            import io
            buf = io.BytesIO()
            await self.bot.download(message.voice, destination=buf)
            
            # Отправляем в ПУП через админ-бот (как голосовые задания)
            ok = await send_to_admin_bot(
                curator_message,
                voice_bytes=buf.getvalue(),
                voice_filename="voice.ogg",
                reply_markup=keyboard
            )
            if not ok:
                await message.answer("❌ Не удалось отправить вопрос кураторам. Попробуйте позже.")
                return
            
            logger.info(f"✅ Voice question sent to PUP (admin bot) from user {user_id}, question_id={question_id}")
        except Exception as e:
            logger.error(f"Error sending voice question to admin bot: {e}", exc_info=True)
            await message.answer("❌ Не удалось отправить вопрос кураторам. Попробуйте позже.")
            return

        persistent_keyboard = self._create_persistent_keyboard()
        await message.answer(
            "✅ <b>Вопрос отправлен!</b>\n\n"
            "📤 Ваше голосовое отправлено куратору.\n"
            "⏳ Мы ответим вам как можно скорее 💬.",
            reply_markup=persistent_keyboard
        )

    def _quick_help_keyboard(self, user: User) -> InlineKeyboardMarkup:
        day = int(getattr(user, "current_day", 0) or 0)
        rows: list[list[InlineKeyboardButton]] = [
            [InlineKeyboardButton(text="❓ Задать вопрос", callback_data=f"question:ask:lesson_{day}")],
        ]
        lesson_data = self.lesson_loader.get_lesson(day)
        task = self.lesson_loader.get_task_for_tariff(day, user.tariff) if lesson_data else None
        if task:
            rows.insert(0, [InlineKeyboardButton(text=f"📝 Отправить ответ на задание №{day}", callback_data=f"assignment:submit:lesson_{day}")])
        rows.append([InlineKeyboardButton(text="🧭 Навигатор", callback_data="navigator:open")])
        return InlineKeyboardMarkup(inline_keyboard=rows)

    async def handle_unclassified_text(self, message: Message):
        user_id = message.from_user.id
        user = await self.user_service.get_user(user_id)
        if not user or not user.has_access():
            raise SkipHandler()

        # If some flow is active, let the specific handlers handle it.
        if (self._user_assignment_context.get(user_id) or {}).get("waiting_for_assignment"):
            raise SkipHandler()
        if (self._user_question_context.get(user_id) or {}).get("waiting_for_question"):
            raise SkipHandler()

        await message.answer(
            "Я получил сообщение.\n\n"
            "Это <b>вопрос</b> или <b>задание</b>?\n"
            "Нажмите кнопку ниже и отправьте одним сообщением.",
            reply_markup=self._quick_help_keyboard(user),
        )

    async def handle_unclassified_voice(self, message: Message):
        user_id = message.from_user.id
        user = await self.user_service.get_user(user_id)
        if not user or not user.has_access():
            raise SkipHandler()

        if (self._user_assignment_context.get(user_id) or {}).get("waiting_for_assignment"):
            raise SkipHandler()
        if (self._user_question_context.get(user_id) or {}).get("waiting_for_question"):
            raise SkipHandler()

        await message.answer(
            "Чтобы отправить голосовое как <b>вопрос</b> или <b>задание</b>, сначала нажмите ❓ или 📝, затем пришлите голосовое.",
            reply_markup=self._quick_help_keyboard(user),
        )

    async def handle_unclassified_media(self, message: Message):
        user_id = message.from_user.id
        user = await self.user_service.get_user(user_id)
        if not user or not user.has_access():
            raise SkipHandler()

        if (self._user_assignment_context.get(user_id) or {}).get("waiting_for_assignment"):
            raise SkipHandler()
        if (self._user_question_context.get(user_id) or {}).get("waiting_for_question"):
            raise SkipHandler()

        await message.answer(
            "Чтобы отправить медиа как <b>задание</b>, нажмите 📝 и отправьте файл.\n"
            "Чтобы задать <b>вопрос</b>, нажмите ❓ и напишите/запишите вопрос.",
            reply_markup=self._quick_help_keyboard(user),
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
        try:
            await callback.answer()
        except:
            pass
        
        try:
            # Извлекаем question_id из callback_data
            # Формат: curator_reply:question_id
            data_parts = callback.data.split(":")
            if len(data_parts) < 2:
                await callback.message.answer("❌ Ошибка: неверный формат данных.")
                return
            
            question_id = int(data_parts[1])
            
            # Получаем информацию о вопросе из БД
            question = await self.question_service.get_question(question_id)
            if not question:
                await callback.message.answer("❌ Вопрос не найден.")
                return
            
            user_id = question.get("user_id")
            lesson_day = question.get("day_number") or question.get("lesson_id")
            
            # Проверяем, откуда пришел callback (группа или личный чат)
            if callback.message.chat.type in ["group", "supergroup"]:
                # В группе ПУП - можно отвечать прямо в группе через reply
                await callback.message.answer(
                    f"💬 <b>Ответ на вопрос</b>\n\n"
                    f"👤 Пользователь ID: {user_id}\n"
                    f"Урок: День {lesson_day}\n\n"
                    f"✍️ Ответьте на это сообщение (reply) с вашим ответом пользователю.\n\n"
                    f"💡 Ответ будет отправлен пользователю анонимно от имени бота.\n"
                    f"💬 Или ответьте прямо в группе - все увидят ваш ответ."
                )
            else:
                # В личном чате - стандартный ответ
                await callback.message.answer(
                    f"💬 <b>Ответ на вопрос</b>\n\n"
                    f"👤 Пользователь ID: {user_id}\n"
                    f"Урок: День {lesson_day}\n\n"
                    f"✍️ Ответьте на это сообщение с вашим ответом пользователю.\n\n"
                    f"💡 Ответ будет отправлен анонимно от имени бота."
                )
        except Exception as e:
            logger.error(f"❌ Error in handle_curator_reply: {e}", exc_info=True)
            try:
                await callback.message.answer("❌ Ошибка при обработке запроса.")
            except:
                pass
    
    async def handle_curator_feedback(self, message: Message):
        """
        Handle curator feedback reply to assignment (anonymous response).
        Works in PUP (premium group) - any user with access can reply.
        
        NOTE: Answers to questions are handled by admin_bot.py, not here.
        This handler only processes assignment feedback.
        """
        if not message.reply_to_message:
            raise SkipHandler()
        
        # Проверяем, что сообщение из ПУП (премиум-группы)
        # Если это не группа или не ПУП, пропускаем обработку
        if message.chat.type not in ["group", "supergroup"]:
            raise SkipHandler()
        
        # Проверяем, что это ПУП (премиум-группа)
        def parse_chat_id(raw: str) -> int:
            s = (raw or "").strip()
            if not s:
                return 0
            if s.startswith("#-") and s[2:].isdigit():
                return int(f"-100{s[2:]}")
            try:
                return int(s)
            except Exception:
                return 0
        
        pup_chat_id = parse_chat_id(Config.PREMIUM_GROUP_ID) if Config.PREMIUM_GROUP_ID else 0
        if message.chat.id != pup_chat_id:
            raise SkipHandler()
        
        reply_text = message.reply_to_message.text or message.reply_to_message.caption or ""
        
        # Проверяем, это ответ на вопрос или на задание
        # Если в сообщении есть "Новый вопрос" или "Вопрос:", это вопрос
        # ВАЖНО: Ответы на вопросы обрабатываются через admin_bot.py, не здесь
        is_question = (
            "❓" in reply_text or 
            "Новый вопрос" in reply_text or 
            "Вопрос:" in reply_text or
            "Ответ на вопрос" in reply_text or
            "curator_reply:" in str(message.reply_to_message.reply_markup) if message.reply_to_message and message.reply_to_message.reply_markup else False
        )
        
        # Проверяем, есть ли в callback_data кнопки curator_reply: или question:answer:
        if not is_question and message.reply_to_message and message.reply_to_message.reply_markup:
            try:
                for row in message.reply_to_message.reply_markup.inline_keyboard:
                    for button in row:
                        if button.callback_data:
                            if "curator_reply:" in button.callback_data or "question:answer:" in button.callback_data:
                                is_question = True
                                break
                    if is_question:
                        break
            except:
                pass
        
        # Если это вопрос - пропускаем обработку (admin_bot.py обработает)
        if is_question:
            raise SkipHandler()
        
        # Обрабатываем только задания (вопросы обрабатываются через admin_bot.py)
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
        # Извлекаем текст из сообщения (поддерживаем текст, подпись к медиа)
        feedback_text_raw = message.text or message.caption or ""
        feedback_text = feedback_text_raw.strip() if feedback_text_raw else ""
        
        # Логируем для отладки
        logger.info(f"   🔍 DEBUG handle_curator_feedback assignment: assignment_id={assignment_id}, "
                   f"message.text={bool(message.text)}, message.caption={bool(message.caption)}, "
                   f"feedback_text_raw length={len(feedback_text_raw)}, feedback_text length={len(feedback_text)}")
        
        # Проверяем, что текст не пустой (включая проверку на только эмодзи/пробелы)
        # Удаляем все эмодзи и проверяем, остался ли текст
        import re
        text_without_emoji = re.sub(r'[\U0001F300-\U0001F9FF\U0001FA00-\U0001FAFF\U00002600-\U000027BF\U0001F1E0-\U0001F1FF\U0001F680-\U0001F6FF\U0001F900-\U0001F9FF]', '', feedback_text)
        text_without_emoji = re.sub(r'[^\w\s]', '', text_without_emoji).strip()
        
        if not feedback_text or (not text_without_emoji and len(feedback_text.strip()) < 3):
            logger.warning(f"   ⚠️ Empty feedback text detected: feedback_text='{feedback_text}', "
                          f"text_without_emoji='{text_without_emoji}', length={len(feedback_text)}")
            await message.answer("❌ Обратная связь не может быть пустой. Пожалуйста, отправьте текст или голосовое сообщение.")
            return
        
        await self.assignment_service.add_feedback(assignment_id, feedback_text)
        
        # Send feedback to user
        user = await self.user_service.get_user(assignment.user_id)
        if user:
            feedback_message = (
                f"💬 <b>Обратная связь по вашему заданию</b>\n\n"
                f"День {assignment.day_number}\n\n"
                f"{feedback_text}"
            )
            
            # Проверяем, что сообщение не пустое перед отправкой
            if not feedback_message.strip() or len(feedback_message.strip()) < 10:
                await message.answer("❌ Ошибка: сообщение обратной связи пустое. Пожалуйста, отправьте текст.")
                return
            
            try:
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
            except Exception as e:
                logger.error(f"Error sending feedback to user {assignment.user_id}: {e}", exc_info=True)
                await message.answer(f"❌ Ошибка при отправке обратной связи: {e}")
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
        feedback_text = (message.text or message.caption or "").strip()
        
        # Проверяем, что текст не пустой (включая проверку на только эмодзи/пробелы)
        # Удаляем все эмодзи и проверяем, остался ли текст
        import re
        text_without_emoji = re.sub(r'[\U0001F300-\U0001F9FF\U0001FA00-\U0001FAFF\U00002600-\U000027BF\U0001F1E0-\U0001F1FF\U0001F680-\U0001F6FF\U0001F900-\U0001F9FF]', '', feedback_text)
        text_without_emoji = re.sub(r'[^\w\s]', '', text_without_emoji).strip()
        
        if not feedback_text or (not text_without_emoji and len(feedback_text.strip()) < 3):
            await message.answer("❌ Обратная связь не может быть пустой. Пожалуйста, отправьте текст или голосовое сообщение.")
            return
        
        await self.assignment_service.add_feedback(assignment_id, feedback_text)
        
        # Send feedback to user
        user = await self.user_service.get_user(assignment.user_id)
        if user:
            feedback_message = (
                f"💬 <b>Обратная связь по вашему заданию</b>\n\n"
                f"День {assignment.day_number}\n\n"
                f"{feedback_text}"
            )
            
            # Проверяем, что сообщение не пустое перед отправкой
            if not feedback_message.strip() or len(feedback_message.strip()) < 10:
                await message.answer("❌ Ошибка: сообщение обратной связи пустое. Пожалуйста, отправьте текст.")
                return
            
            try:
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
            except Exception as e:
                logger.error(f"Error sending feedback to user {assignment.user_id}: {e}", exc_info=True)
                await message.answer(f"❌ Ошибка при отправке обратной связи: {e}")
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
                # Fallback на старый метод, если JSON нет:
                # отделяем задание отдельным блоком и добавляем кнопку отправки ответа.
                await send_typing_action(self.bot, user.user_id, 0.6)

                header = f"📚 <b>День {lesson.day_number}: {html.escape(lesson.title or '')}</b>"
                await self._safe_send_message(user.user_id, header, parse_mode="HTML", protect_content=True)

                content_text = (lesson.content_text or "").strip()
                lesson_part, embedded_task = self._split_assignment_from_text(content_text)
                if lesson_part:
                    await self._safe_send_message(user.user_id, lesson_part, protect_content=True)

                if getattr(lesson, "video_url", None):
                    await self._safe_send_message(user.user_id, f"🎥 Видео: {lesson.video_url}", protect_content=True)

                if getattr(lesson, "image_url", None):
                    try:
                        await self.bot.send_photo(user.user_id, lesson.image_url)
                    except Exception:
                        pass

                assignment_text = (lesson.assignment_text or "").strip() or (embedded_task or "").strip()
                if assignment_text:
                    submit_kb = InlineKeyboardMarkup(inline_keyboard=[[
                        InlineKeyboardButton(
                            text=f"📝 Отправить ответ на задание №{int(lesson.day_number)}",
                            callback_data=f"assignment:submit:lesson_{int(lesson.day_number)}",
                        )
                    ]])
                    await self._safe_send_message(user.user_id, assignment_text, reply_markup=submit_kb)
            
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
        try:
            await self.db.log_user_activity(message.from_user.id, "course", "navigator", "navigation")
        except Exception:
            pass
        await self._show_navigator(message.from_user.id, message)
    
    async def handle_keyboard_ask_question(self, message: Message):
        """Handle 'Задать вопрос' button from persistent keyboard."""
        user_id = message.from_user.id
        user = await self.user_service.get_user(user_id)
        
        persistent_keyboard = self._create_persistent_keyboard()
        
        if not user or not user.has_access():
            await message.answer("❌ У вас нет доступа к этому курсу.", reply_markup=persistent_keyboard)
            return
        
        self._user_question_context[user_id] = {
            "lesson_day": user.current_day,
            "waiting_for_question": True,
        }

        await message.answer(
            f"<b>Задать вопрос</b>\n\n"
            f"Отправьте вопрос по <b>Дню {user.current_day}</b> <b>одним сообщением</b> (можно голосовым).\n\n"
            f"Сообщение уйдёт кураторам, они ответят вам прямо сюда.\n\n"
            f"💡 <i>Чтобы задать ещё вопрос — просто нажмите ❓ снова.</i>",
            reply_markup=persistent_keyboard
        )

    async def handle_keyboard_submit_assignment(self, message: Message):
        """Handle 'Отправить задание' button from persistent keyboard."""
        user_id = message.from_user.id
        user = await self.user_service.get_user(user_id)
        persistent_keyboard = self._create_persistent_keyboard()

        if not user or not user.has_access():
            await message.answer("❌ У вас нет доступа к этому курсу.", reply_markup=persistent_keyboard)
            return

        day = user.current_day
        lesson_data = self.lesson_loader.get_lesson(day)
        task = self.lesson_loader.get_task_for_tariff(day, user.tariff) if lesson_data else None
        if not task:
            await message.answer("📝 Для текущего дня нет задания.", reply_markup=persistent_keyboard)
            return

        try:
            await self.db.mark_assignment_intent(user_id, day)
        except Exception:
            pass

        lesson_title = lesson_data.get("title", f"День {day}") if lesson_data else f"День {day}"
        safe_lesson_title = html.escape(lesson_title)

        self._user_assignment_context[user_id] = {
            "lesson_day": day,
            "waiting_for_assignment": True,
        }

        await message.answer(
            f"<b>Отправить задание для {safe_lesson_title}</b>\n\n"
            "Отправьте ответ <b>одним сообщением</b>: текстом, фото, видео, документом или голосовым.\n\n"
            "<i>После отправки задание уйдёт кураторам.</i>",
            reply_markup=persistent_keyboard,
        )
    
    async def handle_keyboard_tariffs(self, message: Message):
        """Handle 'Тарифы' button from persistent keyboard - redirect to sales bot."""
        try:
            await self.db.log_user_activity(message.from_user.id, "course", "tariffs", "navigation")
        except Exception:
            pass
        # Создаем deep link в sales bot для открытия тарифов
        sales_bot_username = "StartNowQ_bot"  # Имя sales bot
        deep_link = f"https://t.me/{sales_bot_username}?start=tariffs"
        
        persistent_keyboard = self._create_persistent_keyboard()
        await message.answer(
            "<b>Тарифы курса</b>\n\n"
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
        try:
            await self.db.log_user_activity(message.from_user.id, "course", "discussion", "community")
        except Exception:
            pass
        persistent_keyboard = self._create_persistent_keyboard()
        
        # Prefer an explicit discussion URL; fallback to configured group invite links / ID heuristics.
        group_link = (Config.DISCUSSION_GROUP_URL or "").strip()
        if not group_link:
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
                "Обсудите задания и вопросы с другими участниками курса:\n\n"
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
    
    @staticmethod
    def _normalize_time_display(time_str: str) -> str:
        """
        Нормализует отображение времени в формат ЧЧ:ММ.
        
        Args:
            time_str: Время в формате "HH", "HH:MM" или "H:MM"
        
        Returns:
            Время в формате "HH:MM" (например, "08:00", "09:30")
        """
        if not time_str:
            return "08:00"
        
        time_str = time_str.strip()
        
        # Если время уже в формате ЧЧ:ММ, возвращаем как есть
        if ":" in time_str:
            try:
                parts = time_str.split(":")
                hh = parts[0].zfill(2)  # Дополняем до 2 цифр
                mm = parts[1] if len(parts) > 1 else "00"
                if len(mm) == 1:
                    mm = f"0{mm}"  # Дополняем минуты до 2 цифр
                # Проверяем валидность
                hour = int(hh)
                minute = int(mm)
                if 0 <= hour <= 23 and 0 <= minute <= 59:
                    return f"{hh}:{mm}"
            except (ValueError, IndexError):
                pass
        
        # Если только часы (например, "08" или "8")
        try:
            hour = int(time_str)
            if 0 <= hour <= 23:
                return f"{hour:02d}:00"  # Добавляем :00 для минут
        except ValueError:
            pass
        
        # Если не удалось распарсить, возвращаем значение по умолчанию
        return "08:00"
    
    async def handle_keyboard_mentor(self, message: Message):
        """Handle 'Наставник' button from persistent keyboard - show mentor menu."""
        try:
            await self.db.log_user_activity(message.from_user.id, "course", "mentor", "navigation")
        except Exception:
            pass
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
                text = "0"
            elif user.mentor_reminders == i:
                text = f"{i} ☑️"
            
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
            status_text = "Напоминания выключены"
        else:
            status_text = f"☑️ Напоминаний в день: {user.mentor_reminders}"
        
        # Получаем текущие настройки времени и нормализуем их для отображения
        lesson_time_raw = getattr(user, "lesson_delivery_time_local", None) or Config.LESSON_DELIVERY_TIME_LOCAL
        reminder_start_raw = getattr(user, "mentor_reminder_start_local", None) or Config.MENTOR_REMINDER_START_LOCAL
        reminder_end_raw = getattr(user, "mentor_reminder_end_local", None) or Config.MENTOR_REMINDER_END_LOCAL
        
        lesson_time = self._normalize_time_display(lesson_time_raw)
        reminder_start = self._normalize_time_display(reminder_start_raw)
        reminder_end = self._normalize_time_display(reminder_end_raw)
        
        # Добавляем кнопки для настройки времени
        buttons.append([InlineKeyboardButton(
            text="⏰ Время получения заданий",
            callback_data="mentor:settings:lesson_time"
        )])
        
        if user.mentor_reminders > 1:
            buttons.append([InlineKeyboardButton(
                text="🕐 Временной промежуток уведомлений",
                callback_data="mentor:settings:reminder_window"
            )])
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
        
        time_info = f"\n\n⏰ <b>Время получения заданий:</b> {lesson_time}"
        if user.mentor_reminders > 1:
            time_info += f"\n🕐 <b>Уведомления:</b> {reminder_start} - {reminder_end}"
        
        await message.answer(
            f"👨‍🏫 <b>НАСТАВНИК</b>\n\n"
            f"Текущий статус: {status_text}\n\n"
            f"Выберите частоту напоминаний:\n"
            f"• <b>0</b> — напоминания отключены\n"
            f"• <b>1-5</b> — количество напоминаний в день\n\n"
            f"Напоминания содержат задание текущего урока.{time_info}",
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
            status_text = "Ок, напоминания выключены."
        else:
            status_text = f"☑️ Ок, буду напоминать {frequency} раз(а) в день 💬\n\nНапоминание будет содержать задание текущего урока."
        
        persistent_keyboard = self._create_persistent_keyboard()
        await callback.message.answer(
            f"👨‍🏫 <b>НАСТАВНИК</b>\n\n{status_text}",
            reply_markup=persistent_keyboard
        )

        # If user just enabled reminders, send the first one immediately (best-effort).
        # The background scheduler checks periodically, so without this users may think it's broken.
        if frequency > 0:
            try:
                activity = await self.db.has_assignment_activity_for_day(user.user_id, user.current_day)
                if not activity:
                    await self._send_mentor_reminder(user)
            except Exception:
                pass
        
        logger.info(f"User {user_id} set mentor reminders frequency to {frequency}")
    
    async def handle_time_input(self, message: Message):
        """Handle time input in format HH:MM for mentor settings."""
        user_id = message.from_user.id
        text = message.text.strip()
        
        # Проверяем, ожидает ли пользователь ввода времени
        time_context = self._user_time_input_context.get(user_id)
        if not time_context:
            # Не ожидаем ввода времени, пропускаем обработку
            raise SkipHandler()
        
        # Проверяем формат времени ЧЧ:ММ
        time_pattern = re.compile(r'^([0-1]?[0-9]|2[0-3]):([0-5][0-9])$')
        if not time_pattern.match(text):
            await message.answer("❌ Неверный формат времени. Используйте формат ЧЧ:ММ (например, 09:30)")
            return
        
        user = await self.user_service.get_user(user_id)
        if not user or not user.has_access():
            del self._user_time_input_context[user_id]
            raise SkipHandler()
        
        # Парсим время
        try:
            hh, mm = text.split(":")
            hour = int(hh)
            minute = int(mm)
            if hour < 0 or hour > 23 or minute < 0 or minute > 59:
                raise ValueError("Invalid time")
        except (ValueError, IndexError):
            await message.answer("❌ Неверный формат времени. Используйте формат ЧЧ:ММ (например, 09:30)")
            return
        
        # Удаляем контекст ожидания
        del self._user_time_input_context[user_id]
        
        # Обновляем соответствующую настройку на основе контекста
        # Нормализуем время перед сохранением (всегда формат HH:MM)
        normalized_time = self._normalize_time_display(text)
        
        if time_context == "lesson":
            user.lesson_delivery_time_local = normalized_time
            await self.db.update_user(user)
            
            # Пересчитываем start_date с новым временем
            from datetime import datetime, timedelta, time, timezone
            from utils.schedule_timezone import get_schedule_timezone
            if user.start_date:
                tz = get_schedule_timezone()
                now_utc = datetime.now(timezone.utc)
                now_local = now_utc.astimezone(tz)
                tomorrow_local_date = (now_local + timedelta(days=1)).date()
                delivery_t = time(hour=hour, minute=minute)
                start_local = datetime.combine(tomorrow_local_date, delivery_t, tzinfo=tz)
                user.start_date = start_local.astimezone(timezone.utc).replace(tzinfo=None)
                await self.db.update_user(user)
            
            persistent_keyboard = self._create_persistent_keyboard()
            await message.answer(
                f"✅ Время получения заданий установлено: <b>{normalized_time}</b>\n\n"
                f"Новые задания будут приходить каждый день в это время.",
                reply_markup=persistent_keyboard
            )
            logger.info(f"User {user_id} set lesson delivery time to {normalized_time}")
        
        elif time_context == "reminder_start":
            user.mentor_reminder_start_local = normalized_time
            await self.db.update_user(user)
            await message.answer(
                f"✅ Время начала уведомлений установлено: <b>{normalized_time}</b>\n\n"
                f"Теперь установите время конца промежутка уведомлений."
            )
            logger.info(f"User {user_id} set reminder start time to {normalized_time}")
        
        elif time_context == "reminder_end":
            reminder_start = getattr(user, "mentor_reminder_start_local", None) or Config.MENTOR_REMINDER_START_LOCAL
            user.mentor_reminder_end_local = normalized_time
            await self.db.update_user(user)
            reminder_start_normalized = self._normalize_time_display(reminder_start)
            persistent_keyboard = self._create_persistent_keyboard()
            await message.answer(
                f"✅ Временной промежуток уведомлений установлен: "
                f"<b>{reminder_start_normalized} - {normalized_time}</b>\n\n"
                f"Напоминания будут равномерно распределены в течение этого промежутка.",
                reply_markup=persistent_keyboard
            )
            logger.info(f"User {user_id} set reminder window to {reminder_start_normalized} - {normalized_time}")
    
    async def handle_mentor_settings(self, callback: CallbackQuery):
        """Handle mentor settings menu (time settings)."""
        try:
            await callback.answer()
        except:
            pass
        
        user_id = callback.from_user.id
        user = await self.user_service.get_user(user_id)
        
        if not user or not user.has_access():
            await callback.message.answer("❌ У вас нет доступа.")
            return
        
        setting_type = callback.data.split(":")[-1]
        
        if setting_type == "lesson_time":
            # Настройка времени получения заданий
            await self._show_lesson_time_settings(callback.message, user)
        elif setting_type == "reminder_window":
            # Настройка временного промежутка уведомлений
            await self._show_reminder_window_settings(callback.message, user)
    
    async def _show_lesson_time_settings(self, message: Message, user: User):
        """Show lesson delivery time settings."""
        current_time_raw = getattr(user, "lesson_delivery_time_local", None) or Config.LESSON_DELIVERY_TIME_LOCAL
        current_time = self._normalize_time_display(current_time_raw)
        
        # Создаем кнопки с популярными временами
        buttons = []
        popular_times = ["06:00", "07:00", "08:00", "08:30", "09:00", "10:00", "12:00", "18:00", "20:00"]
        row = []
        for time_str in popular_times:
            text = time_str
            # Сравниваем нормализованные значения
            if self._normalize_time_display(time_str) == current_time:
                text = f"{time_str} ☑️"
            row.append(InlineKeyboardButton(
                text=text,
                callback_data=f"mentor:time:lesson:{time_str}"
            ))
            if len(row) == 3:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
        
        buttons.append([InlineKeyboardButton(
            text="✏️ Ввести своё время",
            callback_data="mentor:time:lesson:custom"
        )])
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
        
        await message.answer(
            f"⏰ <b>Время получения заданий</b>\n\n"
            f"Текущее время: <b>{current_time}</b>\n\n"
            f"Выберите время, когда вы хотите получать новые задания каждый день:",
            reply_markup=keyboard
        )
    
    async def _show_reminder_window_settings(self, message: Message, user: User):
        """Show reminder window time settings."""
        current_start_raw = getattr(user, "mentor_reminder_start_local", None) or Config.MENTOR_REMINDER_START_LOCAL
        current_end_raw = getattr(user, "mentor_reminder_end_local", None) or Config.MENTOR_REMINDER_END_LOCAL
        
        current_start = self._normalize_time_display(current_start_raw)
        current_end = self._normalize_time_display(current_end_raw)
        
        # Создаем кнопки для настройки начала и конца промежутка
        buttons = [
            [InlineKeyboardButton(
                text=f"🕐 Начало: {current_start}",
                callback_data="mentor:time:reminder_start:custom"
            )],
            [InlineKeyboardButton(
                text=f"🕐 Конец: {current_end}",
                callback_data="mentor:time:reminder_end:custom"
            )]
        ]
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
        
        await message.answer(
            f"🕐 <b>Временной промежуток уведомлений</b>\n\n"
            f"Текущий промежуток: <b>{current_start} - {current_end}</b>\n\n"
            f"Выберите, когда вы хотите получать напоминания от наставника.\n"
            f"Напоминания будут равномерно распределены в течение этого промежутка.",
            reply_markup=keyboard
        )
    
    async def handle_mentor_time_set(self, callback: CallbackQuery):
        """Handle time setting selection."""
        try:
            await callback.answer()
        except:
            pass
        
        user_id = callback.from_user.id
        user = await self.user_service.get_user(user_id)
        
        if not user or not user.has_access():
            await callback.message.answer("❌ У вас нет доступа.")
            return
        
        parts = callback.data.split(":")
        setting_type = parts[2]  # "lesson" or "reminder_start" or "reminder_end"
        
        if setting_type == "lesson":
            if parts[3] == "custom":
                # Запрашиваем ввод времени
                self._user_time_input_context[user_id] = "lesson"
                await callback.message.answer(
                    "⏰ Введите время получения заданий в формате ЧЧ:ММ (например, 09:30):\n\n"
                    "Отправьте время текстом, например: 09:30"
                )
                return
            else:
                # Установлено конкретное время - нормализуем перед сохранением
                time_str = parts[3]
                normalized_time = self._normalize_time_display(time_str)
                user.lesson_delivery_time_local = normalized_time
                await self.db.update_user(user)
                
                # Пересчитываем start_date с новым временем
                from datetime import datetime, timedelta, time, timezone
                from utils.schedule_timezone import get_schedule_timezone
                if user.start_date:
                    tz = get_schedule_timezone()
                    now_utc = datetime.now(timezone.utc)
                    now_local = now_utc.astimezone(tz)
                    tomorrow_local_date = (now_local + timedelta(days=1)).date()
                    try:
                        # Используем нормализованное время для парсинга
                        normalized_time_str = self._normalize_time_display(time_str)
                        hh, mm = normalized_time_str.split(":", 1)
                        delivery_t = time(hour=int(hh), minute=int(mm))
                    except Exception:
                        delivery_t = time(8, 30)
                    start_local = datetime.combine(tomorrow_local_date, delivery_t, tzinfo=tz)
                    user.start_date = start_local.astimezone(timezone.utc).replace(tzinfo=None)
                    await self.db.update_user(user)
                
                persistent_keyboard = self._create_persistent_keyboard()
                await callback.message.answer(
                    f"✅ Время получения заданий установлено: <b>{normalized_time}</b>\n\n"
                    f"Новые задания будут приходить каждый день в это время.",
                    reply_markup=persistent_keyboard
                )
                logger.info(f"User {user_id} set lesson delivery time to {normalized_time}")
        
        elif setting_type == "reminder_start":
            self._user_time_input_context[user_id] = "reminder_start"
            await callback.message.answer(
                "🕐 Введите время начала промежутка уведомлений в формате ЧЧ:ММ (например, 09:30):\n\n"
                "Отправьте время текстом, например: 09:30"
            )
        elif setting_type == "reminder_end":
            self._user_time_input_context[user_id] = "reminder_end"
            await callback.message.answer(
                "🕐 Введите время конца промежутка уведомлений в формате ЧЧ:ММ (например, 22:00):\n\n"
                "Отправьте время текстом, например: 22:00"
            )
    
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
            
            # Генерируем текст напоминания с учетом данных тестирования
            # Используем данные тестирования для персонализации тона и стиля
            reminder_text = get_mentor_reminder_text(
                task,
                user_temperature=getattr(user, "mentor_temperature", None),
                user_charisma=getattr(user, "mentor_charisma", None)
            )
            
            # Отправляем напоминание
            await self.bot.send_message(user.user_id, reminder_text)
            
            # Обновляем время последнего напоминания
            from datetime import datetime
            user.last_mentor_reminder = datetime.utcnow()
            await self.db.update_user(user)
            
            logger.info(f"   ✅ Mentor reminder sent to user {user.user_id} (day {user.current_day})")
            
        except Exception as e:
            logger.error(f"   ❌ Error sending mentor reminder to user {user.user_id}: {e}", exc_info=True)
    
    async def _update_pup_questions_pinned_message(self, pup_chat_id: int):
        """Update or create pinned message with questions button in PUP."""
        try:
            # Получаем статистику вопросов
            stats = await self.question_service.get_questions_stats()
            
            # Формируем текст сообщения
            message_text = (
                f"📋 <b>Вопросы</b>\n\n"
                f"📊 Всего вопросов: {stats['total']}\n"
                f"⏳ Неотвеченных: {stats['unanswered']}\n"
                f"✅ Отвеченных: {stats['answered']}\n\n"
                f"Нажмите кнопку ниже, чтобы открыть список вопросов 👇"
            )
            
            # Создаем клавиатуру
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=f"📋 Вопросы ({stats['unanswered']})",
                        callback_data="questions:list"
                    )
                ]
            ])
            
            # Проверяем, есть ли уже закрепленное сообщение
            await self.db._ensure_connection()
            pinned_msg_id = await self.db.get_setting("pup_questions_pinned_message_id")
            
            if pinned_msg_id:
                try:
                    # Обновляем существующее сообщение
                    await self.bot.edit_message_text(
                        message_text,
                        chat_id=pup_chat_id,
                        message_id=int(pinned_msg_id),
                        reply_markup=keyboard
                    )
                    return
                except Exception as e:
                    logger.warning(f"Failed to edit pinned message: {e}, creating new one")
            
            # Создаем новое сообщение
            sent_message = await self.bot.send_message(
                pup_chat_id,
                message_text,
                reply_markup=keyboard
            )
            
            # Сохраняем message_id
            await self.db.set_setting("pup_questions_pinned_message_id", str(sent_message.message_id))
            
            # Закрепляем сообщение
            try:
                await self.bot.pin_chat_message(pup_chat_id, sent_message.message_id)
            except Exception as e:
                logger.warning(f"Failed to pin message (may need admin rights): {e}")
                
        except Exception as e:
            logger.error(f"Error updating PUP questions pinned message: {e}", exc_info=True)
    
    async def handle_questions_list(self, callback: CallbackQuery):
        """Handle questions list button click."""
        try:
            await callback.answer()
        except:
            pass
        
        try:
            # Получаем неотвеченные вопросы
            unanswered = await self.question_service.get_unanswered_questions(limit=20)
            all_questions = await self.question_service.get_all_questions(limit=20)
            stats = await self.question_service.get_questions_stats()
            
            if not all_questions:
                await callback.message.answer("📋 Пока нет вопросов.")
                return
            
            # Формируем список вопросов
            message_text = (
                f"📋 <b>Список вопросов</b>\n\n"
                f"📊 Всего: {stats['total']} | ⏳ Неотвеченных: {stats['unanswered']} | ✅ Отвеченных: {stats['answered']}\n\n"
            )
            
            # Добавляем неотвеченные вопросы первыми
            if unanswered:
                message_text += "<b>⏳ Неотвеченные:</b>\n"
                for q in unanswered[:10]:
                    formatted = await self.question_service.format_question_for_list(q)
                    message_text += f"{formatted}\n\n"
            
            # Добавляем остальные вопросы
            answered = [q for q in all_questions if q.get("answered_at")]
            if answered and len(unanswered) < 10:
                message_text += "<b>✅ Отвеченные:</b>\n"
                for q in answered[:10 - len(unanswered)]:
                    formatted = await self.question_service.format_question_for_list(q)
                    message_text += f"{formatted}\n\n"
            
            # Создаем клавиатуру с вопросами
            keyboard_rows = []
            for q in all_questions[:10]:
                question_id = q.get("question_id")
                status = "✅" if q.get("answered_at") else "⏳"
                keyboard_rows.append([
                    InlineKeyboardButton(
                        text=f"{status} #{question_id}",
                        callback_data=f"question:view:{question_id}"
                    )
                ])
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_rows)
            
            await callback.message.answer(message_text, reply_markup=keyboard)
        except Exception as e:
            logger.error(f"Error in handle_questions_list: {e}", exc_info=True)
            try:
                await callback.message.answer("❌ Ошибка при загрузке списка вопросов.")
            except:
                pass
    
    async def handle_question_view(self, callback: CallbackQuery):
        """Handle question view button click."""
        try:
            await callback.answer()
        except:
            pass
        
        try:
            question_id = int(callback.data.split(":")[-1])
            question = await self.question_service.get_question(question_id)
            
            if not question:
                await callback.message.answer("❌ Вопрос не найден.")
                return
            
            # Форматируем вопрос для просмотра
            user_name = html.escape(str(question.get("first_name") or "Unknown"))
            if question.get("last_name"):
                user_name += f" {html.escape(str(question['last_name']))}"
            username = question.get("username")
            if username:
                user_name += f" (@{html.escape(str(username))})"
            
            message_text = f"❓ <b>Вопрос #{question_id}</b>\n\n"
            message_text += f"👤 Пользователь: {user_name}\n"
            message_text += f"🆔 ID: {question['user_id']}\n"
            
            if question.get("day_number"):
                message_text += f"📚 Урок: День {question['day_number']}\n"
            
            message_text += f"🕐 Создан: {question['created_at'][:19] if question.get('created_at') else '?'}\n"
            
            if question.get("answered_at"):
                message_text += f"✅ Отвечен: {question['answered_at'][:19]}\n"
            
            message_text += "\n💭 <b>Вопрос:</b>\n"
            if question.get("question_text"):
                message_text += html.escape(str(question["question_text"]))
            elif question.get("question_voice_file_id"):
                message_text += "🎤 Голосовое сообщение"
            
            if question.get("answer_text"):
                message_text += f"\n\n💬 <b>Ответ:</b>\n{html.escape(str(question['answer_text']))}"
            
            # Создаем клавиатуру
            keyboard_rows = []
            if not question.get("answered_at"):
                keyboard_rows.append([
                    InlineKeyboardButton(
                        text="💬 Ответить",
                        callback_data=f"question:answer:{question_id}"
                    )
                ])
            keyboard_rows.append([
                InlineKeyboardButton(
                    text="◀️ Назад к списку",
                    callback_data="questions:list"
                )
            ])
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_rows)
            
            # Если есть голосовое сообщение, отправляем его отдельно
            if question.get("question_voice_file_id"):
                try:
                    await callback.message.answer_voice(
                        question["question_voice_file_id"],
                        caption=message_text,
                        reply_markup=keyboard
                    )
                except:
                    await callback.message.answer(message_text, reply_markup=keyboard)
            else:
                await callback.message.answer(message_text, reply_markup=keyboard)
                
        except Exception as e:
            logger.error(f"Error in handle_question_view: {e}", exc_info=True)
            try:
                await callback.message.answer("❌ Ошибка при загрузке вопроса.")
            except:
                pass
    
    async def handle_question_answer(self, callback: CallbackQuery):
        """Handle question answer button click."""
        try:
            await callback.answer()
        except:
            pass
        
        try:
            question_id = int(callback.data.split(":")[-1])
            question = await self.question_service.get_question(question_id)
            
            if not question:
                await callback.message.answer("❌ Вопрос не найден.")
                return
            
            if question.get("answered_at"):
                await callback.message.answer("✅ Этот вопрос уже отвечен.")
                return
            
            await callback.message.answer(
                f"💬 <b>Ответ на вопрос #{question_id}</b>\n\n"
                f"👤 Пользователь ID: {question['user_id']}\n"
                f"📚 Урок: День {question.get('day_number', '?')}\n\n"
                f"✍️ Ответьте на это сообщение с вашим ответом пользователю.\n\n"
                f"💡 Ответ будет отправлен анонимно от имени бота."
            )
        except Exception as e:
            logger.error(f"Error in handle_question_answer: {e}", exc_info=True)
            try:
                await callback.message.answer("❌ Ошибка при обработке запроса.")
            except:
                pass
    
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
        
        # Инициализируем закрепленное сообщение с кнопкой "Вопросы" в ПУП
        if Config.PREMIUM_GROUP_ID:
            try:
                def parse_chat_id(raw: str) -> int:
                    s = (raw or "").strip()
                    if not s:
                        return 0
                    if s.startswith("#-") and s[2:].isdigit():
                        return int(f"-100{s[2:]}")
                    try:
                        return int(s)
                    except Exception:
                        return 0
                
                pup_chat_id = parse_chat_id(Config.PREMIUM_GROUP_ID)
                if pup_chat_id != 0:
                    # Небольшая задержка, чтобы бот успел подключиться
                    await asyncio.sleep(2)
                    await self._update_pup_questions_pinned_message(pup_chat_id)
                    logger.info("✅ PUP questions pinned message initialized")
            except Exception as e:
                logger.warning(f"Failed to initialize PUP questions pinned message: {e}")
        
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
