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
import secrets
import string
from typing import Optional
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton
)
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

from core.config import Config
from core.database import Database
from core.models import User, Assignment, Tariff
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

        # Simple admin "state machine" for settings flows (prices/promos)
        self._admin_state: dict[int, dict] = {}

        # Compose-reply state (lets admin answer without replying to the original message):
        # {admin_user_id: {"kind": "question"|"assignment", ...}}
        self._compose_reply: dict[int, dict] = {}
        
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

        # Compose-reply handlers (must be before settings input and before reply handlers)
        self.dp.message.register(self.handle_compose_reply_voice, F.voice)
        self.dp.message.register(self.handle_compose_reply_text, F.text & ~F.command)

        # Settings flows (non-command text input)
        self.dp.message.register(self.handle_admin_state_input, F.text & ~F.command)
        
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
        self.dp.callback_query.register(self.handle_question_reply_callback, F.data.startswith("curator_reply:"))
        self.dp.callback_query.register(self.handle_compose_reply_cancel, F.data == "admin:compose_reply:cancel")
        self.dp.callback_query.register(self.handle_all_user_stats, F.data == "admin:all_user_stats")
        self.dp.callback_query.register(self.handle_user_stats_detail, F.data.startswith("admin:user_stats:"))
        self.dp.callback_query.register(self.handle_restore_confirm, F.data.startswith("admin:restore_confirm:"))
        self.dp.callback_query.register(self.handle_restore_cancel, F.data == "admin:restore_cancel")
        self.dp.callback_query.register(self.handle_admin_prices_menu, F.data == "admin:prices")
        self.dp.callback_query.register(self.handle_admin_promos_menu, F.data == "admin:promos")
        self.dp.callback_query.register(self.handle_admin_promo_create, F.data == "admin:promo:create")
        self.dp.callback_query.register(self.handle_admin_promo_create_free, F.data == "admin:promo:create_free")
        self.dp.callback_query.register(self.handle_admin_promo_list, F.data == "admin:promo:list")
        self.dp.callback_query.register(self.handle_admin_promo_wizard_start, F.data == "admin:promo:wiz")
        self.dp.callback_query.register(self.handle_admin_promo_wizard_action, F.data.startswith("admin:promo:wiz:"))
        self.dp.callback_query.register(self.handle_admin_promo_view, F.data.startswith("admin:promo:view:"))
        self.dp.callback_query.register(self.handle_admin_promo_share, F.data.startswith("admin:promo:share:"))
        self.dp.callback_query.register(self.handle_admin_promo_delete, F.data.startswith("admin:promo:delete:"))
        self.dp.callback_query.register(self.handle_admin_promo_send, F.data.startswith("admin:promo:send:"))
        
        # Commands for user stats
        self.dp.message.register(self.handle_user_stats, Command("user_stats"))
        
        # Persistent keyboard buttons
        self.dp.message.register(self.handle_stats_button, F.text == "📊 Статистика")
        self.dp.message.register(self.handle_users_button, F.text == "👥 Пользователи")
        self.dp.message.register(self.handle_settings_button, F.text == "⚙️ Настройки")
        self.dp.message.register(self.handle_sync_button, F.text == "🔄 Обновить контент")
        self.dp.message.register(self.handle_restore_button, F.text == "⏪ Откатить обновление")
    
    async def handle_start(self, message: Message):
        """Handle /start command - show admin menu."""
        # Bind admin chat id for cross-bot forwarding (sales/course -> PUP).
        try:
            await self.db.connect()
            await self.db.set_setting("pup_admin_chat_id", str(message.chat.id))
        except Exception:
            logger.warning("Failed to bind pup_admin_chat_id", exc_info=True)

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
                ],
                [
                    KeyboardButton(text="⏪ Откатить обновление")
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
    
    def _format_user_display_name(self, user: User) -> str:
        """Format user display name for admin interface."""
        # Try first_name + last_name
        if user.first_name:
            name_parts = [user.first_name]
            if user.last_name:
                name_parts.append(user.last_name)
            return " ".join(name_parts)
        # Fallback to username
        if user.username:
            return f"@{user.username}"
        # Last resort: ID
        return f"ID {user.user_id}"
    
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
                display_name = self._format_user_display_name(user)
                text += (
                    f"• {display_name}\n"
                    f"  ID: {user.user_id} | Тариф: {tariff} | День: {user.current_day}\n\n"
                )
                keyboard_buttons.append([
                    InlineKeyboardButton(
                        text=f"📊 {display_name[:30]}",  # Limit button text length
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
        await self.db.connect()

        prices_text = await self._format_prices_text()
        promos = await self.db.list_promo_codes(limit=5, active_only=True)
        promos_text = ""
        if promos:
            promos_text = "\n\n🎟 <b>Промокоды (последние):</b>\n" + "\n".join(
                [self._format_promo_row(p) for p in promos]
            )

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
            f"• Документ: {('https://docs.google.com/document/d/' + Config.DRIVE_MASTER_DOC_ID + '/edit') if Config.DRIVE_MASTER_DOC_ID else 'Не указан'}\n"
            f"\n{prices_text}{promos_text}\n"
        )
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💰 Цены", callback_data="admin:prices")],
            [InlineKeyboardButton(text="🎟 Промокоды", callback_data="admin:promos")],
        ])
        await message.answer(settings_text, reply_markup=keyboard)

    @staticmethod
    def _promo_code_chars() -> str:
        return string.ascii_uppercase + string.digits

    def _generate_promo_code(self, length: int = 8) -> str:
        return "".join(secrets.choice(self._promo_code_chars()) for _ in range(int(length)))

    @staticmethod
    def _format_promo_row(p: dict) -> str:
        code = p.get("code")
        discount_type = (p.get("discount_type") or "").strip().lower()
        discount_value = p.get("discount_value")
        used = int(p.get("used_count") or 0)
        max_uses = p.get("max_uses")
        active = "✅" if int(p.get("active") or 0) == 1 else "❌"
        if discount_type == "percent":
            disc = f"-{float(discount_value):g}%"
        else:
            disc = f"-{float(discount_value):g}"
        cap = f"{used}/{max_uses}" if max_uses is not None else f"{used}"
        return f"• {active} <code>{code}</code> {disc} (исп.: {cap})"

    async def _format_prices_text(self) -> str:
        online_defaults = {
            Tariff.BASIC: 5000.0,
            Tariff.FEEDBACK: 10000.0,
            Tariff.PREMIUM: 8000.0,
            Tariff.PRACTIC: 20000.0,
        }
        offline_defaults = {
            "slushatel": 6000.0,
            "aktivist": 12000.0,
            "media_persona": 22000.0,
            "glavnyi_geroi": 30000.0,
        }

        lines = ["💰 <b>Цены:</b>", "<b>Онлайн:</b>"]
        for t in [Tariff.BASIC, Tariff.FEEDBACK, Tariff.PREMIUM, Tariff.PRACTIC]:
            price = await self.db.get_online_tariff_price(t, online_defaults[t])
            lines.append(f"• {t.value}: {price:.0f}")
        lines.append("<b>Офлайн:</b>")
        for k, default in offline_defaults.items():
            price = await self.db.get_offline_tariff_price(k, default)
            lines.append(f"• {k}: {price:.0f}")
        return "\n".join(lines)

    async def handle_admin_prices_menu(self, callback: CallbackQuery):
        await callback.answer()
        await self.db.connect()
        text = (
            "💰 <b>Изменение цен</b>\n\n"
            "Отправьте одним сообщением пары <code>ключ=цена</code>.\n"
            "Можно с новой строки или через пробел.\n\n"
            "Ключи онлайн: <code>basic</code>, <code>feedback</code>, <code>premium</code>, <code>practic</code>\n"
            "Ключи офлайн: <code>slushatel</code>, <code>aktivist</code>, <code>media_persona</code>, <code>glavnyi_geroi</code>\n\n"
            "Пример:\n"
            "<code>basic=5000\nfeedback=10000\nslushatel=6000</code>"
        )
        self._admin_state[callback.from_user.id] = {"type": "set_prices"}
        await callback.message.answer(text)

    async def handle_admin_promos_menu(self, callback: CallbackQuery):
        await callback.answer()
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🎛 Создать кнопками", callback_data="admin:promo:wiz")],
            [InlineKeyboardButton(text="➕ Создать (вручную)", callback_data="admin:promo:create")],
            [InlineKeyboardButton(text="🎁 Бесплатный промокод (100%)", callback_data="admin:promo:create_free")],
            [InlineKeyboardButton(text="📋 Список промокодов", callback_data="admin:promo:list")],
        ])
        await callback.message.answer(
            "🎟 <b>Промокоды</b>\n\n"
            "Создайте промокод и разошлите его. В sales-боте есть кнопка «🎟 Промокод».",
            reply_markup=keyboard,
        )

    async def handle_admin_promo_list(self, callback: CallbackQuery):
        await callback.answer()
        await self.db.connect()
        promos = await self.db.list_promo_codes(limit=20, active_only=True)
        if not promos:
            await callback.message.answer("🎟 Промокодов пока нет.")
            return
        text = "🎟 <b>Промокоды:</b>\n" + "\n".join([self._format_promo_row(p) for p in promos])
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"{p.get('code')} →", callback_data=f"admin:promo:view:{p.get('code')}")]
            for p in promos
            if p.get("code")
        ] + [[InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:promos")]])
        await callback.message.answer(text, reply_markup=keyboard)

    async def handle_admin_promo_create(self, callback: CallbackQuery):
        await callback.answer()
        self._admin_state[callback.from_user.id] = {"type": "create_promo"}
        await callback.message.answer(
            "🎟 <b>Создание промокода</b>\n\n"
            "Отправьте скидку и (опционально) код:\n"
            "• <code>10%</code> или <code>-10%</code>\n"
            "• <code>500</code> или <code>-500</code> (фикс. скидка)\n"
            "• <code>CODE 10%</code>\n"
            "Дополнительно можно указать лимит использований: <code>x10</code>\n"
            "Пример: <code>HERO10 10% x50</code>"
        )

    async def handle_admin_promo_create_free(self, callback: CallbackQuery):
        await callback.answer()
        self._admin_state[callback.from_user.id] = {"type": "create_free_promo"}
        await callback.message.answer(
            "🎁 <b>Бесплатный промокод (100%)</b>\n\n"
            "Отправьте (опционально) код и лимит использований:\n"
            "• <code>CODE</code>\n"
            "• <code>CODE x10</code>\n"
            "• <code>x10</code> (код будет сгенерирован автоматически)\n\n"
            "Пример: <code>FREEHERO x50</code>"
        )

    async def handle_admin_promo_send(self, callback: CallbackQuery):
        await callback.answer()
        parts = (callback.data or "").split(":")
        if len(parts) < 5:
            await callback.message.answer("❌ Неверная команда.")
            return
        target = parts[3]
        code = parts[4]
        await self.db.connect()
        promo = await self.db.get_valid_promo_code(code)
        if not promo:
            await callback.message.answer("❌ Промокод не найден или неактивен.")
            return

        chat_id = None
        if target == "general":
            chat_id = Config.GENERAL_GROUP_ID
        elif target == "premium":
            chat_id = Config.PREMIUM_GROUP_ID
        elif target == "me":
            chat_id = callback.from_user.id

        if not chat_id:
            await callback.message.answer("❌ Целевой чат не настроен (GENERAL_GROUP_ID / PREMIUM_GROUP_ID).")
            return

        discount_type = (promo.get("discount_type") or "").strip().lower()
        discount_value = float(promo.get("discount_value") or 0.0)
        disc = f"{discount_value:g}%" if discount_type == "percent" else f"{discount_value:g}"

        await self.bot.send_message(
            chat_id,
            self._promo_share_text(promo, disc),
        )
        await callback.message.answer("✅ Отправлено.")

    @staticmethod
    def _promo_share_text(promo: dict, disc: str) -> str:
        is_free = False
        try:
            is_free = (str(promo.get("discount_type") or "").strip().lower() == "percent") and (float(promo.get("discount_value") or 0) >= 100.0)
        except Exception:
            is_free = False
        title = "🎁 <b>Бесплатный доступ</b>" if is_free else "🎟 <b>Промокод</b>"
        return (
            f"{title}\n\n"
            f"Код: <code>{promo.get('code')}</code>\n"
            f"Скидка: -{disc}\n\n"
            "Откройте sales-бот и нажмите «🎟 Промокод», чтобы применить."
        )

    def _get_promo_wizard(self, admin_id: int) -> dict:
        state = self._admin_state.get(admin_id)
        if not state or state.get("type") != "promo_wizard":
            state = {
                "type": "promo_wizard",
                "discount_type": "percent",
                "discount_value": 10.0,
                "max_uses": None,
                "code": None,
            }
            self._admin_state[admin_id] = state
        return state

    def _promo_wizard_text(self, state: dict) -> str:
        discount_type = (state.get("discount_type") or "").strip().lower()
        discount_value = float(state.get("discount_value") or 0.0)
        max_uses = state.get("max_uses")
        code = state.get("code")
        disc = f"{discount_value:g}%" if discount_type == "percent" else f"{discount_value:g}"
        uses = "∞" if max_uses in (None, "", 0) else str(int(max_uses))
        code_text = code or "🎲 (будет сгенерирован)"
        return (
            "🎛 <b>Создание промокода (кнопками)</b>\n\n"
            f"Скидка: <b>-{disc}</b>\n"
            f"Лимит использований: <b>{uses}</b>\n"
            f"Код: <b>{code_text}</b>\n\n"
            "Выберите параметры и нажмите «✅ Создать»."
        )

    def _promo_wizard_keyboard(self, state: dict) -> InlineKeyboardMarkup:
        discount_type = (state.get("discount_type") or "").strip().lower()

        type_row = [
            InlineKeyboardButton(text="%", callback_data="admin:promo:wiz:type:percent"),
            InlineKeyboardButton(text="₽", callback_data="admin:promo:wiz:type:amount"),
            InlineKeyboardButton(text="🎁 100%", callback_data="admin:promo:wiz:type:free"),
        ]

        if discount_type == "amount":
            values = [500, 1000, 2000, 5000, 10000]
            value_rows = [
                [
                    InlineKeyboardButton(text=str(v), callback_data=f"admin:promo:wiz:val:{v}")
                    for v in values[:3]
                ],
                [
                    InlineKeyboardButton(text=str(v), callback_data=f"admin:promo:wiz:val:{v}")
                    for v in values[3:]
                ],
                [InlineKeyboardButton(text="✍️ Другая сумма", callback_data="admin:promo:wiz:val:custom")],
            ]
        else:
            # percent or free
            values = [5, 10, 15, 20, 25, 30, 50, 100]
            value_rows = [
                [
                    InlineKeyboardButton(text=f"{v}%", callback_data=f"admin:promo:wiz:val:{v}")
                    for v in values[:4]
                ],
                [
                    InlineKeyboardButton(text=f"{v}%", callback_data=f"admin:promo:wiz:val:{v}")
                    for v in values[4:]
                ],
                [InlineKeyboardButton(text="✍️ Другой %", callback_data="admin:promo:wiz:val:custom")],
            ]

        max_rows = [
            [
                InlineKeyboardButton(text="∞", callback_data="admin:promo:wiz:max:unlim"),
                InlineKeyboardButton(text="1", callback_data="admin:promo:wiz:max:1"),
                InlineKeyboardButton(text="5", callback_data="admin:promo:wiz:max:5"),
                InlineKeyboardButton(text="10", callback_data="admin:promo:wiz:max:10"),
            ],
            [
                InlineKeyboardButton(text="50", callback_data="admin:promo:wiz:max:50"),
                InlineKeyboardButton(text="100", callback_data="admin:promo:wiz:max:100"),
            ],
        ]

        code_rows = [
            [
                InlineKeyboardButton(text="🎲 Код авто", callback_data="admin:promo:wiz:code:auto"),
                InlineKeyboardButton(text="✍️ Ввести код", callback_data="admin:promo:wiz:code:custom"),
            ]
        ]

        action_rows = [
            [InlineKeyboardButton(text="✅ Создать", callback_data="admin:promo:wiz:create")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:promos")],
        ]

        return InlineKeyboardMarkup(inline_keyboard=[type_row] + value_rows + max_rows + code_rows + action_rows)

    async def handle_admin_promo_wizard_start(self, callback: CallbackQuery):
        await callback.answer()
        state = self._get_promo_wizard(callback.from_user.id)
        text = self._promo_wizard_text(state)
        kb = self._promo_wizard_keyboard(state)
        try:
            await callback.message.edit_text(text, reply_markup=kb)
        except Exception:
            await callback.message.answer(text, reply_markup=kb)

    async def handle_admin_promo_wizard_action(self, callback: CallbackQuery):
        await callback.answer()
        admin_id = callback.from_user.id
        state = self._get_promo_wizard(admin_id)
        parts = (callback.data or "").split(":")
        # admin:promo:wiz:<cmd>:<arg>
        cmd = parts[3] if len(parts) > 3 else None
        arg = parts[4] if len(parts) > 4 else None

        if cmd == "type":
            if arg == "amount":
                state["discount_type"] = "amount"
                state["discount_value"] = 500.0
            elif arg == "free":
                state["discount_type"] = "percent"
                state["discount_value"] = 100.0
            else:
                state["discount_type"] = "percent"
                state["discount_value"] = 10.0

        elif cmd == "val":
            if arg == "custom":
                self._admin_state[admin_id] = {"type": "promo_wizard_value_input", "wizard": state}
                await callback.message.answer(
                    "✍️ Отправьте значение скидки одним сообщением.\n"
                    "Примеры: <code>10%</code>, <code>25</code>, <code>500</code>"
                )
                return
            try:
                state["discount_value"] = float(arg)
            except Exception:
                pass

        elif cmd == "max":
            if arg == "unlim":
                state["max_uses"] = None
            else:
                try:
                    state["max_uses"] = int(arg)
                except Exception:
                    pass

        elif cmd == "code":
            if arg == "auto":
                state["code"] = None
            elif arg == "custom":
                self._admin_state[admin_id] = {"type": "promo_wizard_code_input", "wizard": state}
                await callback.message.answer("✍️ Отправьте код промокода (латиница/цифры), пример: <code>HERO100</code>")
                return

        elif cmd == "create":
            await self.db.connect()
            code = (state.get("code") or "").strip().upper() or self._generate_promo_code()
            discount_type = (state.get("discount_type") or "percent").strip().lower()
            discount_value = float(state.get("discount_value") or 0.0)
            max_uses = state.get("max_uses")
            try:
                await self.db.create_promo_code(
                    code=code,
                    discount_type=discount_type,
                    discount_value=discount_value,
                    max_uses=max_uses,
                    created_by=admin_id,
                )
            except Exception as e:
                await callback.message.answer(f"❌ Не удалось создать промокод: {e}")
                return

            # Reset wizard state
            self._admin_state.pop(admin_id, None)
            disc = f"{discount_value:g}%" if discount_type == "percent" else f"{discount_value:g}"
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📩 Мне (для пересылки)", callback_data=f"admin:promo:send:me:{code}")],
                [InlineKeyboardButton(text="📨 Сообщение для пересылки", callback_data=f"admin:promo:share:{code}")],
            ])
            await callback.message.answer(
                "✅ <b>Промокод создан</b>\n\n"
                f"Код: <code>{code}</code>\n"
                f"Скидка: -{disc}\n\n"
                "В sales-боте нажмите «🎟 Промокод» и введите этот код.",
                reply_markup=keyboard,
            )
            return

        # Refresh wizard UI
        self._admin_state[admin_id] = state
        text = self._promo_wizard_text(state)
        kb = self._promo_wizard_keyboard(state)
        try:
            await callback.message.edit_text(text, reply_markup=kb)
        except Exception:
            await callback.message.answer(text, reply_markup=kb)

    async def handle_admin_promo_view(self, callback: CallbackQuery):
        await callback.answer()
        parts = (callback.data or "").split(":")
        code = parts[3] if len(parts) > 3 else ""
        await self.db.connect()
        promo = await self.db.get_valid_promo_code(code)
        if not promo:
            await callback.message.answer("❌ Промокод не найден или неактивен.")
            return
        discount_type = (promo.get("discount_type") or "").strip().lower()
        discount_value = float(promo.get("discount_value") or 0.0)
        disc = f"{discount_value:g}%" if discount_type == "percent" else f"{discount_value:g}"
        text = (
            "🎟 <b>Промокод</b>\n\n"
            f"Код: <code>{promo.get('code')}</code>\n"
            f"Скидка: -{disc}\n"
            f"Лимит: {promo.get('max_uses') or '∞'}\n"
            f"Использовано: {promo.get('used_count') or 0}\n"
        )
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📩 Мне (для пересылки)", callback_data=f"admin:promo:send:me:{promo.get('code')}")],
            [InlineKeyboardButton(text="📨 Сообщение для пересылки", callback_data=f"admin:promo:share:{promo.get('code')}")],
            [InlineKeyboardButton(text="🗑 Удалить промокод", callback_data=f"admin:promo:delete:{promo.get('code')}")],
            [InlineKeyboardButton(text="⬅️ К списку", callback_data="admin:promo:list")],
        ])
        await callback.message.answer(text, reply_markup=keyboard)

    async def handle_admin_promo_share(self, callback: CallbackQuery):
        await callback.answer()
        parts = (callback.data or "").split(":")
        code = parts[3] if len(parts) > 3 else ""
        await self.db.connect()
        promo = await self.db.get_valid_promo_code(code)
        if not promo:
            await callback.message.answer("❌ Промокод не найден или неактивен.")
            return
        discount_type = (promo.get("discount_type") or "").strip().lower()
        discount_value = float(promo.get("discount_value") or 0.0)
        disc = f"{discount_value:g}%" if discount_type == "percent" else f"{discount_value:g}"
        await callback.message.answer(self._promo_share_text(promo, disc))

    async def handle_admin_promo_delete(self, callback: CallbackQuery):
        await callback.answer()
        parts = (callback.data or "").split(":")
        code = parts[3] if len(parts) > 3 else ""
        code = (code or "").strip().upper()
        if not code:
            await callback.message.answer("❌ Неверный код.")
            return
        await self.db.connect()
        ok = await self.db.deactivate_promo_code(code)
        if not ok:
            await callback.message.answer("❌ Не удалось удалить промокод (возможно, уже удалён).")
            return
        await callback.message.answer(f"✅ Промокод <code>{code}</code> удалён (деактивирован).")

    async def handle_admin_state_input(self, message: Message):
        state = self._admin_state.get(message.from_user.id)
        if not state:
            raise SkipHandler()

        kind = state.get("type")
        text = (message.text or "").strip()
        if kind == "set_prices":
            await self.db.connect()
            updates = self._parse_price_updates(text)
            if not updates:
                await message.answer("❌ Не нашёл ни одной пары ключ=цена.")
                return
            for key, price in updates.items():
                if key in ("basic", "feedback", "premium", "practic"):
                    await self.db.set_online_tariff_price(Tariff(key), price)
                else:
                    await self.db.set_offline_tariff_price(key, price)
            self._admin_state.pop(message.from_user.id, None)
            await message.answer("✅ Цены обновлены.\n\n" + await self._format_prices_text())
            return

        if kind == "create_free_promo":
            await self.db.connect()
            code, max_uses = self._parse_free_promo_create(text)
            code = code or self._generate_promo_code()
            try:
                await self.db.create_promo_code(
                    code=code,
                    discount_type="percent",
                    discount_value=100.0,
                    max_uses=max_uses,
                    created_by=message.from_user.id,
                )
            except Exception as e:
                await message.answer(f"❌ Не удалось создать промокод: {e}")
                return

            self._admin_state.pop(message.from_user.id, None)
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📩 Мне (для пересылки)", callback_data=f"admin:promo:send:me:{code}")],
                [InlineKeyboardButton(text="📨 Сообщение для пересылки", callback_data=f"admin:promo:share:{code}")],
            ])
            await message.answer(
                "✅ <b>Бесплатный промокод создан</b>\n\n"
                f"Код: <code>{code}</code>\n"
                "Скидка: -100%\n\n"
                "В sales-боте нажмите «🎟 Промокод» и введите этот код — доступ выдастся без оплаты.",
                reply_markup=keyboard,
            )
            return

        if kind in ("promo_wizard_code_input", "promo_wizard_value_input"):
            wizard = state.get("wizard")
            if not isinstance(wizard, dict):
                self._admin_state.pop(message.from_user.id, None)
                await message.answer("❌ Сессия настройки промокода устарела. Откройте «🎛 Создать кнопками» заново.")
                return

            if kind == "promo_wizard_code_input":
                code = (text or "").strip().upper()
                if not code or any(ch not in self._promo_code_chars() for ch in code):
                    await message.answer("❌ Код может содержать только латиницу и цифры. Попробуйте снова.")
                    return
                wizard["code"] = code
            else:
                raw = (text or "").strip().replace(",", ".")
                try:
                    if raw.endswith("%"):
                        val = float(raw.strip("%"))
                        wizard["discount_type"] = "percent"
                        wizard["discount_value"] = abs(val)
                    else:
                        val = float(raw)
                        if (wizard.get("discount_type") or "").strip().lower() == "amount":
                            wizard["discount_value"] = abs(val)
                        else:
                            # default to percent if not amount
                            wizard["discount_type"] = "percent"
                            wizard["discount_value"] = abs(val)
                except Exception:
                    await message.answer("❌ Не понял число. Пример: <code>10%</code> или <code>500</code>")
                    return

            self._admin_state[message.from_user.id] = wizard
            await message.answer(self._promo_wizard_text(wizard), reply_markup=self._promo_wizard_keyboard(wizard))
            return

        if kind == "create_promo":
            await self.db.connect()
            parsed = self._parse_promo_create(text)
            if not parsed:
                await message.answer("❌ Не понял формат. Пример: <code>HERO10 10% x50</code>")
                return
            code, discount_type, discount_value, max_uses = parsed
            code = code or self._generate_promo_code()
            try:
                await self.db.create_promo_code(
                    code=code,
                    discount_type=discount_type,
                    discount_value=discount_value,
                    max_uses=max_uses,
                    created_by=message.from_user.id,
                )
            except Exception as e:
                await message.answer(f"❌ Не удалось создать промокод: {e}")
                return

            self._admin_state.pop(message.from_user.id, None)
            disc = f"{discount_value:g}%" if discount_type == "percent" else f"{discount_value:g}"
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📩 Мне (для пересылки)", callback_data=f"admin:promo:send:me:{code}")],
                [InlineKeyboardButton(text="📨 Сообщение для пересылки", callback_data=f"admin:promo:share:{code}")],
            ])
            await message.answer(
                "✅ <b>Промокод создан</b>\n\n"
                f"Код: <code>{code}</code>\n"
                f"Скидка: -{disc}\n\n"
                "В sales-боте нажмите «🎟 Промокод» и введите этот код.",
                reply_markup=keyboard,
            )
            return

        raise SkipHandler()

    @staticmethod
    def _parse_price_updates(text: str) -> dict[str, float]:
        out: dict[str, float] = {}
        for raw in (text or "").replace(",", ".").split():
            if "=" not in raw:
                continue
            k, v = raw.split("=", 1)
            k = (k or "").strip().lower()
            v = (v or "").strip()
            try:
                out[k] = float(v)
            except Exception:
                continue
        return out

    @staticmethod
    def _parse_free_promo_create(text: str) -> tuple[Optional[str], Optional[int]]:
        tokens = (text or "").strip().split()
        if not tokens:
            return None, None

        max_uses = None
        for t in tokens[:]:
            tl = t.lower().strip()
            if tl.startswith("x") and tl[1:].isdigit():
                max_uses = int(tl[1:])
                tokens.remove(t)
                break
            if tl.startswith("max=") and tl[4:].isdigit():
                max_uses = int(tl[4:])
                tokens.remove(t)
                break

        code = tokens[0].strip().upper() if tokens else None
        return code, max_uses

    @staticmethod
    def _parse_promo_create(text: str) -> Optional[tuple[Optional[str], str, float, Optional[int]]]:
        tokens = (text or "").strip().split()
        if not tokens:
            return None

        code = None
        discount_token = None
        max_uses = None

        # detect max uses token: x10 or max=10
        for t in tokens[:]:
            tl = t.lower().strip()
            if tl.startswith("x") and tl[1:].isdigit():
                max_uses = int(tl[1:])
                tokens.remove(t)
                break
            if tl.startswith("max=") and tl[4:].isdigit():
                max_uses = int(tl[4:])
                tokens.remove(t)
                break

        if len(tokens) == 1:
            discount_token = tokens[0]
        elif len(tokens) >= 2:
            # CODE DISCOUNT
            if "%" in tokens[1] or tokens[1].lstrip("+-").replace(".", "", 1).isdigit():
                code = tokens[0].strip().upper()
                discount_token = tokens[1]
            else:
                discount_token = tokens[0]

        if not discount_token:
            return None

        dt = discount_token.strip()
        if dt.endswith("%"):
            try:
                val = float(dt.strip("%").lstrip("+-"))
            except Exception:
                return None
            return code, "percent", val, max_uses

        try:
            val = float(dt.lstrip("+-"))
        except Exception:
            return None
        return code, "amount", val, max_uses
    
    async def handle_sync_content(self, message: Message):
        """Handle /sync_content command - sync content from Google Drive."""
        if not self.drive_sync or not self.drive_sync._admin_ready():
            await message.answer(
                "❌ Синхронизация с Google Drive не настроена.\n\n"
                "Убедитесь, что установлены:\n"
                "• DRIVE_CONTENT_ENABLED=1\n"
                "• DRIVE_MASTER_DOC_ID (ID документа)\n"
                "• GOOGLE_SERVICE_ACCOUNT_JSON"
            )
            return

    def _compose_cancel_keyboard(self) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✖️ Отмена", callback_data="admin:compose_reply:cancel")]
        ])

    async def handle_compose_reply_cancel(self, callback: CallbackQuery):
        await callback.answer()
        self._compose_reply.pop(callback.from_user.id, None)
        await callback.message.answer("✅ Отправка отменена.")

    async def handle_compose_reply_text(self, message: Message):
        if message.reply_to_message:
            raise SkipHandler()

        state = self._compose_reply.get(message.from_user.id)
        if not state:
            raise SkipHandler()

        answer_text = (message.text or "").strip()
        if not answer_text:
            await message.answer("❌ Отправьте текст или голосовое.")
            return

        kind = state.get("kind")
        if kind == "question":
            try:
                await self._send_answer_to_user(
                    user_id=int(state["user_id"]),
                    answer_text=answer_text,
                    lesson_day=state.get("lesson_day"),
                    bot_type=str(state.get("bot_type") or "course"),
                )
            except Exception as e:
                await message.answer(f"❌ Не удалось отправить ответ: {e}")
                return
            else:
                self._compose_reply.pop(message.from_user.id, None)
                await message.answer("✅ Ответ отправлен пользователю.")
            return

        if kind == "assignment":
            assignment_id = int(state["assignment_id"])
            try:
                await self._send_assignment_feedback_to_user(
                    admin_message=message,
                    assignment_id=assignment_id,
                    answer_text=answer_text,
                    voice_file_id=None,
                )
            except Exception as e:
                await message.answer(f"❌ Не удалось отправить обратную связь: {e}")
                return
            else:
                self._compose_reply.pop(message.from_user.id, None)
            return

        self._compose_reply.pop(message.from_user.id, None)
        await message.answer("❌ Не удалось отправить: неизвестный тип ответа.")

    async def handle_compose_reply_voice(self, message: Message):
        if message.reply_to_message:
            raise SkipHandler()

        state = self._compose_reply.get(message.from_user.id)
        if not state:
            raise SkipHandler()

        if not message.voice:
            raise SkipHandler()

        voice_file_id = message.voice.file_id
        answer_text = (message.caption or "").strip()

        kind = state.get("kind")
        if kind == "question":
            try:
                await self._send_answer_to_user(
                    user_id=int(state["user_id"]),
                    answer_text=answer_text,
                    lesson_day=state.get("lesson_day"),
                    bot_type=str(state.get("bot_type") or "course"),
                    voice_file_id=voice_file_id,
                )
            except Exception as e:
                await message.answer(f"❌ Не удалось отправить ответ: {e}")
                return
            else:
                self._compose_reply.pop(message.from_user.id, None)
                await message.answer("✅ Ответ отправлен пользователю.")
            return

        if kind == "assignment":
            assignment_id = int(state["assignment_id"])
            try:
                await self._send_assignment_feedback_to_user(
                    admin_message=message,
                    assignment_id=assignment_id,
                    answer_text=answer_text,
                    voice_file_id=voice_file_id,
                )
            except Exception as e:
                await message.answer(f"❌ Не удалось отправить обратную связь: {e}")
                return
            else:
                self._compose_reply.pop(message.from_user.id, None)
            return

        self._compose_reply.pop(message.from_user.id, None)
        await message.answer("❌ Не удалось отправить: неизвестный тип ответа.")
        
        # Show current document info
        doc_id = (Config.DRIVE_MASTER_DOC_ID or "").strip()
        doc_url = f"https://docs.google.com/document/d/{doc_id}/edit" if doc_id else "Не указан"
        
        await message.answer(
            f"🔄 <b>Начинаю синхронизацию контента</b>\n\n"
            f"📄 <b>Документ:</b> {doc_url}\n"
            f"⏳ Подтягиваю данные из Google Drive...\n\n"
            f"Это может занять несколько секунд."
        )
        
        try:
            # sync_now is synchronous, run in executor to avoid blocking
            import asyncio
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, self.drive_sync.sync_now)
            
            # Check for warnings
            warnings_text = ""
            if result.warnings:
                warnings_text = f"\n⚠️ <b>Предупреждения:</b>\n" + "\n".join([f"• {w}" for w in result.warnings[:5]])
                if len(result.warnings) > 5:
                    warnings_text += f"\n... и еще {len(result.warnings) - 5} предупреждений"
            
            # result is SyncResult dataclass
            await message.answer(
                f"✅ <b>Синхронизация завершена</b>\n\n"
                f"📄 Документ: {doc_url}\n"
                f"• Обновлено дней: {result.days_synced}\n"
                f"• Медиа файлов загружено: {result.media_files_downloaded}\n"
                f"• Путь к урокам: {result.lessons_path}\n"
                f"{warnings_text}\n\n"
                f"💡 Контент обновлен. Курс-бот автоматически подхватит изменения."
            )
        except Exception as e:
            logger.error(f"Error syncing content: {e}", exc_info=True)
            await message.answer(
                f"❌ <b>Ошибка при синхронизации</b>\n\n"
                f"{str(e)}\n\n"
                f"💡 Проверьте:\n"
                f"• Доступ к Google Drive\n"
                f"• Правильность ID документа\n"
                f"• Настройки сервисного аккаунта"
            )
    
    async def handle_reply(self, message: Message):
        """Handle reply to question/assignment message."""
        if not message.reply_to_message:
            return
        
        reply_text = message.reply_to_message.text or message.reply_to_message.caption or ""
        answer_text = message.text or message.caption or ""
        voice_file_id = message.voice.file_id if message.voice else None
        
        if not answer_text and not voice_file_id:
            await message.answer("❌ Ответ не может быть пустым (текст или голосовое).")
            return

        def _extract_callback_data(msg: Message) -> list[str]:
            datas: list[str] = []
            rm = getattr(msg, "reply_markup", None)
            kb = getattr(rm, "inline_keyboard", None) if rm else None
            if not kb:
                return datas
            for row in kb:
                for btn in row:
                    cd = getattr(btn, "callback_data", None)
                    if cd:
                        datas.append(cd)
            return datas

        # Prefer machine-readable routing from inline keyboard callback_data
        for cd in _extract_callback_data(message.reply_to_message):
            if cd.startswith("admin_reply:"):
                try:
                    assignment_id = int(cd.split(":")[1])
                except Exception:
                    continue
                await self._send_assignment_feedback_to_user(
                    admin_message=message,
                    assignment_id=assignment_id,
                    answer_text=answer_text,
                    voice_file_id=voice_file_id,
                )
                return

            if cd.startswith("curator_reply:") or cd.startswith("reply_question:"):
                parts = cd.split(":")
                if len(parts) < 2:
                    continue
                try:
                    user_id = int(parts[1])
                except Exception:
                    continue

                if cd.startswith("reply_question:"):
                    bot_type = "sales"
                    lesson_day = None
                else:
                    bot_type = "course"
                    try:
                        lesson_day = int(parts[2]) if len(parts) > 2 else None
                    except Exception:
                        lesson_day = None

                await self._send_answer_to_user(
                    user_id=user_id,
                    answer_text=answer_text,
                    lesson_day=lesson_day,
                    bot_type=bot_type,
                    voice_file_id=voice_file_id,
                )
                await message.answer("✅ Ответ отправлен пользователю.")
                return
        
        # Check if this is a question or assignment
        # Questions: contain "❓", "Новый вопрос", "Вопрос:", "💭 Вопрос:"
        is_question = (
            "❓" in reply_text or 
            "Новый вопрос" in reply_text or 
            "Вопрос:" in reply_text or
            "💭 Вопрос:" in reply_text or
            "Ответ на вопрос" in reply_text
        )
        # Assignments: contain "📝", "Новое задание", "Задание", "ID задания:", "Assignment ID:"
        is_assignment = (
            "📝" in reply_text or 
            "Новое задание" in reply_text or 
            "Задание" in reply_text or 
            "ID задания:" in reply_text or
            "🔢 ID задания:" in reply_text or
            "Assignment ID:" in reply_text
        )
        
        if is_question:
            await self._handle_question_reply(message, reply_text, answer_text, voice_file_id=voice_file_id)
        elif is_assignment:
            await self._handle_assignment_reply(message, reply_text, answer_text, voice_file_id=voice_file_id)
        else:
            await message.answer("❌ Не удалось определить тип сообщения. Ответьте на вопрос или задание.")
    
    async def _handle_question_reply(self, message: Message, reply_text: str, answer_text: str, voice_file_id: Optional[str] = None):
        """Handle reply to question."""
        # Extract user_id from message - try multiple formats
        user_id = None
        lesson_day = None
        bot_type = "course"  # default
        
        # Try "🆔 ID:" format
        if "🆔 ID:" in reply_text:
            try:
                parts = reply_text.split("🆔 ID:")
                if len(parts) > 1:
                    user_id_str = parts[1].split("\n")[0].strip()
                    user_id = int(user_id_str)
            except (ValueError, IndexError):
                pass
        
        # Try "👤 Пользователь ID:" format
        if not user_id and "👤 Пользователь ID:" in reply_text:
            try:
                parts = reply_text.split("👤 Пользователь ID:")
                if len(parts) > 1:
                    user_id_str = parts[1].split("\n")[0].strip()
                    user_id = int(user_id_str)
            except (ValueError, IndexError):
                pass
        
        # Try extracting from callback data if available
        if not user_id and hasattr(message.reply_to_message, 'reply_markup'):
            if message.reply_to_message.reply_markup:
                for row in message.reply_to_message.reply_markup.inline_keyboard:
                    for button in row:
                        if button.callback_data:
                            # Try curator_reply format: curator_reply:user_id:lesson_day
                            if "curator_reply:" in button.callback_data:
                                try:
                                    parts = button.callback_data.split(":")
                                    if len(parts) >= 2:
                                        user_id = int(parts[1])
                                        if len(parts) >= 3:
                                            lesson_day = int(parts[2])
                                    break
                                except (ValueError, IndexError):
                                    pass
                            # Try reply_question format: reply_question:user_id:lesson_day
                            elif "reply_question:" in button.callback_data:
                                try:
                                    parts = button.callback_data.split(":")
                                    if len(parts) >= 2:
                                        user_id = int(parts[1])
                                        if len(parts) >= 3:
                                            lesson_day = int(parts[2])
                                    break
                                except (ValueError, IndexError):
                                    pass
        
        # Extract lesson day from text
        if "📚 Урок:" in reply_text or "День" in reply_text:
            try:
                import re
                day_match = re.search(r'День\s+(\d+)', reply_text)
                if day_match:
                    lesson_day = int(day_match.group(1))
            except (ValueError, IndexError):
                pass
        
        # Check bot type - determine if question came from sales bot
        is_sales_bot = (
            "sales bot" in reply_text.lower() or 
            "продающего бота" in reply_text.lower() or
            "продающий бот" in reply_text.lower() or
            "Источник: Продающий бот" in reply_text or
            "Источник:" in reply_text and "sales bot" in reply_text.lower()
        )
        bot_type = "sales" if is_sales_bot else "course"
        
        if not user_id:
            await message.answer("❌ Не удалось найти ID пользователя. Убедитесь, что вы отвечаете на сообщение с вопросом.")
            return
        
        # Send answer to user via appropriate bot (determined by bot_type)
        try:
            await self._send_answer_to_user(user_id, answer_text, lesson_day, bot_type, voice_file_id=voice_file_id)
            bot_name = "продающий бот" if bot_type == "sales" else "обучающий бот"
            await message.answer(f"✅ Ответ отправлен пользователю в {bot_name}.")
        except Exception as e:
            logger.error(f"Error sending answer to user: {e}", exc_info=True)
            await message.answer(f"❌ Ошибка при отправке ответа: {e}")

    async def _send_assignment_feedback_to_user(
        self,
        admin_message: Message,
        assignment_id: int,
        answer_text: str,
        voice_file_id: Optional[str] = None,
    ):
        assignment = await self.assignment_service.get_assignment(assignment_id)
        if not assignment:
            await admin_message.answer("❌ Задание не найдено.")
            return

        await self.assignment_service.add_feedback(assignment_id, answer_text or "")

        user = await self.user_service.get_user(assignment.user_id)
        if not user:
            await admin_message.answer("❌ Пользователь не найден.")
            return

        feedback_message = f"💬 <b>Обратная связь по вашему заданию</b>\n\nДень {assignment.day_number}"
        if answer_text:
            feedback_message += f"\n\n{answer_text}"

        from core.config import Config
        from aiogram import Bot

        if not Config.COURSE_BOT_TOKEN:
            await admin_message.answer("❌ COURSE_BOT_TOKEN не настроен.")
            return

        followup_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📝 Отправить дополнение", callback_data=f"assignment:submit:lesson_{assignment.day_number}")],
            [InlineKeyboardButton(text="❓ Задать вопрос", callback_data=f"question:ask:lesson_{assignment.day_number}")],
            [InlineKeyboardButton(text="🧭 Навигатор", callback_data="navigator:open")],
        ])

        course_bot = Bot(token=Config.COURSE_BOT_TOKEN)
        try:
            if voice_file_id:
                await course_bot.send_voice(user.user_id, voice_file_id, caption=feedback_message, reply_markup=followup_kb)
            else:
                await course_bot.send_message(user.user_id, feedback_message, reply_markup=followup_kb)
            await self.assignment_service.mark_feedback_sent(assignment_id)
            await admin_message.answer("✅ Обратная связь отправлена пользователю в обучающий бот.")
        except Exception as e:
            logger.error(f"Error sending feedback to user: {e}", exc_info=True)
            await admin_message.answer(f"❌ Ошибка при отправке обратной связи: {e}")
        finally:
            await course_bot.session.close()
    
    async def _handle_assignment_reply(self, message: Message, reply_text: str, answer_text: str, voice_file_id: Optional[str] = None):
        """Handle reply to assignment."""
        # Extract assignment_id - try multiple formats
        assignment_id = None
        
        # Try "🔢 ID задания:" format (Russian)
        if "🔢 ID задания:" in reply_text or "ID задания:" in reply_text:
            try:
                parts = reply_text.split("ID задания:")
                if len(parts) > 1:
                    assignment_id_str = parts[1].split("\n")[0].strip()
                    assignment_id = int(assignment_id_str)
            except (ValueError, IndexError):
                pass
        
        # Try "Assignment ID:" format (English)
        if not assignment_id and "Assignment ID:" in reply_text:
            try:
                parts = reply_text.split("Assignment ID:")
                if len(parts) > 1:
                    assignment_id_str = parts[1].split("\n")[0].strip()
                    assignment_id = int(assignment_id_str)
            except (ValueError, IndexError):
                pass
        
        # Try extracting from callback data if available
        if not assignment_id and hasattr(message.reply_to_message, 'reply_markup'):
            if message.reply_to_message.reply_markup:
                for row in message.reply_to_message.reply_markup.inline_keyboard:
                    for button in row:
                        if button.callback_data and "admin_reply:" in button.callback_data:
                            try:
                                assignment_id = int(button.callback_data.split(":")[1])
                                break
                            except (ValueError, IndexError):
                                pass
        
        if not assignment_id:
            await message.answer("❌ Не удалось найти ID задания. Убедитесь, что вы отвечаете на сообщение с заданием.")
            return
        
        assignment = await self.assignment_service.get_assignment(assignment_id)
        if not assignment:
            await message.answer("❌ Задание не найдено.")
            return
        
        # Add feedback
        await self.assignment_service.add_feedback(assignment_id, answer_text)
        
        # Send feedback to user via course bot
        user = await self.user_service.get_user(assignment.user_id)
        if user:
            feedback_message = f"💬 <b>Обратная связь по вашему заданию</b>\n\nДень {assignment.day_number}"
            if answer_text:
                feedback_message += f"\n\n{answer_text}"
            
            # Send via course bot
            from core.config import Config
            from aiogram import Bot
            if not Config.COURSE_BOT_TOKEN:
                await message.answer("❌ COURSE_BOT_TOKEN не настроен.")
                return
            
            course_bot = Bot(token=Config.COURSE_BOT_TOKEN)
            try:
                followup_kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="📝 Отправить дополнение", callback_data=f"assignment:submit:lesson_{assignment.day_number}")],
                    [InlineKeyboardButton(text="❓ Задать вопрос", callback_data=f"question:ask:lesson_{assignment.day_number}")],
                    [InlineKeyboardButton(text="🧭 Навигатор", callback_data="navigator:open")],
                ])
                if voice_file_id:
                    await course_bot.send_voice(user.user_id, voice_file_id, caption=feedback_message, reply_markup=followup_kb)
                else:
                    await course_bot.send_message(user.user_id, feedback_message, reply_markup=followup_kb)
                await self.assignment_service.mark_feedback_sent(assignment_id)
                await message.answer("✅ Обратная связь отправлена пользователю в обучающий бот.")
            except Exception as e:
                logger.error(f"Error sending feedback to user: {e}", exc_info=True)
                await message.answer(f"❌ Ошибка при отправке обратной связи: {e}")
            finally:
                await course_bot.session.close()
        else:
            await message.answer("❌ Пользователь не найден.")
    
    async def _send_answer_to_user(
        self,
        user_id: int,
        answer_text: str,
        lesson_day: Optional[int] = None,
        bot_type: str = "course",
        voice_file_id: Optional[str] = None,
    ):
        """Send answer to user via appropriate bot."""
        from core.config import Config
        from aiogram import Bot

        def _course_followup_keyboard(day: Optional[int]) -> InlineKeyboardMarkup:
            rows: list[list[InlineKeyboardButton]] = []
            if day is not None:
                rows.append([
                    InlineKeyboardButton(
                        text="📝 Отправить дополнение",
                        callback_data=f"assignment:submit:lesson_{day}",
                    )
                ])
                rows.append([
                    InlineKeyboardButton(
                        text="❓ Задать вопрос",
                        callback_data=f"question:ask:lesson_{day}",
                    )
                ])
            rows.append([
                InlineKeyboardButton(text="🧭 Навигатор", callback_data="navigator:open")
            ])
            return InlineKeyboardMarkup(inline_keyboard=rows)
        
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
        if lesson_day is not None:
            answer_message += f"📚 Урок: День {lesson_day}\n\n"
        answer_message += (answer_text or "")
        
        try:
            reply_markup = _course_followup_keyboard(lesson_day) if bot_type != "sales" else None
            if voice_file_id:
                await target_bot.send_voice(user_id, voice_file_id, caption=answer_message, reply_markup=reply_markup)
            else:
                await target_bot.send_message(user_id, answer_message, reply_markup=reply_markup)
        finally:
            await target_bot.session.close()
    
    async def handle_reply_button(self, callback: CallbackQuery):
        """Handle reply button click."""
        await callback.answer()
        try:
            assignment_id = int(callback.data.split(":")[1])
        except Exception:
            await callback.message.answer("❌ Не удалось распознать ID задания.")
            return

        self._compose_reply[callback.from_user.id] = {
            "kind": "assignment",
            "assignment_id": assignment_id,
        }

        await callback.message.answer(
            "💬 <b>Ответ на задание</b>\n\n"
            "Отправьте обратную связь <b>одним сообщением</b> (текстом или голосовым).\n"
            "Можно <b>не отвечать</b> реплаем на исходное сообщение — просто отправьте сообщение сюда.\n\n"
            "Для отмены нажмите кнопку ниже.",
            reply_markup=self._compose_cancel_keyboard(),
        )
    
    async def handle_assignment_reply_callback(self, callback: CallbackQuery):
        """Handle assignment reply button."""
        await callback.answer()
        assignment_id = int(callback.data.split(":")[1])
        self._compose_reply[callback.from_user.id] = {
            "kind": "assignment",
            "assignment_id": assignment_id,
        }

        await callback.message.answer(
            f"💬 <b>Ответ на задание</b>\n\n"
            f"Assignment ID: {assignment_id}\n\n"
            "Отправьте обратную связь <b>одним сообщением</b> (текстом или голосовым).\n"
            "Можно <b>не отвечать</b> реплаем на исходное сообщение — просто отправьте сообщение сюда.",
            reply_markup=self._compose_cancel_keyboard(),
        )
    
    async def handle_question_reply_callback(self, callback: CallbackQuery):
        """Handle question reply button."""
        await callback.answer()
        parts = callback.data.split(":")
        user_id = int(parts[1])
        bot_type = "sales" if callback.data.startswith("reply_question:") else "course"

        if bot_type == "sales":
            lesson_day = None
        else:
            try:
                lesson_day = int(parts[2]) if len(parts) > 2 else None
            except Exception:
                lesson_day = None

        self._compose_reply[callback.from_user.id] = {
            "kind": "question",
            "user_id": user_id,
            "lesson_day": lesson_day,
            "bot_type": bot_type,
        }

        await callback.message.answer(
            "💬 <b>Ответ на вопрос</b>\n\n"
            f"👤 Пользователь ID: {user_id}\n"
            + (f"📚 Урок: День {lesson_day}\n" if lesson_day is not None else "")
            + f"📍 Источник: {'Продающий бот' if bot_type == 'sales' else 'Обучающий бот'}\n\n"
            "Отправьте ответ <b>одним сообщением</b> (текстом или голосовым).\n"
            "Можно <b>не отвечать</b> реплаем на исходное сообщение — просто отправьте сообщение сюда.\n\n"
            "Для отмены нажмите кнопку ниже.",
            reply_markup=self._compose_cancel_keyboard(),
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
    
    async def handle_restore_button(self, message: Message):
        """Handle restore button from keyboard - show list of 5 latest backups."""
        if not self.drive_sync or not self.drive_sync._admin_ready():
            await message.answer("❌ Синхронизация с Google Drive не настроена.")
            return
        
        await message.answer("⏪ Проверяю доступные бэкапы...")
        
        try:
            backups = self.drive_sync.get_all_backups()
            
            if not backups:
                await message.answer(
                    "❌ <b>Бэкапы не найдены</b>\n\n"
                    "Нет сохраненных версий для отката.\n"
                    "Бэкапы создаются автоматически при каждой синхронизации."
                )
                return
            
            # Show last 5 backups
            recent_backups = backups[:5]
            backup_info = f"📦 <b>Доступные бэкапы</b> (последние 5):\n\n"
            
            keyboard_buttons = []
            for i, (backup_path, backup_time) in enumerate(recent_backups):
                backup_info += f"{i+1}. 📅 {backup_time.strftime('%Y-%m-%d %H:%M:%S')}\n"
                keyboard_buttons.append([
                    InlineKeyboardButton(
                        text=f"⏪ Откатить к {backup_time.strftime('%d.%m %H:%M')}",
                        callback_data=f"admin:restore_confirm:{backup_path.name}"
                    )
                ])
            
            if len(backups) > 5:
                backup_info += f"\n... и еще {len(backups) - 5} бэкапов\n"
            
            backup_info += "\n⚠️ <b>Внимание:</b> Откат заменит текущую версию уроков на версию из выбранного бэкапа.\n"
            backup_info += "Выберите бэкап для отката:"
            
            # Add cancel button
            keyboard_buttons.append([
                InlineKeyboardButton(
                    text="❌ Отмена",
                    callback_data="admin:restore_cancel"
                )
            ])
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
            await message.answer(backup_info, reply_markup=keyboard)
        except Exception as e:
            logger.error(f"Error getting backups: {e}", exc_info=True)
            await message.answer(f"❌ Ошибка при получении бэкапов: {e}")
    
    async def handle_restore_confirm(self, callback: CallbackQuery):
        """Handle restore confirmation."""
        await callback.answer()
        
        try:
            backup_name = callback.data.split(":")[2]
            
            # Find backup by name
            backups = self.drive_sync.get_all_backups()
            backup_path = None
            for path, _ in backups:
                if path.name == backup_name:
                    backup_path = path
                    break
            
            if not backup_path or not backup_path.exists():
                await callback.message.answer("❌ Бэкап не найден.")
                return
            
            await callback.message.answer("⏪ Выполняю откат...")
            
            # Restore from backup
            import asyncio
            loop = asyncio.get_event_loop()
            success = await loop.run_in_executor(None, self.drive_sync.restore_from_backup, backup_path)
            
            if success:
                await callback.message.answer(
                    f"✅ <b>Откат выполнен успешно</b>\n\n"
                    f"📦 Восстановлен бэкап: {backup_name}\n"
                    f"💡 Курс-бот автоматически подхватит изменения при следующей загрузке уроков."
                )
            else:
                await callback.message.answer("❌ Ошибка при откате. Проверьте логи.")
        except Exception as e:
            logger.error(f"Error restoring from backup: {e}", exc_info=True)
            await callback.message.answer(f"❌ Ошибка при откате: {e}")
    
    async def handle_restore_cancel(self, callback: CallbackQuery):
        """Handle restore cancellation."""
        await callback.answer("Отменено")
        try:
            await callback.message.edit_text("✅ Откат отменен.")
        except Exception:
            await callback.message.answer("✅ Откат отменен.")
    
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
                    # Try to update user info from Telegram if missing
                    if not user.first_name and not user.username:
                        try:
                            await self._update_user_from_telegram(user)
                        except Exception as e:
                            logger.debug(f"Could not update user {user.user_id} from Telegram: {e}")
                    users.append(user)
        return users
    
    async def _update_user_from_telegram(self, user: User):
        """Try to get user info from Telegram API and update in database."""
        try:
            # Try to get user info from any available bot token
            from core.config import Config
            from aiogram import Bot
            
            # Try course bot first
            if Config.COURSE_BOT_TOKEN:
                bot = Bot(token=Config.COURSE_BOT_TOKEN)
                try:
                    chat_member = await bot.get_chat(user.user_id)
                    if chat_member:
                        user.first_name = getattr(chat_member, 'first_name', None) or user.first_name
                        user.last_name = getattr(chat_member, 'last_name', None) or user.last_name
                        user.username = getattr(chat_member, 'username', None) or user.username
                        await self.db.update_user(user)
                        logger.info(f"Updated user {user.user_id} info from Telegram")
                finally:
                    await bot.session.close()
        except Exception as e:
            logger.debug(f"Could not fetch user {user.user_id} from Telegram: {e}")
    
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
        
        display_name = self._format_user_display_name(user)
        return (
            f"👤 <b>{display_name}</b>\n"
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
        
        display_name = self._format_user_display_name(user)
        return (
            f"📊 <b>Детальная статистика пользователя</b>\n\n"
            f"👤 <b>{display_name}</b>\n"
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
