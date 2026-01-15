"""
Admin Bot - "Пункт управления полетами"

Centralized admin interface for:
- Receiving questions from sales and course bots
- Receiving assignment submissions from course bot
- Replying to users
- Administrative functions (statistics, users, settings, sync_content)
"""

import asyncio
import logging
from datetime import datetime
from typing import Optional
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton
)
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

from core.config import Config
from core.database import Database
from core.models import User, Assignment
from services.user_service import UserService
from services.assignment_service import AssignmentService
from services.question_service import QuestionService
from services.drive_content_sync import DriveContentSync

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class AdminBot:
    """Admin Bot - Flight Control Center implementation."""
    
    def __init__(self):
        if not Config.ADMIN_BOT_TOKEN:
            raise ValueError("ADMIN_BOT_TOKEN not configured")
        
        self.bot = Bot(
            token=Config.ADMIN_BOT_TOKEN,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML)
        )
        self.dp = Dispatcher()
        self.db = Database()
        
        self.user_service = UserService(self.db)
        self.assignment_service = AssignmentService(self.db)
        self.question_service = QuestionService(self.db)
        
        # Drive content sync (optional)
        try:
            self.drive_sync = DriveContentSync()
        except Exception as e:
            logger.warning(f"Drive sync not available: {e}")
            self.drive_sync = None
        
        # Track pending replies: {message_id: {"user_id": int, "bot_type": "sales"|"course", "context": str}}
        self._pending_replies: dict[int, dict] = {}
        
        # Register handlers
        self._register_handlers()
    
    def _register_handlers(self):
        """Register all bot handlers."""
        # Commands
        self.dp.message.register(self.handle_start, Command("start"))
        self.dp.message.register(self.handle_help, Command("help"))
        self.dp.message.register(self.handle_stats, Command("stats"))
        self.dp.message.register(self.handle_users, Command("users"))
        self.dp.message.register(self.handle_settings, Command("settings"))
        self.dp.message.register(self.handle_sync_content, Command("sync_content"))
        
        # Reply handlers (for answering questions/assignments)
        self.dp.message.register(self.handle_reply, F.reply_to_message)
        
        # Handle messages from other bots (questions/assignments forwarded to admin chat)
        # These messages come from sales/course bots to ADMIN_CHAT_ID
        if Config.ADMIN_CHAT_ID:
            self.dp.message.register(
                self.handle_forwarded_message,
                F.chat.id == Config.ADMIN_CHAT_ID,
                ~F.reply_to_message  # Not a reply, but a new forwarded message
            )
        
        # Callback handlers
        self.dp.callback_query.register(self.handle_reply_button, F.data.startswith("admin_reply:"))
        self.dp.callback_query.register(self.handle_assignment_reply_callback, F.data.startswith("reply_assignment:"))
        self.dp.callback_query.register(self.handle_question_reply_callback, F.data.startswith("reply_question:"))
        self.dp.callback_query.register(self.handle_all_user_stats, F.data == "admin:all_user_stats")
        self.dp.callback_query.register(self.handle_user_stats_detail, F.data.startswith("admin:user_stats:"))
        
        # Commands for user stats
        self.dp.message.register(self.handle_user_stats, Command("user_stats"))
        
        # Persistent keyboard buttons
        self.dp.message.register(self.handle_stats_button, F.text == "📊 Статистика")
        self.dp.message.register(self.handle_users_button, F.text == "👥 Пользователи")
        self.dp.message.register(self.handle_settings_button, F.text == "⚙️ Настройки")
        self.dp.message.register(self.handle_sync_button, F.text == "🔄 Обновить контент")
    
    async def handle_start(self, message: Message):
        """Handle /start command - show admin menu."""
        keyboard = self._create_admin_keyboard()
        await message.answer(
            "🚀 <b>Пункт управления полетами</b>\n\n"
            "Добро пожаловать в админ-панель курса.\n\n"
            "Используйте команды или кнопки ниже для управления системой.",
            reply_markup=keyboard
        )
    
    async def handle_help(self, message: Message):
        """Handle /help command."""
        help_text = (
            "📚 <b>Справка по командам</b>\n\n"
            "/start - Главное меню\n"
            "/stats - Статистика системы\n"
            "/users - Список пользователей\n"
            "/settings - Настройки ботов\n"
            "/sync_content - Обновить контент из Google Drive\n\n"
            "💬 <b>Ответы на вопросы/задания:</b>\n"
            "Ответьте на сообщение с вопросом или заданием, чтобы отправить ответ пользователю."
        )
        await message.answer(help_text)
    
    def _create_admin_keyboard(self) -> ReplyKeyboardMarkup:
        """Create persistent admin keyboard."""
        keyboard = ReplyKeyboardMarkup(
            keyboard=[
                [
                    KeyboardButton(text="📊 Статистика"),
                    KeyboardButton(text="👥 Пользователи")
                ],
                [
                    KeyboardButton(text="⚙️ Настройки"),
                    KeyboardButton(text="🔄 Обновить контент")
                ]
            ],
            resize_keyboard=True,
            is_persistent=True
        )
        return keyboard
    
    async def handle_stats(self, message: Message):
        """Handle /stats command - show system statistics and per-user details."""
        try:
            await self.db.connect()
            
            # Get user statistics
            total_users = await self._get_total_users()
            active_users = await self._get_active_users()
            users_with_access = await self._get_users_with_access()
            
            # Get assignment statistics
            total_assignments = await self._get_total_assignments()
            pending_assignments = await self._get_pending_assignments()
            
            stats_text = (
                "📊 <b>Статистика системы</b>\n\n"
                f"👥 <b>Пользователи:</b>\n"
                f"• Всего: {total_users}/200\n"
                f"• Активных: {active_users}\n"
                f"• С доступом: {users_with_access}\n\n"
                f"📝 <b>Задания:</b>\n"
                f"• Всего отправлено: {total_assignments}\n"
                f"• Ожидают проверки: {pending_assignments}\n\n"
                f"💡 <b>Для детальной статистики по пользователю:</b>\n"
                f"Используйте /user_stats USER_ID"
            )
            
            # Add keyboard with button to get all users stats
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="📋 Статистика всех пользователей",
                        callback_data="admin:all_user_stats"
                    )
                ]
            ])
            
            await message.answer(stats_text, reply_markup=keyboard)
        except Exception as e:
            logger.error(f"Error getting stats: {e}", exc_info=True)
            await message.answer("❌ Ошибка при получении статистики.")
    
    async def handle_users(self, message: Message):
        """Handle /users command - show user list with stats buttons."""
        try:
            await self.db.connect()
            users = await self._get_recent_users(limit=200)  # Show all users (max 200)
            
            if not users:
                await message.answer("👥 Пользователи не найдены.")
                return
            
            text = f"👥 <b>Пользователи</b> (всего: {len(users)}/200):\n\n"
            
            # Show first 20 users with inline buttons for stats
            keyboard_buttons = []
            for i, user in enumerate(users[:20]):  # Telegram inline keyboard limit
                tariff = user.tariff.value.upper() if user.tariff else "Нет"
                text += (
                    f"• {user.first_name or 'Без имени'}"
                    f"{f' (@{user.username})' if user.username else ''}\n"
                    f"  ID: {user.user_id} | Тариф: {tariff} | День: {user.current_day}\n\n"
                )
                keyboard_buttons.append([
                    InlineKeyboardButton(
                        text=f"📊 {user.first_name or user.user_id}",
                        callback_data=f"admin:user_stats:{user.user_id}"
                    )
                ])
            
            if len(users) > 20:
                text += f"\n... и еще {len(users) - 20} пользователей. Используйте /user_stats USER_ID для просмотра статистики."
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons) if keyboard_buttons else None
            await message.answer(text, reply_markup=keyboard)
        except Exception as e:
            logger.error(f"Error getting users: {e}", exc_info=True)
            await message.answer("❌ Ошибка при получении списка пользователей.")
    
    async def handle_settings(self, message: Message):
        """Handle /settings command - show bot settings."""
        settings_text = (
            "⚙️ <b>Настройки ботов</b>\n\n"
            f"📱 <b>Токены ботов:</b>\n"
            f"• Sales Bot: {'✅ Настроен' if Config.SALES_BOT_TOKEN else '❌ Не настроен'}\n"
            f"• Course Bot: {'✅ Настроен' if Config.COURSE_BOT_TOKEN else '❌ Не настроен'}\n"
            f"• Admin Bot: {'✅ Настроен' if Config.ADMIN_BOT_TOKEN else '❌ Не настроен'}\n\n"
            f"💾 <b>База данных:</b>\n"
            f"• Путь: {Config.DATABASE_PATH}\n\n"
            f"📁 <b>Google Drive:</b>\n"
            f"• Синхронизация: {'✅ Включена' if self.drive_sync and self.drive_sync._admin_ready() else '❌ Отключена'}\n"
        )
        await message.answer(settings_text)
    
    async def handle_sync_content(self, message: Message):
        """Handle /sync_content command - sync content from Google Drive."""
        if not self.drive_sync or not self.drive_sync._admin_ready():
            await message.answer("❌ Синхронизация с Google Drive не настроена.")
            return
        
        await message.answer("🔄 Начинаю синхронизацию контента из Google Drive...")
        
        try:
            # sync_now is synchronous, run in executor to avoid blocking
            import asyncio
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, self.drive_sync.sync_now)
            
            # result is SyncResult dataclass
            await message.answer(
                f"✅ <b>Синхронизация завершена</b>\n\n"
                f"• Обновлено дней: {result.days_synced}\n"
                f"• Медиа файлов загружено: {result.media_files_downloaded}\n"
                f"• Путь к урокам: {result.lessons_path}\n"
            )
        except Exception as e:
            logger.error(f"Error syncing content: {e}", exc_info=True)
            await message.answer(f"❌ Ошибка при синхронизации: {e}")
    
    async def handle_reply(self, message: Message):
        """Handle reply to question/assignment message."""
        if not message.reply_to_message:
            return
        
        reply_text = message.reply_to_message.text or message.reply_to_message.caption or ""
        answer_text = message.text or message.caption or ""
        
        if not answer_text:
            await message.answer("❌ Ответ не может быть пустым.")
            return
        
        # Check if this is a question or assignment
        is_question = "❓" in reply_text or "Новый вопрос" in reply_text or "Вопрос:" in reply_text
        is_assignment = "📝" in reply_text or "Задание" in reply_text or "Assignment ID:" in reply_text
        
        if is_question:
            await self._handle_question_reply(message, reply_text, answer_text)
        elif is_assignment:
            await self._handle_assignment_reply(message, reply_text, answer_text)
        else:
            await message.answer("❌ Не удалось определить тип сообщения. Ответьте на вопрос или задание.")
    
    async def _handle_question_reply(self, message: Message, reply_text: str, answer_text: str):
        """Handle reply to question."""
        # Extract user_id from message
        user_id = None
        lesson_day = None
        bot_type = "course"  # default
        
        # Try to extract from formatted message
        if "🆔 ID:" in reply_text:
            try:
                parts = reply_text.split("🆔 ID:")
                if len(parts) > 1:
                    user_id_str = parts[1].split("\n")[0].strip()
                    user_id = int(user_id_str)
            except (ValueError, IndexError):
                pass
        
        if "👤 Пользователь ID:" in reply_text:
            try:
                parts = reply_text.split("👤 Пользователь ID:")
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
        
        # Check bot type
        if "sales bot" in reply_text.lower() or "продающего бота" in reply_text.lower():
            bot_type = "sales"
        
        if not user_id:
            await message.answer("❌ Не удалось найти ID пользователя.")
            return
        
        # Send answer to user
        try:
            await self._send_answer_to_user(user_id, answer_text, lesson_day, bot_type)
            await message.answer("✅ Ответ отправлен пользователю.")
        except Exception as e:
            logger.error(f"Error sending answer to user: {e}", exc_info=True)
            await message.answer(f"❌ Ошибка при отправке ответа: {e}")
    
    async def _handle_assignment_reply(self, message: Message, reply_text: str, answer_text: str):
        """Handle reply to assignment."""
        # Extract assignment_id
        assignment_id = None
        if "Assignment ID:" in reply_text:
            try:
                parts = reply_text.split("Assignment ID:")
                if len(parts) > 1:
                    assignment_id_str = parts[1].split("\n")[0].strip()
                    assignment_id = int(assignment_id_str)
            except (ValueError, IndexError):
                pass
        
        if not assignment_id:
            await message.answer("❌ Не удалось найти ID задания.")
            return
        
        assignment = await self.assignment_service.get_assignment(assignment_id)
        if not assignment:
            await message.answer("❌ Задание не найдено.")
            return
        
        # Add feedback
        await self.assignment_service.add_feedback(assignment_id, answer_text)
        
        # Send feedback to user
        user = await self.user_service.get_user(assignment.user_id)
        if user:
            feedback_message = (
                f"💬 <b>Обратная связь по вашему заданию</b>\n\n"
                f"День {assignment.day_number}\n\n"
                f"{answer_text}"
            )
            
            # Send via course bot
            from core.config import Config
            from aiogram import Bot
            if not Config.COURSE_BOT_TOKEN:
                await message.answer("❌ COURSE_BOT_TOKEN не настроен.")
                return
            
            course_bot = Bot(token=Config.COURSE_BOT_TOKEN)
            try:
                await course_bot.send_message(user.user_id, feedback_message)
                await self.assignment_service.mark_feedback_sent(assignment_id)
                await message.answer("✅ Обратная связь отправлена пользователю.")
            finally:
                await course_bot.session.close()
        else:
            await message.answer("❌ Пользователь не найден.")
    
    async def _send_answer_to_user(self, user_id: int, answer_text: str, lesson_day: Optional[int] = None, bot_type: str = "course"):
        """Send answer to user via appropriate bot."""
        from core.config import Config
        from aiogram import Bot
        
        # Determine which bot to use
        if bot_type == "sales":
            if not Config.SALES_BOT_TOKEN:
                raise ValueError("SALES_BOT_TOKEN not configured")
            target_bot = Bot(token=Config.SALES_BOT_TOKEN)
        else:
            if not Config.COURSE_BOT_TOKEN:
                raise ValueError("COURSE_BOT_TOKEN not configured")
            target_bot = Bot(token=Config.COURSE_BOT_TOKEN)
        
        answer_message = "💬 <b>Ответ на ваш вопрос</b>\n\n"
        if lesson_day:
            answer_message += f"📚 Урок: День {lesson_day}\n\n"
        answer_message += answer_text
        
        try:
            await target_bot.send_message(user_id, answer_message)
        finally:
            await target_bot.session.close()
    
    async def handle_reply_button(self, callback: CallbackQuery):
        """Handle reply button click."""
        await callback.answer()
        # This can be used for inline reply buttons if needed
        await callback.message.answer("💬 Ответьте на сообщение выше, чтобы отправить ответ пользователю.")
    
    async def handle_assignment_reply_callback(self, callback: CallbackQuery):
        """Handle assignment reply button."""
        await callback.answer()
        assignment_id = int(callback.data.split(":")[1])
        await callback.message.answer(
            f"💬 <b>Ответ на задание</b>\n\n"
            f"Assignment ID: {assignment_id}\n\n"
            f"Ответьте на это сообщение с вашим ответом."
        )
    
    async def handle_question_reply_callback(self, callback: CallbackQuery):
        """Handle question reply button."""
        await callback.answer()
        parts = callback.data.split(":")
        user_id = int(parts[1])
        lesson_day = int(parts[2]) if len(parts) > 2 else None
        
        await callback.message.answer(
            f"💬 <b>Ответ на вопрос</b>\n\n"
            f"👤 Пользователь ID: {user_id}\n"
            f"{f'📚 Урок: День {lesson_day}' if lesson_day else ''}\n\n"
            f"Ответьте на это сообщение с вашим ответом."
        )
    
    async def handle_stats_button(self, message: Message):
        """Handle stats button from keyboard."""
        await self.handle_stats(message)
    
    async def handle_users_button(self, message: Message):
        """Handle users button from keyboard."""
        await self.handle_users(message)
    
    async def handle_settings_button(self, message: Message):
        """Handle settings button from keyboard."""
        await self.handle_settings(message)
    
    async def handle_sync_button(self, message: Message):
        """Handle sync button from keyboard."""
        await self.handle_sync_content(message)
    
    async def handle_forwarded_message(self, message: Message):
        """
        Handle messages forwarded from sales/course bots.
        These messages contain questions or assignments.
        """
        # Messages from other bots are already formatted and sent to ADMIN_CHAT_ID
        # We just need to ensure they're displayed properly
        # The reply handler will handle responses
        pass
    
    # Helper methods for statistics
    async def _get_total_users(self) -> int:
        """Get total number of users."""
        await self.db._ensure_connection()
        async with self.db.conn.execute("SELECT COUNT(*) FROM users") as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0
    
    async def _get_active_users(self) -> int:
        """Get number of active users (accessed in last 30 days)."""
        # Simple implementation - users with access
        await self.db._ensure_connection()
        async with self.db.conn.execute(
            "SELECT COUNT(*) FROM users WHERE tariff IS NOT NULL"
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0
    
    async def _get_users_with_access(self) -> int:
        """Get number of users with active access."""
        return await self._get_active_users()
    
    async def _get_total_assignments(self) -> int:
        """Get total number of assignments."""
        await self.db._ensure_connection()
        async with self.db.conn.execute("SELECT COUNT(*) FROM assignments") as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0
    
    async def _get_pending_assignments(self) -> int:
        """Get number of pending assignments."""
        await self.db._ensure_connection()
        async with self.db.conn.execute(
            "SELECT COUNT(*) FROM assignments WHERE feedback IS NULL OR feedback = ''"
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0
    
    async def _get_recent_users(self, limit: int = 20) -> list[User]:
        """Get recent users."""
        users = []
        await self.db._ensure_connection()
        async with self.db.conn.execute(
            "SELECT * FROM users ORDER BY created_at DESC LIMIT ?",
            (limit,)
        ) as cursor:
            rows = await cursor.fetchall()
            for row in rows:
                user = self.db._row_to_user(row)
                if user:
                    users.append(user)
        return users
    
    async def handle_user_stats(self, message: Message):
        """Handle /user_stats USER_ID command - show detailed stats for a user."""
        try:
            parts = message.text.split()
            if len(parts) < 2:
                await message.answer("❌ Использование: /user_stats USER_ID")
                return
            
            user_id = int(parts[1])
            await self._show_user_stats(message, user_id)
        except ValueError:
            await message.answer("❌ Неверный USER_ID. Используйте числовой ID.")
        except Exception as e:
            logger.error(f"Error getting user stats: {e}", exc_info=True)
            await message.answer("❌ Ошибка при получении статистики пользователя.")
    
    async def handle_all_user_stats(self, callback: CallbackQuery):
        """Handle callback to show all users stats."""
        await callback.answer()
        try:
            await self.db.connect()
            users = await self._get_recent_users(limit=200)  # Get all users (max 200)
            
            if not users:
                await callback.message.answer("👥 Пользователи не найдены.")
                return
            
            # Send stats for each user (split into multiple messages if needed)
            text = "📊 <b>Статистика всех пользователей</b>\n\n"
            for user in users:
                stats = await self.db.get_user_statistics(user.user_id)
                text += await self._format_user_stats_short(user, stats)
                text += "\n" + "─" * 30 + "\n\n"
                
                # Telegram message limit is 4096 chars, send in batches
                if len(text) > 3500:
                    await callback.message.answer(text, parse_mode="HTML")
                    text = ""
            
            if text:
                await callback.message.answer(text, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Error getting all user stats: {e}", exc_info=True)
            await callback.message.answer("❌ Ошибка при получении статистики.")
    
    async def handle_user_stats_detail(self, callback: CallbackQuery):
        """Handle callback to show detailed stats for a specific user."""
        await callback.answer()
        try:
            user_id = int(callback.data.split(":")[2])
            await self._show_user_stats(callback.message, user_id)
        except Exception as e:
            logger.error(f"Error getting user stats detail: {e}", exc_info=True)
            await callback.message.answer("❌ Ошибка при получении статистики.")
    
    async def _show_user_stats(self, message_or_callback, user_id: int):
        """Show detailed statistics for a user."""
        user = await self.user_service.get_user(user_id)
        if not user:
            await message_or_callback.answer("❌ Пользователь не найден.")
            return
        
        stats = await self.db.get_user_statistics(user_id)
        stats_text = await self._format_user_stats_detailed(user, stats)
        await message_or_callback.answer(stats_text, parse_mode="HTML")
    
    async def _format_user_stats_short(self, user: User, stats: dict) -> str:
        """Format short user stats (for list view)."""
        online_time = stats["total_online_time_seconds"]
        hours = online_time // 3600
        minutes = (online_time % 3600) // 60
        
        assignment_completion = 0
        if stats["assignments_submitted"] > 0:
            assignment_completion = (stats["assignments_completed"] / stats["assignments_submitted"]) * 100
        
        activity_percent = 0
        total_actions = sum(stats["activity_by_action"].values())
        if total_actions > 0:
            # Simple activity calculation based on actions
            activity_percent = min(100, (total_actions / 100) * 100)  # Normalize
        
        return (
            f"👤 <b>{user.first_name or 'Без имени'}</b> "
            f"{f'(@{user.username})' if user.username else ''}\n"
            f"🆔 ID: {user.user_id}\n"
            f"⏱️ Онлайн: {hours}ч {minutes}м\n"
            f"🔢 Заходов: {stats['total_bot_visits']}\n"
            f"❓ Вопросов: {stats['questions_count']}\n"
            f"📝 Заданий: {stats['assignments_submitted']} (выполнено: {stats['assignments_completed']})\n"
            f"📊 Активность: {activity_percent:.1f}%\n"
            f"✅ Выполнение заданий: {assignment_completion:.1f}%"
        )
    
    async def _format_user_stats_detailed(self, user: User, stats: dict) -> str:
        """Format detailed user statistics."""
        online_time = stats["total_online_time_seconds"]
        hours = online_time // 3600
        minutes = (online_time % 3600) // 60
        seconds = online_time % 60
        
        assignment_completion = 0
        if stats["assignments_submitted"] > 0:
            assignment_completion = (stats["assignments_completed"] / stats["assignments_submitted"]) * 100
        
        activity_percent = 0
        total_actions = sum(stats["activity_by_action"].values())
        if total_actions > 0:
            activity_percent = min(100, (total_actions / 50) * 100)  # Normalize based on expected activity
        
        # Top sections
        top_sections = sorted(stats["activity_by_section"].items(), key=lambda x: x[1], reverse=True)[:5]
        sections_text = "\n".join([f"  • {section}: {count}" for section, count in top_sections]) if top_sections else "  Нет данных"
        
        # Top actions
        top_actions = sorted(stats["activity_by_action"].items(), key=lambda x: x[1], reverse=True)[:5]
        actions_text = "\n".join([f"  • {action}: {count}" for action, count in top_actions]) if top_actions else "  Нет данных"
        
        return (
            f"📊 <b>Детальная статистика пользователя</b>\n\n"
            f"👤 <b>{user.first_name or 'Без имени'}</b> "
            f"{f'(@{user.username})' if user.username else ''}\n"
            f"🆔 ID: {user.user_id}\n"
            f"📅 Тариф: {user.tariff.value.upper() if user.tariff else 'Нет'}\n"
            f"📚 Текущий день: {user.current_day}\n\n"
            f"⏱️ <b>Время онлайн:</b>\n"
            f"  Всего: {hours}ч {minutes}м {seconds}с\n\n"
            f"🔢 <b>Заходы в ботов:</b>\n"
            f"  Всего: {stats['total_bot_visits']}\n"
            f"  Продающий бот: {stats['sales_bot_visits']}\n"
            f"  Курс-бот: {stats['course_bot_visits']}\n\n"
            f"❓ <b>Вопросы:</b> {stats['questions_count']}\n\n"
            f"📝 <b>Задания:</b>\n"
            f"  Отправлено: {stats['assignments_submitted']}\n"
            f"  Выполнено: {stats['assignments_completed']}\n"
            f"  Процент выполнения: {assignment_completion:.1f}%\n\n"
            f"📊 <b>Процент активности:</b> {activity_percent:.1f}%\n\n"
            f"📂 <b>Популярные разделы:</b>\n{sections_text}\n\n"
            f"🎯 <b>Популярные действия:</b>\n{actions_text}"
        )
    
    async def start(self):
        """Start the admin bot."""
        logger.info("Starting Admin Bot...")
        await self.db.connect()
        await self.dp.start_polling(self.bot)
    
    async def stop(self):
        """Stop the admin bot."""
        logger.info("Stopping Admin Bot...")
        await self.dp.stop_polling()
        await self.bot.session.close()
        await self.db.close()
