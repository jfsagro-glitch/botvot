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
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup
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
from utils.telegram_helpers import create_lesson_keyboard, format_lesson_message
from utils.scheduler import LessonScheduler
from utils.premium_ui import send_typing_action, create_premium_separator

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
        
        # Register handlers
        self._register_handlers()
    
    def _register_handlers(self):
        """Register all bot handlers."""
        self.dp.message.register(self.handle_start, CommandStart())
        self.dp.message.register(self.handle_current_lesson, Command("lesson"))
        self.dp.message.register(self.handle_progress, Command("progress"))
        self.dp.callback_query.register(self.handle_submit_assignment, F.data.startswith("assignment:submit:"))
        self.dp.callback_query.register(self.handle_ask_question, F.data.startswith("question:ask:"))
        self.dp.callback_query.register(self.handle_admin_reply, F.data.startswith("admin_reply:"))
        self.dp.message.register(self.handle_assignment_text, F.text)
        self.dp.message.register(self.handle_assignment_media, F.photo | F.video | F.document)
        self.dp.message.register(self.handle_question_text, F.text & ~F.command)
        self.dp.message.register(self.handle_admin_feedback, F.chat.id == Config.ADMIN_CHAT_ID, F.reply_to_message)
    
    async def handle_start(self, message: Message):
        """Handle /start command - check access and show current lesson."""
        user_id = message.from_user.id
        user = await self.user_service.get_user(user_id)
        
        if not user:
            await message.answer(
                "❌ You don't have access to this course.\n\n"
                "Please purchase access through our sales bot first."
            )
            return
        
        if not user.has_access():
            await message.answer(
                "❌ You don't have active course access.\n\n"
                "Please purchase access through our sales bot first."
            )
            return
        
        # Show welcome and current lesson
        await message.answer(
            f"👋 Добро пожаловать в курс, {user.first_name}!\n\n"
            f"📅 День {user.current_day} из {Config.COURSE_DURATION_DAYS}\n"
            f"📚 Тариф: {user.tariff.value.upper()}\n\n"
            f"Используйте /lesson для просмотра текущего урока."
        )
        
        # Send current lesson if available
        await self._send_current_lesson(user_id)
    
    async def handle_current_lesson(self, message: Message):
        """Handle /lesson command - show current lesson."""
        user_id = message.from_user.id
        await self._send_current_lesson(user_id)
    
    async def _send_current_lesson(self, user_id: int):
        """Send current lesson to user from JSON."""
        user = await self.user_service.get_user(user_id)
        if not user or not user.has_access():
            return
        
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
        lesson_data = self.lesson_loader.get_lesson(user.current_day)
        
        if not lesson_data:
            await self.bot.send_message(
                user_id,
                f"⏳ Урок для дня {user.current_day} пока не готов.\n"
                f"Он будет отправлен автоматически, когда наступит время."
            )
            return
        
        # Проверяем день тишины
        if self.lesson_loader.is_silent_day(user.current_day):
            logger.info(f"Day {user.current_day} is silent day for user {user_id}")
            return
        
        # Отправляем урок с анимацией
        await send_typing_action(self.bot, user_id, 0.8)
        await self._send_lesson_from_json(user, lesson_data)
    
    async def handle_progress(self, message: Message):
        """Handle /progress command - show user progress."""
        user_id = message.from_user.id
        user = await self.user_service.get_user(user_id)
        
        if not user or not user.has_access():
            await message.answer("❌ You don't have access to this course.")
            return
        
        progress_percent = (user.current_day / Config.COURSE_DURATION_DAYS) * 100
        
        await message.answer(
            f"📊 <b>Your Progress</b>\n\n"
            f"Current Day: <b>{user.current_day}/{Config.COURSE_DURATION_DAYS}</b>\n"
            f"Progress: <b>{progress_percent:.1f}%</b>\n"
            f"Tariff: <b>{user.tariff.value.upper()}</b>\n"
            f"Started: {user.start_date.strftime('%Y-%m-%d') if user.start_date else 'N/A'}"
        )
    
    async def handle_submit_assignment(self, callback: CallbackQuery):
        """Handle assignment submission button click."""
        await callback.answer()
        
        user_id = callback.from_user.id
        user = await self.user_service.get_user(user_id)
        
        if not user or not user.has_access():
            await callback.message.answer("❌ You don't have access to this course.")
            return
        
        lesson_id = int(callback.data.split(":")[2])
        lesson = await self.lesson_service.get_lesson_for_day(user.current_day)
        
        if not lesson or lesson.lesson_id != lesson_id:
            await callback.message.answer("❌ Lesson not found.")
            return
        
        # Check if user can submit assignments (BASIC tariff cannot)
        if not user.can_receive_feedback():
            await callback.message.answer(
                "ℹ️ <b>Обратная связь не включена</b>\n\n"
                "В вашем текущем тарифе (BASIC) задания не проверяются.\n\n"
                "Вы можете выполнять задания для себя, "
                "но они не будут проверяться нашей командой.\n\n"
                "Для получения обратной связи обновитесь до тарифа FEEDBACK или PREMIUM.\n\n"
                "💬 Но вы можете обсудить задания в общем пространстве участников 👇"
            )
            return
        
        await callback.message.answer(
            f"📝 <b>Отправить задание для Дня {lesson.day_number}</b>\n\n"
            f"Отправьте ваше задание текстом, фото, видео или документом.\n\n"
            f"<i>Можно отправить несколько сообщений. Напишите 'готово', когда закончите.</i>"
        )
    
    async def handle_ask_question(self, callback: CallbackQuery):
        """Handle question button click."""
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
        
        lesson = await self.lesson_service.get_lesson_for_day(user.current_day)
        
        await callback.message.answer(
            f"❓ <b>Задать вопрос</b>\n\n"
            f"Отправьте ваш вопрос по уроку <b>День {day_from_callback}</b>.\n\n"
            f"Наша команда ответит как можно скорее.\n\n"
            f"💡 <i>Совет: Чем конкретнее вопрос, тем быстрее вы получите ответ!</i>"
        )
        
        # Сохраняем контекст вопроса для дальнейшей обработки
        # В production можно сохранить в БД, что пользователь задаёт вопрос по конкретному уроку
    
    async def _send_lesson_from_json(self, user: User, lesson_data: dict):
        """
        Отправляет урок из JSON структуры пользователю.
        
        Args:
            user: Пользователь
            lesson_data: Данные урока из JSON
        """
        try:
            day = user.current_day
            title = lesson_data.get("title", f"День {day}")
            text = lesson_data.get("text", "")
            
            # Получаем задание в зависимости от тарифа
            task = self.lesson_loader.get_task_for_tariff(day, user.tariff)
            
            # Формируем сообщение урока
            lesson_message = (
                f"{create_premium_separator()}\n"
                f"📚 <b>{title}</b>\n"
                f"{create_premium_separator()}\n\n"
                f"{text}\n\n"
            )
            
            # Добавляем задание, если есть
            if task:
                lesson_message += (
                    f"{create_premium_separator()}\n\n"
                    f"📝 <b>Задание:</b>\n"
                    f"{task}\n\n"
                )
            
            # Отправляем текст урока
            keyboard = create_lesson_keyboard_from_json(lesson_data, user, Config.GENERAL_GROUP_ID)
            await self.bot.send_message(user.user_id, lesson_message, reply_markup=keyboard)
            
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
            
            logger.info(f"✅ Урок {day} отправлен пользователю {user.user_id}")
            
        except Exception as e:
            logger.error(f"❌ Ошибка при отправке урока пользователю {user.user_id}: {e}", exc_info=True)
    
    async def handle_assignment_text(self, message: Message):
        """Handle assignment text submission."""
        user_id = message.from_user.id
        user = await self.user_service.get_user(user_id)
        
        if not user or not user.has_access():
            return
        
        # Check if this is assignment submission context
        # In production, you might want to track submission state
        lesson = await self.lesson_service.get_user_current_lesson(user)
        if not lesson or not lesson.has_assignment():
            return
        
        # Check if user can receive feedback
        if not user.can_receive_feedback():
            await message.answer(
                "ℹ️ Your assignment has been noted, but feedback is not included "
                "in your current tariff."
            )
            return
        
        # Submit assignment
        assignment = await self.assignment_service.submit_assignment(
            user=user,
            lesson=lesson,
            submission_text=message.text
        )
        
        # Forward to admin
        admin_text = (
            f"📝 <b>New Assignment Submission</b>\n\n"
            f"User: {user.first_name} (@{user.username or 'N/A'})\n"
            f"User ID: {user.user_id}\n"
            f"Lesson: Day {lesson.day_number} - {lesson.title}\n"
            f"Assignment ID: {assignment.assignment_id}\n\n"
            f"<b>Submission:</b>\n{message.text}"
        )
        
        await self.bot.send_message(
            Config.ADMIN_CHAT_ID,
            admin_text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=f"💬 Reply to User",
                        callback_data=f"admin_reply:{assignment.assignment_id}"
                    )
                ]
            ])
        )
        
        await message.answer(
            "✅ <b>Assignment Submitted!</b>\n\n"
            "Your assignment has been sent to our team for review.\n"
            "You'll receive feedback soon."
        )
    
    async def handle_assignment_media(self, message: Message):
        """Handle assignment media submission (photos, videos, documents)."""
        user_id = message.from_user.id
        user = await self.user_service.get_user(user_id)
        
        if not user or not user.has_access():
            return
        
        lesson = await self.lesson_service.get_user_current_lesson(user)
        if not lesson or not lesson.has_assignment():
            return
        
        if not user.can_receive_feedback():
            await message.answer(
                "ℹ️ Your media has been noted, but feedback is not included "
                "in your current tariff."
            )
            return
        
        # Collect media file IDs
        media_ids = []
        if message.photo:
            media_ids.append(f"photo:{message.photo[-1].file_id}")
        elif message.video:
            media_ids.append(f"video:{message.video.file_id}")
        elif message.document:
            media_ids.append(f"document:{message.document.file_id}")
        
        # Submit assignment
        assignment = await self.assignment_service.submit_assignment(
            user=user,
            lesson=lesson,
            submission_text=message.caption,
            submission_media_ids=media_ids
        )
        
        # Forward to admin
        admin_text = (
            f"📝 <b>New Assignment Submission (Media)</b>\n\n"
            f"User: {user.first_name} (@{user.username or 'N/A'})\n"
            f"User ID: {user.user_id}\n"
            f"Lesson: Day {lesson.day_number} - {lesson.title}\n"
            f"Assignment ID: {assignment.assignment_id}"
        )
        
        if message.caption:
            admin_text += f"\n\n<b>Caption:</b>\n{message.caption}"
        
        # Forward media to admin
        if message.photo:
            await self.bot.send_photo(Config.ADMIN_CHAT_ID, message.photo[-1].file_id, caption=admin_text)
        elif message.video:
            await self.bot.send_video(Config.ADMIN_CHAT_ID, message.video.file_id, caption=admin_text)
        elif message.document:
            await self.bot.send_document(Config.ADMIN_CHAT_ID, message.document.file_id, caption=admin_text)
        
        await message.answer(
            "✅ <b>Задание отправлено!</b>\n\n"
            "Ваше задание отправлено нашей команде на проверку.\n"
            "Вы получите обратную связь в ближайшее время."
        )
    
    async def handle_question_text(self, message: Message):
        """Handle question text submission."""
        user_id = message.from_user.id
        user = await self.user_service.get_user(user_id)
        
        if not user or not user.has_access():
            return
        
        # Проверяем, не является ли это заданием
        lesson = await self.lesson_service.get_user_current_lesson(user)
        if lesson and lesson.has_assignment():
            # Если есть задание, это может быть задание, а не вопрос
            # Пропускаем обработку вопросов для заданий
            return
        
        # Обрабатываем как вопрос
        question_data = await self.question_service.create_question(
            user_id=user_id,
            lesson_id=lesson.lesson_id if lesson else None,
            question_text=message.text,
            context=f"День {user.current_day}" if user.current_day else None
        )
        
        # Форматируем вопрос для админа
        admin_message = await self.question_service.format_question_for_admin(question_data)
        
        # Отправляем админу
        await self.bot.send_message(
            Config.ADMIN_CHAT_ID,
            admin_message,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="💬 Ответить пользователю",
                        callback_data=f"admin_reply_question:{user_id}"
                    )
                ]
            ])
        )
        
        await message.answer(
            "✅ <b>Вопрос отправлен!</b>\n\n"
            "Ваш вопрос отправлен нашей команде.\n"
            "Мы ответим вам как можно скорее."
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
    
    async def handle_admin_feedback(self, message: Message):
        """Handle admin feedback reply to assignment."""
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
            await message.answer("❌ Could not find assignment ID. Please reply to the assignment message.")
            return
        
        assignment = await self.assignment_service.get_assignment(assignment_id)
        if not assignment:
            await message.answer("❌ Assignment not found.")
            return
        
        # Add feedback
        feedback_text = message.text or message.caption or ""
        await self.assignment_service.add_feedback(assignment_id, feedback_text)
        
        # Send feedback to user
        user = await self.user_service.get_user(assignment.user_id)
        if user:
            feedback_message = (
                f"💬 <b>Feedback on Your Assignment</b>\n\n"
                f"Day {assignment.day_number} Assignment\n\n"
                f"{feedback_text}"
            )
            
            await self.bot.send_message(user.user_id, feedback_message)
            await self.assignment_service.mark_feedback_sent(assignment_id)
            
            await message.answer("✅ Feedback sent to user.")
        else:
            await message.answer("❌ User not found.")
    
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
                await self._send_lesson_from_json(user, lesson_data)
            else:
                # Fallback на старый метод, если JSON нет
                lesson_text = format_lesson_message(lesson)
                keyboard = create_lesson_keyboard(lesson, Config.GENERAL_GROUP_ID)
                
                # Send lesson text
                await self.bot.send_message(user.user_id, lesson_text, reply_markup=keyboard)
                
                # Send image if available
                if lesson.image_url:
                    await self.bot.send_photo(user.user_id, lesson.image_url)
            
            logger.info(f"✅ Урок {user.current_day} отправлен пользователю {user.user_id}")
            
        except Exception as e:
            logger.error(f"❌ Ошибка при отправке урока пользователю {user.user_id}: {e}", exc_info=True)
    
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
        logger.error("Invalid configuration. Please check your .env file.")
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

