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
import re
from datetime import datetime
from pathlib import Path
from typing import Optional, Union
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, FSInputFile
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
from services.lesson_loader import LessonLoader
from utils.telegram_helpers import create_tariff_keyboard, create_programs_tariff_keyboard, format_tariff_description, create_persistent_keyboard
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

        # In-memory contexts (good enough for sales flow; DB stores the resulting email)
        self._awaiting_email: dict[int, dict] = {}
        self._awaiting_forget_confirm: set[int] = set()
        self._awaiting_promo: set[int] = set()
        # When enabled, all next messages from user are forwarded to curator group until stopped
        self._talk_mode_users: set[int] = set()
        # Remember action to continue after legal consent
        self._pending_after_legal: dict[int, dict] = {}
        # Tracks user's last selected program in sales flow ("online"/"offline")
        self._selected_program: dict[int, str] = {}
        # Test state: stores current test step and results
        self._test_state: dict[int, dict] = {}  # user_id -> {step: int, results: dict}
        
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
        self.dp.message.register(self.handle_menu, Command("menu"))
        self.dp.message.register(self.handle_author, Command("author"))
        # Bind curator/answers group (run inside target group)
        self.dp.message.register(self.handle_bind_sales_group, Command("bind_sales_group"))

        # Persistent keyboard buttons (sales bot)
        # IMPORTANT: register these BEFORE any generic text handler
        self.dp.message.register(self.handle_keyboard_upgrade, (F.text == "⬆️ Апгрейд тарифа") | (F.text == "🔷 Апгрейд тарифа"))
        self.dp.message.register(self.handle_keyboard_go_to_course, (F.text == "🧿 Перейти в курс") | (F.text == "📚 Перейти в курс"))
        self.dp.message.register(self.handle_keyboard_select_tariff, (F.text == "🗳️ Выбор тарифа") | (F.text == "🟦 Выбор тарифа") | (F.text == "📋 Выбор тарифа"))
        # Handle "Онлайн" button (with or without price in text)
        self.dp.message.register(self.handle_keyboard_online, F.text.startswith("Онлайн"))
        # Handle "Офлайн" button (with or without price in text)
        self.dp.message.register(self.handle_keyboard_offline, F.text.startswith("Офлайн"))
        self.dp.message.register(self.handle_keyboard_promo, F.text == "🎟 Промокод")
        self.dp.message.register(self.handle_keyboard_talk_to_human, (F.text == "💬 Поговорить с человеком") | (F.text == "🔵 Поговорить с человеком"))
        self.dp.message.register(
            self.handle_forget_everything_button,
            (F.text == "🧊 Забыть все") | (F.text == "🕶️ Забыть все") | (F.text == "Забыть все") | (F.text == "🧹 Забыть все") | (F.text == "🧹 Забыть всё")
        )

        # Voice questions in talk-to-human mode
        self.dp.message.register(self.handle_voice_question_from_sales, F.voice)

        # Test handlers (before email/promo to catch test responses)
        self.dp.callback_query.register(self.handle_test_skill_rating, F.data.startswith("test:skill:"))
        self.dp.callback_query.register(self.handle_test_time_selection, F.data.startswith("test:time:"))
        self.dp.callback_query.register(self.handle_test_mentor_setting, F.data.startswith("test:mentor:"))

        # Test time input (before email/promo)
        self.dp.message.register(self.handle_test_time_input, F.text & ~F.command)

        # Email input (receipt requirement)
        self.dp.message.register(self.handle_email_input, F.text & ~F.command)

        # Promo code input (should be BEFORE generic question handler)
        self.dp.message.register(self.handle_promo_input, F.text & ~F.command)

        # Questions from sales bot (generic text) - should be LAST among text handlers
        self.dp.message.register(self.handle_question_from_sales, F.text & ~F.command)
        
        # Регистрация обработчиков callback query
        # ВАЖНО: Порядок регистрации важен - более специфичные первыми
        # Регистрируем обработчики в порядке от более специфичных к менее специфичным
        # startswith проверки должны быть ПЕРЕД точными совпадениями
        
        # Обработчик для tariff: (должен быть первым среди startswith)
        self.dp.callback_query.register(self.handle_tariff_selection, F.data.startswith("tariff:"))
        
        # Обработчик для upgrade:
        self.dp.callback_query.register(self.handle_upgrade_tariff_selection, F.data.startswith("upgrade:"))
        
        # Free promo instant access (must be BEFORE generic handlers)
        self.dp.callback_query.register(self.handle_free_promo_choice, F.data.startswith("free_promo:"))
         
        # Обработчик для pay:
        self.dp.callback_query.register(self.handle_payment_initiate, F.data.startswith("pay:"))
        
        # Обработчик для check_payment:
        self.dp.callback_query.register(self.handle_payment_check, F.data.startswith("check_payment:"))

        # Legal consent (must be BEFORE generic handlers)
        self.dp.callback_query.register(self.handle_legal_accept, F.data == "legal:accept")

        # Forget everything (test)
        self.dp.callback_query.register(self.handle_forget_everything_confirm, F.data == "forget:confirm")
        self.dp.callback_query.register(self.handle_forget_everything_cancel, F.data == "forget:cancel")
        
        # Точные совпадения после startswith
        self.dp.callback_query.register(self.handle_upgrade_tariff, F.data == "upgrade_tariff")
        self.dp.callback_query.register(self.handle_back_to_tariffs, F.data == "back_to_tariffs")
        self.dp.callback_query.register(self.handle_cancel, F.data == "cancel")
        self.dp.callback_query.register(self.handle_talk_to_human, F.data == "sales:talk_to_human")
        self.dp.callback_query.register(self.handle_talk_to_human_stop, F.data == "sales:talk_to_human:stop")
        self.dp.callback_query.register(self.handle_show_tariffs_online, F.data == "sales:tariffs:online")
        self.dp.callback_query.register(self.handle_show_tariffs_offline, F.data == "sales:tariffs:offline")
        self.dp.callback_query.register(self.handle_offline_info, F.data == "sales:offline_info")
        self.dp.callback_query.register(self.handle_about_course, F.data == "sales:about_course")
        
        # Универсальный обработчик для отладки необработанных callback (должен быть последним)
        # Регистрируем БЕЗ фильтров, чтобы он ловил все остальное
        self.dp.callback_query.register(self.handle_unhandled_callback)

    def _legal_consent_text(self) -> str:
        offer = "https://docs.google.com/document/d/1TJKkr0A4YFpiY5NIG5mBJnhoY3BQzwMiee6zMnpC6OI/edit?usp=sharing"
        privacy = "https://docs.google.com/document/d/1INTWXjxfSH58sv51oYFeVOT6tXAd8iUMCqEPFXxEGrw/edit?usp=sharing"
        personal = "https://docs.google.com/document/d/1Yh1CzAf5s9ZexrfxLU2IaTr2ptgIC0n6cM9TFCvWwXw/edit?usp=sharing"
        return (
            "✔️ <b>Согласие</b>\n\n"
            "Нажимая кнопку ниже, вы соглашаетесь с "
            f"<a href='{offer}'>договором оферты</a> и "
            f"<a href='{privacy}'>политикой конфиденциальности</a>, "
            "а также даёте "
            f"<a href='{personal}'>согласие на обработку персональных данных</a>."
        )

    def _legal_consent_keyboard(self) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="☑️ Ознакомлен", callback_data="legal:accept")
        ]])

    @staticmethod
    def _format_payment_error(e: Exception) -> str:
        """
        Format a safe, user-visible payment error.
        Avoids leaking secrets; tries to include useful diagnostic hints like HTTP status code.
        """
        status = getattr(e, "status", None) or getattr(e, "status_code", None)
        # Some libs keep status on response
        resp = getattr(e, "response", None)
        if status is None and resp is not None:
            status = getattr(resp, "status", None) or getattr(resp, "status_code", None)
        name = type(e).__name__
        msg = str(e) or ""
        msg = msg.replace("\n", " ").strip()
        if len(msg) > 220:
            msg = msg[:220] + "…"
        if status:
            return f"{name} (HTTP {status}): {msg}" if msg else f"{name} (HTTP {status})"
        return f"{name}: {msg}" if msg else name

    def _receipt_required(self) -> bool:
        return str(getattr(Config, "YOOKASSA_RECEIPT_REQUIRED", "0")).strip() == "1"

    def _is_valid_email(self, email: str) -> bool:
        email = (email or "").strip()
        if len(email) < 5 or len(email) > 254:
            return False
        # Pragmatic validation; YooKassa requires a usable email.
        return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email))

    async def handle_email_input(self, message: Message):
        """Handle email input for YooKassa receipt."""
        user_id = message.from_user.id
        ctx = self._awaiting_email.get(user_id)
        if not ctx:
            raise SkipHandler()

        email = (message.text or "").strip()
        if not self._is_valid_email(email):
            await message.answer("📨 Введите корректный email для чека (пример: name@gmail.com)")
            return

        user = await self.user_service.get_or_create_user(
            user_id,
            message.from_user.username,
            message.from_user.first_name,
            message.from_user.last_name,
        )
        user.email = email
        await self.db.update_user(user)
        del self._awaiting_email[user_id]

        await message.answer("✅ Email сохранён. Создаю платёж…")

        kind = ctx.get("kind")
        if kind == "pay":
            prog = ctx.get("program")
            if prog in ("online", "offline"):
                self._selected_program[user_id] = prog
            tariff = Tariff(ctx["tariff"])
            await self._start_payment_flow(message, user, tariff)
            return
        if kind == "pay_offline":
            # Handle offline tariff payment
            tariff_str = ctx.get("tariff")
            OFFLINE_TARIFF_PRICES = {
                "slushatel": 6000.0,
                "aktivist": 12000.0,
                "media_persona": 22000.0,
                "glavnyi_geroi": 30000.0,
            }
            OFFLINE_TARIFF_NAMES = {
                "slushatel": "СЛУШАТЕЛЬ",
                "aktivist": "АКТИВИСТ",
                "media_persona": "МЕДИА-ПЕРСОНА",
                "glavnyi_geroi": "ГЛАВНЫЙ ГЕРОЙ",
            }
            if tariff_str not in OFFLINE_TARIFF_PRICES:
                await message.answer("❌ Ошибка: неверный офлайн тариф. Попробуйте снова.")
                return
            
            promo_code = await self._get_user_promo_code(user_id)
            offline_base_price = await self.db.get_offline_tariff_price(tariff_str, OFFLINE_TARIFF_PRICES[tariff_str])
            offline_price, promo = await self.payment_service._apply_promo_to_amount(offline_base_price, promo_code)
            offline_name = OFFLINE_TARIFF_NAMES[tariff_str]
            self._selected_program[user_id] = "offline"
            
            # Prepare metadata with email for receipt generation
            metadata = {
                "user_id": user_id,
                "tariff": f"offline_{tariff_str}",
                "tariff_name": offline_name,
                "course_program": "offline",
                "offline_tariff": "true",
                "customer_email": email  # For receipt generation
            }
            if promo:
                metadata["promo_code"] = promo.get("code")
                metadata["promo_discount_type"] = promo.get("discount_type")
                metadata["promo_discount_value"] = promo.get("discount_value")
                metadata["base_amount"] = offline_base_price
            
            # Create payment with correct arguments
            payment_info = await self.payment_processor.create_payment(
                user_id=user_id,
                amount=offline_price,
                currency=Config.PAYMENT_CURRENCY,
                description=f"Офлайн курс «Главный герой» - тариф {offline_name}",
                metadata=metadata
            )
            payment_id = payment_info.get("id") or payment_info.get("payment_id")
            payment_url = payment_info.get("confirmation", {}).get("confirmation_url") or payment_info.get("payment_url")
            
            payment_note = ""
            if Config.PAYMENT_PROVIDER.lower() == "mock":
                payment_note = "\n\n<i>Примечание: Это тестовая система оплаты. Платеж автоматически завершится через 5 секунд.</i>\n\nЧерез 5 секунд нажмите кнопку 'Проверить статус оплаты'."
            else:
                payment_note = "\n\n<i>После оплаты нажмите кнопку 'Проверить статус оплаты' для подтверждения.</i>"
            
            currency_symbol = "₽" if Config.PAYMENT_CURRENCY == "RUB" else Config.PAYMENT_CURRENCY
            
            await message.answer(
                f"💳 <b>Требуется оплата</b>\n\n"
                f"Программа: <b>офлайн · ГЛАВНЫЙ ГЕРОЙ</b>\n"
                f"Тариф: <b>{offline_name}</b>\n"
                + (f"🎟 Промокод: <code>{promo_code}</code>\n" if promo_code else "")
                + f"Сумма: {offline_price:.0f}{currency_symbol}\n\n"
                + f"Нажмите кнопку ниже для завершения оплаты:{payment_note}\n\n"
                + f"<i>Не забудьте после оплаты прислать свое имя в Телеграм на @niktatv, чтобы вас включили в рабочую группу.</i>",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="🏧 Оплатить",
                            url=payment_url
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="🔎 Проверить оплату",
                            callback_data=f"check_payment:{payment_id}"
                        )
                    ]
                ])
            )
            return
        if kind == "upgrade":
            # For upgrade we stored required fields
            current_tariff = Tariff(ctx["current_tariff"])
            new_tariff = Tariff(ctx["new_tariff"])
            upgrade_price = float(ctx["upgrade_price"])
            await self._start_upgrade_payment_flow(message, user, current_tariff, new_tariff, upgrade_price)
            return

        # Unknown context -> ignore
        raise SkipHandler()

    def _forget_confirm_keyboard(self) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✔️ Да, стереть всё", callback_data="forget:confirm"),
                InlineKeyboardButton(text="⬅️ Отмена", callback_data="forget:cancel"),
            ]
        ])

    def _agent_j_image_path(self) -> Path:
        # File is in repo under /logo. Prefer ASCII name for cross-platform compatibility.
        base = Path(__file__).resolve().parent.parent / "logo"
        candidate = base / "agent_j.png"
        if candidate.exists():
            return candidate
        # Fallback: any png in logo dir
        try:
            pngs = sorted(base.glob("*.png"))
            if pngs:
                return pngs[0]
        except Exception:
            pass
        return candidate

    async def handle_forget_everything_button(self, message: Message):
        """
        TEST BUTTON: wipes user access/progress and resets sales bot state.
        """
        user_id = message.from_user.id
        self._awaiting_forget_confirm.add(user_id)
        img_path = self._agent_j_image_path()
        caption = (
            "🧊\n\n"
            "<b>Забыть всё?</b>\n\n"
            "Это тестовая функция. Она удалит:\n"
            "• доступ/подписку\n"
            "• прогресс уроков\n"
            "• отправленные задания\n\n"
            "После этого всё начнётся сначала."
        )
        try:
            if img_path.exists():
                await message.answer_photo(
                    FSInputFile(str(img_path)),
                    caption=caption,
                    reply_markup=self._forget_confirm_keyboard()
                )
                return
        except Exception:
            pass

        # Fallback without image
        await message.answer(caption, reply_markup=self._forget_confirm_keyboard())

    async def handle_forget_everything_cancel(self, callback: CallbackQuery):
        try:
            await callback.answer("Отменено")
        except Exception:
            pass
        self._awaiting_forget_confirm.discard(callback.from_user.id)
        try:
            await callback.message.edit_text("✅ Ок, ничего не меняю.")
        except Exception:
            try:
                await callback.message.answer("✅ Ок, ничего не меняю.")
            except Exception:
                pass

    async def handle_forget_everything_confirm(self, callback: CallbackQuery):
        user_id = callback.from_user.id
        if user_id not in self._awaiting_forget_confirm:
            try:
                await callback.answer("Сначала нажмите «Забыть все»", show_alert=True)
            except Exception:
                pass
            return

        try:
            await callback.answer("Стираю…")
        except Exception:
            pass

        # Clear in-memory contexts for this user
        try:
            if hasattr(self, "_user_question_context") and user_id in self._user_question_context:
                del self._user_question_context[user_id]
        except Exception:
            pass
        try:
            if user_id in self._awaiting_email:
                del self._awaiting_email[user_id]
        except Exception:
            pass
        self._awaiting_forget_confirm.discard(user_id)

        # Wipe DB user data (affects both sales and course bots)
        await self.db.reset_user_data(user_id)
        # Verify
        remaining = await self.db.get_user(user_id)
        if remaining is not None:
            await callback.message.answer("❌ Не удалось сбросить данные (пользователь всё ещё в базе). Попробуйте ещё раз.")
            return
        # Also exit talk mode if active
        self._talk_mode_users.discard(user_id)

        # Send Agent J image + confirmation
        img_path = self._agent_j_image_path()
        try:
            if img_path.exists():
                await callback.message.answer_photo(
                    FSInputFile(str(img_path)),
                    caption="🕶️ Память стерта. Начинаем с нуля.\n\nНажмите /start"
                )
            else:
                await callback.message.answer("🕶️ Память стерта. Начинаем с нуля.\n\nНажмите /start")
        except Exception:
            await callback.message.answer("🕶️ Память стерта. Начинаем с нуля.\n\nНажмите /start")

    async def _normalize_curator_chat_id(self) -> Union[int, str]:
        """
        Normalize curator group ID from env (supports:
        - '-100123...'
        - '-123...' (web.telegram internal) -> converted to -100...
        - 'https://web.telegram.org/k/#-123...' -> converted
        - '@username')
        Default per user request: web.telegram.org/k/#-3576021889 -> -1003576021889
        """
        # Prefer runtime-bound group id if set (stored in DB)
        try:
            bound = await self.db.get_setting("sales_curator_group_id")
        except Exception:
            bound = None
        if bound:
            try:
                return int(bound)
            except Exception:
                pass

        raw = (Config.CURATOR_GROUP_ID or "").strip()
        if not raw:
            # fallback to the group provided by user
            return -1003576021889

        m = re.search(r"#-([0-9]{6,})", raw)
        if m:
            digits = m.group(1)
            return int(f"-100{digits}")

        if raw.startswith("-100") and raw[4:].isdigit():
            return int(raw)

        if raw.startswith("-") and raw[1:].isdigit():
            # If this looks like web.telegram internal id, convert to -100...
            digits = raw[1:]
            if len(digits) >= 9 and not raw.startswith("-100"):
                return int(f"-100{digits}")
            return int(raw)

        if raw.isdigit():
            return int(raw)

        return raw

    async def handle_bind_sales_group(self, message: Message):
        """
        Run this command inside the target group to bind it as the destination
        for "Поговорить с человеком" forwarding.
        """
        if message.chat.type == "private":
            await message.answer("Эту команду нужно отправить в группе, которую хотите привязать.")
            return

        chat_id = message.chat.id
        await self.db.set_setting("sales_curator_group_id", str(chat_id))
        await message.answer(f"✅ Группа привязана для продающего бота.\nchat_id: <code>{chat_id}</code>")

    def _talk_mode_keyboard(self) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⏺️ Завершить", callback_data="sales:talk_to_human:stop")]
        ])

    async def handle_keyboard_talk_to_human(self, message: Message):
        """Persistent keyboard: enter talk-to-human mode."""
        user_id = message.from_user.id

        # If user was in another "input" flow (promo/email), stop it so their next message
        # is treated as a support message and forwarded to PUP.
        self._awaiting_promo.discard(user_id)
        if user_id in self._awaiting_email:
            del self._awaiting_email[user_id]
        
        # Try sending a test message to admin bot (PUP) if configured
        from utils.admin_helpers import is_admin_bot_configured, send_to_admin_bot
        if not is_admin_bot_configured():
                await message.answer(
                    "❌ Сейчас поддержка недоступна: канал кураторов не настроен.\n\n"
                    "Проверьте переменные окружения: ADMIN_BOT_TOKEN и ADMIN_CHAT_ID."
                )
                return

        try:
            test_message = (
                f"🟢 <b>Новый диалог (sales bot)</b>\n"
                f"👤 {message.from_user.first_name or 'Пользователь'}"
                + (f" (@{message.from_user.username})" if message.from_user.username else "")
                + f"\n🆔 ID: {user_id}\n\n"
                f"📍 <b>Источник:</b> Продающий бот"
            )
            sent = await send_to_admin_bot(test_message)
            if not sent:
                raise RuntimeError("send_to_admin_bot returned False")
            logger.info(f"✅ Test message sent to admin bot (PUP) from sales bot user {user_id}")
        except Exception as e:
            logger.error(f"❌ Cannot send to admin bot (PUP): {e}", exc_info=True)
            await message.answer(
                "❌ Не могу отправить сообщение кураторам.\n\n"
                "Проверьте настройки ADMIN_BOT_TOKEN и ADMIN_CHAT_ID."
            )
            return

        self._talk_mode_users.add(user_id)

        await message.answer(
            "💬 <b>Поговорить с человеком</b>\n\n"
            "Напишите сообщение или отправьте голосовое — я перешлю кураторам.\n\n"
            "Чтобы завершить — нажмите «⏺️ Завершить».",
            reply_markup=self._talk_mode_keyboard()
        )

    async def handle_talk_to_human_stop(self, callback: CallbackQuery):
        try:
            await callback.answer("Готово")
        except Exception:
            pass
        user_id = callback.from_user.id
        self._talk_mode_users.discard(user_id)
        try:
            await callback.message.edit_text("✅ Диалог завершён. Можете продолжать пользоваться ботом.")
        except Exception:
            try:
                await callback.message.answer("✅ Диалог завершён. Можете продолжать пользоваться ботом.")
            except Exception:
                pass
        # Restore persistent keyboard after inline-only talk mode.
        try:
            online_min_price = await self.payment_service.get_tariff_base_price(Tariff.BASIC)
            persistent_keyboard = create_persistent_keyboard(
                online_min_price=online_min_price,
                offline_min_price=6000.0,
            )
            await callback.message.answer("\u200B", reply_markup=persistent_keyboard)
        except Exception:
            pass

    async def handle_voice_question_from_sales(self, message: Message):
        """Forward voice messages to admin bot (PUP) when talk-to-human mode is enabled."""
        user_id = message.from_user.id
        if user_id not in self._talk_mode_users:
            raise SkipHandler()

        first_name = message.from_user.first_name or "Пользователь"
        username = message.from_user.username
        header = (
            f"🎤 <b>Голосовое сообщение</b>\n\n"
            f"👤 Пользователь: {first_name}"
            + (f" (@{username})" if username else "")
            + f"\n🆔 ID: {user_id}\n\n"
            f"📍 <b>Источник:</b> Продающий бот"
        )

        # Send to admin bot (PUP) if configured
        # Check both token and chat_id (chat_id can be negative for groups, so check != 0)
        from utils.admin_helpers import is_admin_bot_configured, send_to_admin_bot
        if not is_admin_bot_configured():
            await message.answer(
                "❌ Сейчас поддержка недоступна: не настроен ПУП.\n\n"
                "Откройте ПУП и нажмите /start, либо проверьте переменные окружения: ADMIN_BOT_TOKEN и ADMIN_CHAT_ID.",
                reply_markup=self._talk_mode_keyboard()
            )
            return

        try:
            from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="💬 Ответить",
                        callback_data=f"reply_question:{user_id}:0"
                    )
                ]
            ])

            # Re-upload voice to PUP: file_id from sales bot is not valid for admin bot token.
            import io
            buf = io.BytesIO()
            await self.bot.download(message.voice, destination=buf)

            sent = await send_to_admin_bot(
                message_text=header,
                voice_bytes=buf.getvalue(),
                voice_filename="voice.ogg",
                reply_markup=keyboard
            )
            if not sent:
                raise RuntimeError("send_to_admin_bot returned False")

            await message.answer("✅ Голосовое отправлено кураторам.", reply_markup=self._talk_mode_keyboard())
            logger.info(f"✅ Voice question from sales bot sent to admin bot (PUP) from user {user_id}")
        except Exception as e:
            logger.error(f"Error sending voice to admin bot: {e}", exc_info=True)
            await message.answer(
                "❌ Не удалось отправить голосовое кураторам.\n\n"
                "Проверьте настройки ADMIN_BOT_TOKEN и ADMIN_CHAT_ID.",
                reply_markup=self._talk_mode_keyboard()
            )

    async def _start_payment_flow(self, message: Message, user, tariff: Tariff):
        """Create payment and show payment URL (non-upgrade)."""
        promo_code = await self._get_user_promo_code(user.user_id)
        payment_info = await self.payment_service.initiate_payment(
            user_id=user.user_id,
            tariff=tariff,
            referral_partner_id=user.referral_partner_id,
            customer_email=getattr(user, "email", None),
            course_program=self._selected_program.get(user.user_id),
            promo_code=promo_code,
        )
        payment_id = payment_info["payment_id"]
        payment_url = payment_info["payment_url"]

        payment_note = ""
        if Config.PAYMENT_PROVIDER.lower() == "mock":
            payment_note = "\n\n<i>Примечание: Это тестовая система оплаты. Платеж автоматически завершится через 5 секунд.</i>\n\nЧерез 5 секунд нажмите кнопку 'Проверить статус оплаты'."
        else:
            payment_note = "\n\n<i>После оплаты нажмите кнопку 'Проверить статус оплаты' для подтверждения.</i>"

        base_price = await self.payment_service.get_tariff_base_price(tariff)
        price, _ = await self.payment_service._apply_promo_to_amount(base_price, promo_code)
        currency_symbol = "₽" if Config.PAYMENT_CURRENCY == "RUB" else Config.PAYMENT_CURRENCY
        await message.answer(
            f"💳 <b>Требуется оплата</b>\n\n"
            f"Тариф: <b>{tariff.value.upper()}</b>\n"
            + (f"🎟 Промокод: <code>{promo_code}</code>\n" if promo_code else "")
            + f"Сумма: {price:.0f}{currency_symbol}\n\n"
            + f"Нажмите кнопку ниже для завершения оплаты:{payment_note}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🏧 Оплатить", url=payment_url)],
                    [InlineKeyboardButton(text="🔎 Проверить оплату", callback_data=f"check_payment:{payment_id}")],
            ])
        )

    async def _start_upgrade_payment_flow(self, message: Message, user, current_tariff: Tariff, new_tariff: Tariff, upgrade_price: float):
        """Create payment and show payment URL (upgrade)."""
        promo_code = await self._get_user_promo_code(user.user_id)
        upgrade_base_price = max(0.0, float(upgrade_price))
        upgrade_to_pay, _ = await self.payment_service._apply_promo_to_amount(upgrade_base_price, promo_code)

        payment_info = await self.payment_service.initiate_payment(
            user_id=user.user_id,
            tariff=new_tariff,
            referral_partner_id=user.referral_partner_id,
            customer_email=getattr(user, "email", None),
            upgrade_from=current_tariff,
            promo_code=promo_code,
            upgrade_price=upgrade_base_price,
        )
        payment_id = payment_info["payment_id"]
        payment_url = payment_info["payment_url"]

        currency_symbol = "₽" if Config.PAYMENT_CURRENCY == "RUB" else Config.PAYMENT_CURRENCY
        payment_note = "\n\n<i>После оплаты нажмите кнопку 'Проверить статус оплаты' для подтверждения.</i>"
        upgrade_message = (
            f"{create_premium_separator()}\n"
            f"💳 <b>ОПЛАТА АПГРЕЙДА ТАРИФА</b>\n"
            f"{create_premium_separator()}\n\n"
            f"Текущий тариф: <b>{current_tariff.value.upper()}</b>\n"
            f"Новый тариф: <b>{new_tariff.value.upper()}</b>\n\n"
            + (f"🎟 Промокод: <code>{promo_code}</code>\n" if promo_code else "")
            + f"💰 К доплате: <b>{upgrade_to_pay:.0f}{currency_symbol}</b>{payment_note}"
        )
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🏧 Оплатить", url=payment_url)],
            [InlineKeyboardButton(text="🔎 Проверить оплату", callback_data=f"check_payment:{payment_id}")],
            [InlineKeyboardButton(text="⬅️ Отмена", callback_data="cancel")],
        ])
        await message.answer(upgrade_message, reply_markup=keyboard)

    async def _ensure_legal_consent(self, chat_id: int, user_id: int) -> bool:
        """
        Returns True if legal consent already accepted; otherwise sends consent message and returns False.
        """
        user = await self.user_service.get_or_create_user(user_id)
        if getattr(user, "legal_accepted_at", None):
            return True
        await self.bot.send_message(
            chat_id,
            self._legal_consent_text(),
            reply_markup=self._legal_consent_keyboard(),
            disable_web_page_preview=True
        )
        return False

    async def handle_legal_accept(self, callback: CallbackQuery):
        """Handle legal consent acceptance."""
        try:
            await callback.answer()
        except Exception:
            pass

        user_id = callback.from_user.id
        user = await self.user_service.get_or_create_user(
            user_id,
            callback.from_user.username,
            callback.from_user.first_name,
            callback.from_user.last_name
        )
        user.legal_accepted_at = datetime.utcnow()
        await self.db.update_user(user)

        # Continue pending action if any
        pending = self._pending_after_legal.pop(user_id, None)
        if pending:
            kind = pending.get("kind")
            if kind == "pay":
                tariff_value = pending.get("tariff")
                program = pending.get("program")
                try:
                    tv = str(tariff_value)
                    # allow "online:basic" format (backward compatible)
                    if ":" in tv:
                        tv = tv.split(":")[-1]
                    tariff = Tariff(tv)
                except Exception:
                    tariff = None
                if tariff is not None:
                    # Ensure we have a user object for email/referral
                    user = await self.user_service.get_or_create_user(
                        user_id,
                        callback.from_user.username,
                        callback.from_user.first_name,
                        callback.from_user.last_name
                    )
                    if self._receipt_required() and not getattr(user, "email", None):
                        self._awaiting_email[user_id] = {"kind": "pay", "tariff": tariff.value, "program": program}
                        if program in ("online", "offline"):
                            self._selected_program[user_id] = program
                        await callback.message.answer(
                            "✅ Спасибо! Согласие принято.\n\n"
                            "✉️ Для оплаты нужен email для отправки чека.\n"
                            "Пожалуйста, отправьте ваш email одним сообщением (пример: name@gmail.com)."
                        )
                        return
                    if program in ("online", "offline"):
                        self._selected_program[user_id] = program
                    await callback.message.answer("✅ Спасибо! Согласие принято. Перехожу к оплате…")
                    await self._start_payment_flow(callback.message, user, tariff)
                    return

            if kind == "go_to_course":
                await callback.message.answer("✅ Спасибо! Согласие принято. Перехожу в курс…")
                await self.handle_keyboard_go_to_course(callback.message)
                return

            if kind == "free_promo":
                program = pending.get("program")
                tariff_key = pending.get("tariff")
                if program in ("online", "offline"):
                    self._selected_program[user_id] = program
                await callback.message.answer("✅ Спасибо! Согласие принято. Выдаю бесплатный доступ…")
                await self._process_free_promo_grant(
                    callback.message,
                    user_id,
                    str(program or ""),
                    str(tariff_key or ""),
                    tg_user=callback.from_user,
                )
                return

        # Default confirmation and next step
        # Confirm and give next step
        if user.has_access():
            keyboard = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="📚 Перейти в курс", url="https://t.me/StartNowAI_bot?start=course")
            ]])
            await callback.message.answer("✅ Спасибо! Согласие принято. Теперь вы можете перейти в курс 👇", reply_markup=keyboard)
        else:
            await callback.message.answer("✅ Спасибо! Согласие принято. Теперь можно выбрать тариф и оплатить курс 👇")
            # Show tariffs right away for convenience
            try:
                await self.handle_keyboard_select_tariff(callback.message)
            except Exception:
                pass
    
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
            # Log session start (non-blocking, don't fail if DB not ready)
            try:
                from datetime import datetime
                # Ensure DB is connected before logging
                await self.db._ensure_connection()
                await self.db.log_user_session(message.from_user.id, "sales", datetime.utcnow())
                await self.db.log_user_activity(message.from_user.id, "sales", "start", "main")
            except Exception as e:
                # Don't fail the request if logging fails
                logger.debug(f"Failed to log user activity (non-critical): {e}")
            
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
            except ValueError as e:
                # User limit reached
                logger.warning(f"User limit reached: {e}")
                await message.answer(
                    "❌ <b>Достигнут лимит пользователей</b>\n\n"
                    "К сожалению, в данный момент регистрация новых пользователей временно недоступна.\n\n"
                    "Попробуйте позже или свяжитесь с администратором."
                )
                return
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
                await self._show_program_tariff_menu(message)
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
                await self._show_program_tariff_menu(message)
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
            
            # No access -> show compact start menu (no duplicated long course info)
            logger.info("Showing program/tariff start menu...")
            persistent_keyboard = create_persistent_keyboard()
            await message.answer("Используйте кнопки внизу для навигации 👇", reply_markup=persistent_keyboard)
            await self._show_program_tariff_menu(message)
            logger.info("Program/tariff menu shown successfully")
        except Exception as e:
            logger.error(f"❌ Error in handle_start: {e}", exc_info=True)
            try:
                await message.answer("❌ Произошла ошибка. Попробуйте позже.")
            except Exception as send_error:
                logger.error(f"Error sending error message: {send_error}")

    async def _show_program_tariff_menu(self, message: Message):
        """Compact start menu: greeting + programs/tariffs (no long course description)."""
        text = (
            "Привет. Я помогу записаться на курс по искусству задавать вопросы и получать ответы.\n\n"
            "<b>Выбери программу и тариф:</b>"
        )
        keyboard = create_programs_tariff_keyboard()
        await send_animated_message(
            self.bot, 
            message.chat.id, 
            text, 
            keyboard, 
            typing_duration=0.8
        )

    async def handle_show_tariffs_online(self, callback: CallbackQuery):
        try:
            await callback.answer()
        except Exception:
            pass

        promo_code = await self._get_user_promo_code(callback.from_user.id)
        prices = await self._get_online_prices_for_user(callback.from_user.id)
        basic_price = prices.get(Tariff.BASIC, 0)
        feedback_price = prices.get(Tariff.FEEDBACK, 0)
        practic_price = prices.get(Tariff.PRACTIC, 0)

        # Show online tariffs + pay buttons (existing payment flow)
        text = (
            "💠 <b>онлайн · ВОПРОСЫ, КОТОРЫЕ МЕНЯЮТ ВСЁ</b> 💠\n\n"
            + (f"🎟 Промокод: <code>{promo_code}</code>\n\n" if promo_code else "")
            + "💎 <b>BASIC</b>\n"
            "<b>Что включено</b>\n"
            "30 занятий\n\n"
            "Ежедневные материалы (тексты, фото, видео, ссылки)\n\n"
            "Практические задания к каждому уроку\n\n"
            "Доступ к сообществу\n\n"
            "<b>Особенности</b>\n"
            "Полный доступ ко всему контенту\n\n"
            "Выполняйте задания в своем темпе\n\n"
            "Без обратной связи от лидера\n\n"
            f"💰 <b>{basic_price:.0f} ₽</b>\n\n"
            "⭐ <b>FEEDBACK</b>\n"
            "<b>Что включено</b>\n"
            "Всё из Базового тарифа\n\n"
            "Персональная обратная связь от лидера\n\n"
            "Проверка выполненных заданий\n\n"
            "Ответы на ваши вопросы\n\n"
            "<b>Особенности</b>\n"
            "Лидер проверяет ваши задания\n\n"
            "Персональные комментарии\n\n"
            "Можно задавать вопросы и получать ответы\n\n"
            f"💰 <b>{feedback_price:.0f} ₽</b>\n\n"
            "👑 <b>PRACTIC</b>\n"
            "<b>Что включено</b>\n"
            "Всё из тарифов Basic + Feedback\n\n"
            "Организация 3-х интервью онлайн\n\n"
            "Видеозапись 3-х интервью\n\n"
            "Профессиональный разбор 3-х интервью от лидера или куратора\n\n"
            "<b>Особенности</b>\n"
            "Каждое интервью до 15 мин\n\n"
            "Подбор собеседника\n\n"
            "Профессиональный формат\n\n"
            f"💰 <b>{practic_price:.0f} ₽</b>\n\n"
            "✨ <b>Выберите тариф для оплаты:</b>"
        )

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"💎 Оплатить BASIC · {int(basic_price)}₽", callback_data="pay:online:basic")],
            [InlineKeyboardButton(text=f"⭐ Оплатить FEEDBACK · {int(feedback_price)}₽", callback_data="pay:online:feedback")],
            [InlineKeyboardButton(text=f"👑 Оплатить PRACTIC · {int(practic_price)}₽", callback_data="pay:online:practic")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_tariffs")],
        ])
        await callback.message.answer(text, reply_markup=kb, disable_web_page_preview=True)

    async def handle_show_tariffs_offline(self, callback: CallbackQuery):
        try:
            await callback.answer()
        except Exception:
            pass

        promo_code = await self._get_user_promo_code(callback.from_user.id)
        prices = await self._get_offline_prices_for_user(callback.from_user.id)
        slushatel_price = prices.get("slushatel", 6000.0)
        aktivist_price = prices.get("aktivist", 12000.0)
        media_persona_price = prices.get("media_persona", 22000.0)
        glavnyi_geroi_price = prices.get("glavnyi_geroi", 30000.0)

        text = (
            "🎬 <b>офлайн · ГЛАВНЫЙ ГЕРОЙ</b> 🎬\n\n"
            + (f"🎟 Промокод: <code>{promo_code}</code>\n\n" if promo_code else "")
            + "👂 <b>СЛУШАТЕЛЬ</b>\n"
            "• Присутствие\n"
            "• Лекционная часть\n"
            "• Обсуждение\n"
            "• Нетворкинг\n"
            f"💰 <b>{int(slushatel_price)} ₽</b>\n\n"
            "🎯 <b>АКТИВИСТ</b>\n"
            "• Всё, что в прошлом тарифе\n"
            "• Берёт интервью как ведущий\n"
            "• Даёт интервью как спикер\n"
            "• Разбор от тренеров\n"
            f"💰 <b>{int(aktivist_price)} ₽</b>\n\n"
            "📹 <b>МЕДИА-ПЕРСОНА</b>\n"
            "• Всё, что в прошлом тарифе\n"
            "• Получает смонтированные видео\n"
            "• 2 видеоинтервью по 10-15 мин\n"
            f"💰 <b>{int(media_persona_price)} ₽</b>\n\n"
            "👑 <b>ГЛАВНЫЙ ГЕРОЙ</b>\n"
            "• Всё, что в прошлом тарифе\n"
            "• 10 рилсов для продвижения\n"
            "• Личная стратегическая онлайн-консультация\n"
            f"💰 <b>{int(glavnyi_geroi_price)} ₽</b>\n\n"
            "✨ <b>Выберите тариф для оплаты:</b>"
        )

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"👂 Оплатить СЛУШАТЕЛЬ · {int(slushatel_price)}₽", callback_data="pay:offline:slushatel")],
            [InlineKeyboardButton(text=f"🎯 Оплатить АКТИВИСТ · {int(aktivist_price)}₽", callback_data="pay:offline:aktivist")],
            [InlineKeyboardButton(text=f"📹 Оплатить МЕДИА-ПЕРСОНА · {int(media_persona_price)}₽", callback_data="pay:offline:media_persona")],
            [InlineKeyboardButton(text=f"👑 Оплатить ГЛАВНЫЙ ГЕРОЙ · {int(glavnyi_geroi_price)}₽", callback_data="pay:offline:glavnyi_geroi")],
            [InlineKeyboardButton(text="⬅️ Назад к описанию", callback_data="sales:offline_info")],
        ])
        await callback.message.answer(text, reply_markup=kb, disable_web_page_preview=True)
    
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

    async def handle_menu(self, message: Message):
        """Resend persistent keyboard (useful if user hid it)."""
        online_min_price = await self.payment_service.get_tariff_base_price(Tariff.BASIC)
        persistent_keyboard = create_persistent_keyboard(online_min_price=online_min_price, offline_min_price=6000.0)
        await message.answer("✅ Кнопки внизу включены.", reply_markup=persistent_keyboard)

    async def handle_keyboard_promo(self, message: Message):
        """Persistent keyboard: enter promo-code input mode."""
        user_id = message.from_user.id
        self._awaiting_promo.add(user_id)
        await message.answer(
            "🎟 <b>Промокод</b>\n\n"
            "Отправьте промокод одним сообщением.\n"
            "Чтобы сбросить промокод — отправьте <code>сброс</code>.",
        )

    async def handle_promo_input(self, message: Message):
        user_id = message.from_user.id
        # In talk-to-human mode any text must go to PUP, not be treated as a promo code.
        if user_id in self._talk_mode_users:
            raise SkipHandler()
        if user_id not in self._awaiting_promo:
            raise SkipHandler()

        text = (message.text or "").strip()
        if not text:
            await message.answer("❌ Пустой промокод.")
            return

        if text.lower() in ("сброс", "reset", "0", "отмена", "cancel"):
            await self.db.clear_user_promo_code(user_id)
            self._awaiting_promo.discard(user_id)
            await message.answer("✅ Промокод сброшен.")
            return

        code = text.strip().upper()
        promo = await self.db.get_valid_promo_code(code)
        if not promo:
            await message.answer("❌ Промокод не найден или неактивен.")
            return

        await self.db.set_user_promo_code(user_id, code)
        self._awaiting_promo.discard(user_id)

        discount_type = (promo.get("discount_type") or "").strip().lower()
        discount_value = float(promo.get("discount_value") or 0.0)
        disc = f"{discount_value:g}%" if discount_type == "percent" else f"{discount_value:g}"

        if self._is_free_access_promo(promo):
            await message.answer(
                "🎁 <b>Бесплатный доступ активирован</b>\n\n"
                f"Промокод: <code>{code}</code> (скидка -{disc})\n\n"
                "Выберите программу и тариф — доступ будет выдан сразу, без оплаты.",
                reply_markup=self._free_promo_keyboard(),
            )
            return

        await message.answer(f"✅ Промокод применён: <code>{code}</code> (скидка -{disc}).")

    @staticmethod
    def _is_free_access_promo(promo: dict) -> bool:
        discount_type = (promo.get("discount_type") or "").strip().lower()
        try:
            discount_value = float(promo.get("discount_value") or 0.0)
        except Exception:
            discount_value = 0.0
        return discount_type == "percent" and discount_value >= 100.0

    @staticmethod
    def _free_promo_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🎁 Онлайн · BASIC", callback_data="free_promo:online:basic")],
            [InlineKeyboardButton(text="🎁 Онлайн · FEEDBACK", callback_data="free_promo:online:feedback")],
            [InlineKeyboardButton(text="🎁 Онлайн · PRACTIC", callback_data="free_promo:online:practic")],
            [InlineKeyboardButton(text="🎁 Офлайн · СЛУШАТЕЛЬ", callback_data="free_promo:offline:slushatel")],
            [InlineKeyboardButton(text="🎁 Офлайн · АКТИВИСТ", callback_data="free_promo:offline:aktivist")],
            [InlineKeyboardButton(text="🎁 Офлайн · МЕДИА-ПЕРСОНА", callback_data="free_promo:offline:media_persona")],
            [InlineKeyboardButton(text="🎁 Офлайн · ГЛАВНЫЙ ГЕРОЙ", callback_data="free_promo:offline:glavnyi_geroi")],
            [InlineKeyboardButton(text="⬅️ Отмена", callback_data="cancel")],
        ])

    async def handle_free_promo_choice(self, callback: CallbackQuery):
        """Grant instant access for 100% promo codes (no payment)."""
        try:
            await callback.answer()
        except Exception:
            pass

        user_id = callback.from_user.id
        parts = (callback.data or "").split(":")
        if len(parts) < 3:
            await callback.message.answer("❌ Неверная команда.")
            return

        program = (parts[1] or "").strip().lower()
        tariff_key = (parts[2] or "").strip().lower()

        # Legal consent required before granting access
        if not await self._ensure_legal_consent(callback.message.chat.id, user_id):
            self._pending_after_legal[user_id] = {"kind": "free_promo", "program": program, "tariff": tariff_key}
            if program in ("online", "offline"):
                self._selected_program[user_id] = program
            return

        await self._process_free_promo_grant(callback.message, user_id, program, tariff_key, tg_user=callback.from_user)

    async def _process_free_promo_grant(
        self,
        message: Message,
        user_id: int,
        program: str,
        tariff_key: str,
        *,
        tg_user: Optional[object] = None,
    ):
        promo_code = await self._get_user_promo_code(user_id)
        if not promo_code:
            await message.answer("❌ Промокод не найден. Нажмите «🎟 Промокод» и введите код заново.")
            return

        promo = await self.db.get_valid_promo_code(promo_code)
        if not promo or not self._is_free_access_promo(promo):
            await message.answer("❌ Этот промокод не даёт бесплатный доступ.")
            return

        if program == "online":
            try:
                tariff = Tariff(tariff_key)
            except Exception:
                await message.answer("❌ Неверный тариф.")
                return
            if tariff == Tariff.PREMIUM:
                await message.answer("❌ Тариф PREMIUM отключён.")
                return

            base_price = await self.payment_service.get_tariff_base_price(tariff)
            final_price, _ = await self.payment_service._apply_promo_to_amount(base_price, promo_code)
            if final_price > 0.01:
                await message.answer("❌ Для выбранного тарифа промокод не даёт 100% скидку.")
                return

            try:
                ok = await self.db.increment_promo_code_use(promo_code)
                if not ok:
                    await self.db.clear_user_promo_code(user_id)
                    await message.answer("❌ Лимит использований промокода исчерпан.")
                    return
            except Exception:
                pass

            username = getattr(tg_user, "username", None) if tg_user is not None else None
            first_name = getattr(tg_user, "first_name", None) if tg_user is not None else None
            last_name = getattr(tg_user, "last_name", None) if tg_user is not None else None
            user = await self.user_service.get_or_create_user(user_id, username, first_name, last_name)
            is_upgrade = bool(user.has_access() and user.tariff != tariff)

            if not user.has_access():
                user = await self.user_service.grant_access(
                    user_id=user_id,
                    tariff=tariff,
                    referral_partner_id=user.referral_partner_id,
                )
            else:
                user.tariff = tariff
                await self.db.update_user(user)

            # Record analytics event (best-effort)
            try:
                from core.config import Config
                base_amount_f = float(base_price) if base_price is not None else None
                await self.db.record_payment_event(
                    payment_id=None,
                    user_id=int(user_id),
                    course_program="online",
                    tariff=tariff.value,
                    is_upgrade=is_upgrade,
                    base_amount=base_amount_f,
                    paid_amount=0.0,
                    currency=Config.PAYMENT_CURRENCY,
                    promo_code=promo_code,
                    promo_discount_type=promo.get("discount_type"),
                    promo_discount_value=promo.get("discount_value"),
                    promo_discount_amount=base_amount_f,
                    source="free_promo",
                )
            except Exception:
                pass

            try:
                await self.db.clear_user_promo_code(user_id)
            except Exception:
                pass

            await self._grant_access_and_notify(message, user, is_upgrade=is_upgrade)
            return

        if program == "offline":
            defaults = {
                "slushatel": 6000.0,
                "aktivist": 12000.0,
                "media_persona": 22000.0,
                "glavnyi_geroi": 30000.0,
            }
            if tariff_key not in defaults:
                await message.answer("❌ Неверный офлайн-тариф.")
                return

            base_price = await self.db.get_offline_tariff_price(tariff_key, defaults[tariff_key])
            final_price, _ = await self.payment_service._apply_promo_to_amount(base_price, promo_code)
            if final_price > 0.01:
                await message.answer("❌ Для выбранного тарифа промокод не даёт 100% скидку.")
                return

            try:
                ok = await self.db.increment_promo_code_use(promo_code)
                if not ok:
                    await self.db.clear_user_promo_code(user_id)
                    await message.answer("❌ Лимит использований промокода исчерпан.")
                    return
            except Exception:
                pass
            try:
                await self.db.clear_user_promo_code(user_id)
            except Exception:
                pass

            # Record analytics event (best-effort)
            try:
                from core.config import Config
                base_amount_f = float(base_price) if base_price is not None else None
                await self.db.record_payment_event(
                    payment_id=None,
                    user_id=int(user_id),
                    course_program="offline",
                    tariff=tariff_key,
                    is_upgrade=False,
                    base_amount=base_amount_f,
                    paid_amount=0.0,
                    currency=Config.PAYMENT_CURRENCY,
                    promo_code=promo_code,
                    promo_discount_type=promo.get("discount_type"),
                    promo_discount_value=promo.get("discount_value"),
                    promo_discount_amount=base_amount_f,
                    source="free_promo",
                )
            except Exception:
                pass

            names = {
                "slushatel": "СЛУШАТЕЛЬ",
                "aktivist": "АКТИВИСТ",
                "media_persona": "МЕДИА-ПЕРСОНА",
                "glavnyi_geroi": "ГЛАВНЫЙ ГЕРОЙ",
            }
            await message.answer(
                "✅ <b>Бесплатный доступ оформлен</b>\n\n"
                "Программа: <b>офлайн · ГЛАВНЫЙ ГЕРОЙ</b>\n"
                f"Тариф: <b>{names.get(tariff_key, tariff_key)}</b>\n\n"
                "<i>Напишите @niktatv, чтобы вас добавили в рабочую группу.</i>"
            )
            return

        await message.answer("❌ Неизвестная программа.")

    async def _get_user_promo_code(self, user_id: int) -> Optional[str]:
        code = (await self.db.get_user_promo_code(user_id) or "").strip()
        if not code:
            return None
        promo = await self.db.get_valid_promo_code(code)
        if not promo:
            try:
                await self.db.clear_user_promo_code(user_id)
            except Exception:
                pass
            return None
        return code

    async def _get_online_prices_for_user(self, user_id: int) -> dict[Tariff, float]:
        promo_code = await self._get_user_promo_code(user_id)
        out: dict[Tariff, float] = {}
        for t in [Tariff.BASIC, Tariff.FEEDBACK, Tariff.PRACTIC]:
            base = await self.payment_service.get_tariff_base_price(t)
            amount, _ = await self.payment_service._apply_promo_to_amount(base, promo_code)
            out[t] = amount
        return out

    async def _get_offline_prices_for_user(self, user_id: int) -> dict[str, float]:
        promo_code = await self._get_user_promo_code(user_id)
        defaults = {
            "slushatel": 6000.0,
            "aktivist": 12000.0,
            "media_persona": 22000.0,
            "glavnyi_geroi": 30000.0,
        }
        out: dict[str, float] = {}
        for k, default in defaults.items():
            base = await self.db.get_offline_tariff_price(k, default)
            amount, _ = await self.payment_service._apply_promo_to_amount(base, promo_code)
            out[k] = amount
        return out
    
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
        
        promo_code = await self._get_user_promo_code(message.from_user.id)
        if promo_code:
            final_message += f"\n\n🎟 Промокод применён: <code>{promo_code}</code>"

        prices = await self._get_online_prices_for_user(message.from_user.id)
        keyboard = create_tariff_keyboard(prices=prices)
        await send_animated_message(self.bot, message.chat.id, final_message, keyboard, 0.8)
    
    async def _show_upgrade_menu(self, message: Message, user, first_name: str):
        """Show tariff upgrade menu for user with access."""
        try:
            current_tariff = user.tariff
            current_price = await self.payment_service.get_tariff_base_price(current_tariff)
            
            # Определяем доступные тарифы для апгрейда
            available_upgrades = []
            if current_tariff == Tariff.BASIC:
                available_upgrades = [
                    (Tariff.FEEDBACK, await self.payment_service.get_tariff_base_price(Tariff.FEEDBACK))
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
                    text="⬅️ Отмена",
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
        logger.info(f"   Message ID: {callback.message.message_id if callback.message else 'None'}")
        logger.info("=" * 60)
        
        try:
            # Сначала отвечаем на callback, чтобы убрать индикатор загрузки
            try:
                await callback.answer()
                logger.info("   ✅ Callback answered successfully")
            except Exception as answer_error:
                logger.warning(f"   ⚠️ Не удалось ответить на callback (возможно устарел): {answer_error}")
                # Продолжаем выполнение, даже если не удалось ответить
            
            # Проверяем наличие callback.data
            if not callback.data:
                logger.error(f"   ❌ Callback data is None or empty")
                try:
                    await callback.message.answer("❌ Ошибка: данные не получены. Попробуйте снова.")
                except Exception as send_error:
                    logger.error(f"   ❌ Failed to send error message: {send_error}")
                return
            
            # Парсим тариф из callback data
            if ":" not in callback.data:
                logger.error(f"   ❌ Invalid callback data format: '{callback.data}' (no colon found)")
                try:
                    await callback.message.answer("❌ Ошибка: неверный формат данных. Попробуйте снова.")
                except Exception as send_error:
                    logger.error(f"   ❌ Failed to send error message: {send_error}")
                return
            
            # Supports both formats:
            # - tariff:<tariff>
            # - tariff:<program>:<tariff>
            raw = callback.data[len("tariff:"):].strip()
            program = None
            tariff_str = raw.strip().lower()
            if ":" in raw:
                maybe_program, maybe_tariff = raw.split(":", 1)
                program = maybe_program.strip().lower() or None
                tariff_str = maybe_tariff.strip().lower()
            logger.info(f"   Parsed program='{program}', tariff='{tariff_str}'")
            
            try:
                tariff = Tariff(tariff_str)
                logger.info(f"   ✅ Selected tariff: {tariff.value}")
            except ValueError as e:
                logger.error(f"   ❌ Invalid tariff value: '{tariff_str}', error: {e}")
                logger.error(f"   Available tariffs: {[t.value for t in Tariff]}")
                try:
                    await callback.message.answer(f"❌ Ошибка: неверный тариф '{tariff_str}'. Попробуйте снова.")
                except Exception as send_error:
                    logger.error(f"   ❌ Failed to send error message: {send_error}")
                return
            
            # Remember selected program for this user if provided
            if program in ("online", "offline"):
                self._selected_program[callback.from_user.id] = program

            # Ensure tariff has a valid configured price (DB override or default)
            try:
                await self.payment_service.get_tariff_base_price(tariff)
            except Exception:
                logger.warning(f"   ⚠️ Tariff {tariff.value} not priced/configured")
                try:
                    await callback.message.answer("❌ Этот тариф временно недоступен. Используйте /start для выбора тарифа.")
                except Exception as send_error:
                    logger.error(f"   ❌ Failed to send error message: {send_error}")
                return
            
            # Получаем или создаем пользователя
            user_id = callback.from_user.id
            user = None
            
            # Убеждаемся, что база данных подключена
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    # Проверяем подключение
                    if not hasattr(self.db, 'conn') or self.db.conn is None:
                        logger.info(f"   ⚠️ Database not connected (attempt {attempt + 1}), connecting...")
                        await self.db.connect()
                        logger.info(f"   ✅ Database connected")
                    
                    # Пробуем получить/создать пользователя
                    user = await self.user_service.get_or_create_user(
                        user_id,
                        callback.from_user.username,
                        callback.from_user.first_name,
                        callback.from_user.last_name
                    )
                    logger.info(f"   ✅ User retrieved/created: {user_id}")
                    break  # Успешно, выходим из цикла
                    
                except Exception as user_error:
                    logger.error(f"   ❌ Error getting/creating user (attempt {attempt + 1}): {user_error}", exc_info=True)
                    if attempt < max_retries - 1:
                        # Пробуем переподключиться
                        try:
                            if hasattr(self.db, 'conn') and self.db.conn:
                                try:
                                    await self.db.close()
                                except:
                                    pass
                        except:
                            pass
                        await asyncio.sleep(0.5)  # Небольшая задержка перед повтором
                        continue
                    else:
                        # Все попытки исчерпаны
                        logger.error(f"   ❌ All {max_retries} attempts failed")
                        try:
                            await callback.message.answer("❌ Ошибка при обработке запроса. Попробуйте позже.")
                        except:
                            pass
                        return
            
            if user is None:
                logger.error(f"   ❌ Failed to get/create user after {max_retries} attempts")
                try:
                    await callback.message.answer("❌ Ошибка при обработке запроса. Попробуйте позже.")
                except:
                    pass
                return
            
            # Show tariff details
            try:
                description = format_tariff_description(tariff)
                logger.info(f"   ✅ Tariff description generated for {tariff.value}")
            except Exception as desc_error:
                logger.error(f"   ❌ Error generating tariff description: {desc_error}", exc_info=True)
                description = f"📦 <b>Тариф: {tariff.value.upper()}</b>\n\n💳 Перейти к оплате?"
            
            # Создаем клавиатуру с кнопками
            try:
                prog = self._selected_program.get(callback.from_user.id)
                pay_cb = f"pay:{tariff.value}" if not prog else f"pay:{prog}:{tariff.value}"
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="🏧 Оплатить",
                            callback_data=pay_cb
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="🟦 Выбор тарифа",
                            callback_data="back_to_tariffs"
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="🔹 Отмена",
                            callback_data="cancel"
                        )
                    ]
                ])
                logger.info(f"   ✅ Keyboard created with callback_data: pay:{tariff.value}")
            except Exception as keyboard_error:
                logger.error(f"   ❌ Error creating keyboard: {keyboard_error}", exc_info=True)
                raise
            
            # Редактируем сообщение
            try:
                # Пробуем отредактировать сообщение
                try:
                    await callback.message.edit_text(
                        description + "\n\n💳 Перейти к оплате?",
                        reply_markup=keyboard
                    )
                    logger.info(f"   ✅ Message edited successfully for tariff {tariff.value}")
                except Exception as edit_error:
                    error_msg = str(edit_error).lower()
                    # Если сообщение не изменилось или другая ошибка редактирования
                    if "message is not modified" in error_msg or "message_not_modified" in error_msg:
                        logger.warning(f"   ⚠️ Message not modified (same content), sending new message")
                        # Отправляем новое сообщение вместо редактирования
                        await callback.message.answer(
                            description + "\n\n💳 Перейти к оплате?",
                            reply_markup=keyboard
                        )
                        logger.info(f"   ✅ New message sent instead of edit (message not modified)")
                    elif "message can't be edited" in error_msg or "message_to_edit_not_found" in error_msg:
                        logger.warning(f"   ⚠️ Message can't be edited, sending new message")
                        # Отправляем новое сообщение
                        await callback.message.answer(
                            description + "\n\n💳 Перейти к оплате?",
                            reply_markup=keyboard
                        )
                        logger.info(f"   ✅ New message sent instead of edit (can't edit)")
                    else:
                        # Другая ошибка - пробуем отправить новое сообщение
                        logger.error(f"   ❌ Error editing message: {edit_error}", exc_info=True)
                        await callback.message.answer(
                            description + "\n\n💳 Перейти к оплате?",
                            reply_markup=keyboard
                        )
                        logger.info(f"   ✅ New message sent instead of edit (error fallback)")
            except Exception as send_error:
                logger.error(f"   ❌ Failed to send/edit message: {send_error}", exc_info=True)
                # Пробуем отправить простое сообщение без клавиатуры
                try:
                    await callback.message.answer(
                        f"📦 <b>Тариф: {tariff.value.upper()}</b>\n\n"
                        f"{description}\n\n"
                        f"💳 Для оплаты используйте кнопку ниже или команду /start"
                    )
                    logger.info(f"   ✅ Fallback message sent")
                except Exception as final_error:
                    logger.error(f"   ❌ Final fallback failed: {final_error}", exc_info=True)
                    raise
                    
        except Exception as e:
            logger.error(f"❌ Error in handle_tariff_selection: {e}", exc_info=True)
            try:
                await callback.answer("❌ Ошибка при выборе тарифа", show_alert=True)
            except:
                # Если не удалось ответить на callback, пробуем отправить сообщение
                try:
                    if callback.message:
                        await callback.message.answer("❌ Ошибка при выборе тарифа. Попробуйте снова.")
                except Exception as final_error:
                    logger.error(f"   ❌ Final error handling failed: {final_error}", exc_info=True)
    
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
            await self._show_program_tariff_menu(callback.message)
            
        except Exception as e:
            logger.error(f"❌ Error in handle_back_to_tariffs: {e}", exc_info=True)
            try:
                await callback.answer("❌ Ошибка при загрузке тарифов", show_alert=True)
            except:
                try:
                    await callback.message.answer("❌ Ошибка при загрузке тарифов. Попробуйте позже.")
                except:
                    pass
    
    async def handle_unhandled_callback(self, callback: CallbackQuery):
        """Handle unhandled callback queries for debugging."""
        logger.warning("=" * 60)
        logger.warning("⚠️ UNHANDLED CALLBACK QUERY")
        logger.warning(f"   Callback data: '{callback.data}'")
        logger.warning(f"   Callback data type: {type(callback.data)}")
        logger.warning(f"   User ID: {callback.from_user.id}")
        logger.warning(f"   Username: @{callback.from_user.username}")
        logger.warning(f"   Message ID: {callback.message.message_id if callback.message else 'None'}")
        logger.warning("=" * 60)
        try:
            await callback.answer("⚠️ Эта кнопка пока не обрабатывается", show_alert=True)
        except Exception as e:
            logger.error(f"   Failed to answer callback: {e}")
    
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
            promo_code = await self._get_user_promo_code(user_id)
            current_price = await self.payment_service.get_tariff_base_price(current_tariff)
            
            # Определяем доступные тарифы для апгрейда
            available_upgrades: list[Tariff] = []
            if current_tariff == Tariff.BASIC:
                available_upgrades = [Tariff.FEEDBACK, Tariff.PRACTIC]
            elif current_tariff == Tariff.FEEDBACK:
                available_upgrades = [Tariff.PRACTIC]
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
                f"Ваш текущий тариф: <b>{current_tariff.value.upper()}</b> ({current_price:.0f}₽)\n"
                + (f"🎟 Промокод: <code>{promo_code}</code>\n" if promo_code else "")
                + "\n"
                + f"Доступные тарифы для апгрейда:\n\n"
            )
            
            # Создаем клавиатуру с доступными тарифами
            keyboard_buttons = []
            for tariff in available_upgrades:
                price = await self.payment_service.get_tariff_base_price(tariff)
                price_diff_base = max(0.0, float(price) - float(current_price))
                price_diff, _ = await self.payment_service._apply_promo_to_amount(price_diff_base, promo_code)
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
                    text="⬅️ Отмена",
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
            
            promo_code = await self._get_user_promo_code(user_id)
            current_price = await self.payment_service.get_tariff_base_price(current_tariff)
            new_price = await self.payment_service.get_tariff_base_price(new_tariff)
            price_diff_base = max(0.0, float(new_price) - float(current_price))
            price_diff, _ = await self.payment_service._apply_promo_to_amount(price_diff_base, promo_code)
            
            logger.info(f"   Current: {current_tariff.value} ({current_price}₽)")
            logger.info(f"   New: {new_tariff.value} ({new_price}₽)")
            logger.info(f"   Difference: {price_diff}₽ (base={price_diff_base}₽)")
            
            # Receipt/email required for some YooKassa shops
            if self._receipt_required() and not getattr(user, "email", None):
                self._awaiting_email[user_id] = {
                    "kind": "upgrade",
                    "current_tariff": current_tariff.value,
                    "new_tariff": new_tariff.value,
                    "upgrade_price": float(price_diff_base),
                }
                await callback.message.answer(
                    "✉️ Для оплаты нужен email для отправки чека.\n"
                    "Пожалуйста, отправьте ваш email одним сообщением (пример: name@gmail.com)."
                )
                return

            # Создаем платеж на разницу
            payment_info = await self.payment_service.initiate_payment(
                user_id=user_id,
                tariff=new_tariff,  # Новый тариф
                referral_partner_id=user.referral_partner_id,
                customer_email=getattr(user, "email", None),
                upgrade_from=current_tariff,  # Старый тариф для справки
                promo_code=promo_code,
                upgrade_price=price_diff_base,  # Базовая цена апгрейда (скидка применится внутри PaymentService)
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
                + (f"🎟 Промокод: <code>{promo_code}</code>\n" if promo_code else "")
                + f"💰 К доплате: <b>{price_diff:.0f}{currency_symbol}</b>{payment_note}"
            )
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🏧 Оплатить",
                        url=payment_url
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🔎 Проверить оплату",
                        callback_data=f"check_payment:{payment_id}"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="⬅️ Отмена",
                        callback_data="cancel"
                    )
                ]
            ])
            
            await callback.message.edit_text(upgrade_message, reply_markup=keyboard)
            
        except Exception as e:
            logger.error(f"❌ Error in handle_upgrade_tariff_selection: {e}", exc_info=True)
            safe_err = self._format_payment_error(e)
            try:
                await callback.answer(f"❌ Ошибка оплаты: {safe_err}", show_alert=True)
            except:
                try:
                    await callback.message.answer(
                        "❌ Ошибка при создании платежа.\n\n"
                        f"Диагностика: <code>{safe_err}</code>\n\n"
                        "Проверьте ключи YooKassa (Shop ID/Secret Key) и попробуйте ещё раз."
                    )
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

            # Legal consent required before payment
            if not await self._ensure_legal_consent(callback.message.chat.id, callback.from_user.id):
                # Remember desired payment action so we can continue after consent
                try:
                    # supports pay:<tariff> and pay:<program>:<tariff>
                    tariff_str_pending = callback.data[len("pay:"):]
                except Exception:
                    tariff_str_pending = None
                if tariff_str_pending:
                    prog = None
                    tv = tariff_str_pending.strip()
                    if ":" in tv:
                        prog, tv = tv.split(":", 1)
                        prog = (prog or "").strip().lower() or None
                        tv = (tv or "").strip().lower()
                    self._pending_after_legal[callback.from_user.id] = {"kind": "pay", "tariff": tv, "program": prog}
                    if prog in ("online", "offline"):
                        self._selected_program[callback.from_user.id] = prog
                return
            
            # supports pay:<tariff> and pay:<program>:<tariff>
            rest = callback.data[len("pay:"):]
            prog = None
            tariff_str = rest.strip()
            if ":" in rest:
                prog, tariff_str = rest.split(":", 1)
                prog = (prog or "").strip().lower() or None
                tariff_str = (tariff_str or "").strip().lower()
            
            # Offline tariffs mapping (not in Tariff enum)
            OFFLINE_TARIFF_PRICES = {
                "slushatel": 6000.0,
                "aktivist": 12000.0,
                "media_persona": 22000.0,
                "glavnyi_geroi": 30000.0,
            }
            
            OFFLINE_TARIFF_NAMES = {
                "slushatel": "СЛУШАТЕЛЬ",
                "aktivist": "АКТИВИСТ",
                "media_persona": "МЕДИА-ПЕРСОНА",
                "glavnyi_geroi": "ГЛАВНЫЙ ГЕРОЙ",
            }
            
            # Check if this is an offline tariff
            is_offline_tariff = False
            offline_price = None
            offline_name = None
            
            if prog == "offline" and tariff_str in OFFLINE_TARIFF_PRICES:
                is_offline_tariff = True
                offline_price = OFFLINE_TARIFF_PRICES[tariff_str]
                offline_name = OFFLINE_TARIFF_NAMES[tariff_str]
                self._selected_program[callback.from_user.id] = "offline"
            else:
                # Online tariff (from Tariff enum)
                try:
                    tariff = Tariff(tariff_str)
                    if prog in ("online", "offline"):
                        self._selected_program[callback.from_user.id] = prog
                except ValueError:
                    logger.error(f"   ❌ Invalid tariff: '{tariff_str}'")
                    await callback.message.answer(f"❌ Ошибка: неверный тариф '{tariff_str}'. Попробуйте снова.")
                    return
            
            user_id = callback.from_user.id
            user = await self.user_service.get_or_create_user(
                user_id,
                callback.from_user.username,
                callback.from_user.first_name,
                callback.from_user.last_name
            )
            
            if is_offline_tariff:
                logger.info(f"   Offline Tariff: {offline_name}, Price: {offline_price}, User: {user_id}")
                
                # Receipt/email required for some YooKassa shops
                if self._receipt_required() and not getattr(user, "email", None):
                    self._awaiting_email[user_id] = {"kind": "pay_offline", "tariff": tariff_str, "program": "offline"}
                    await callback.message.answer(
                        "✉️ Для оплаты нужен email для отправки чека.\n"
                        "Пожалуйста, отправьте ваш email одним сообщением (пример: name@gmail.com)."
                    )
                    return
                
                # For offline tariffs, we need to create a payment with a custom price
                # Since PaymentService expects a Tariff enum, we'll use a workaround
                # Create payment directly with payment processor
                payment_processor = self.payment_processor

                promo_code = await self._get_user_promo_code(user_id)
                offline_base_price = await self.db.get_offline_tariff_price(tariff_str, OFFLINE_TARIFF_PRICES[tariff_str])
                offline_price, promo = await self.payment_service._apply_promo_to_amount(offline_base_price, promo_code)
                
                # Prepare metadata
                metadata = {
                    "user_id": user_id,
                    "tariff": f"offline_{tariff_str}",
                    "tariff_name": offline_name,
                    "course_program": "offline",
                    "offline_tariff": "true"
                }
                if promo:
                    metadata["promo_code"] = promo.get("code")
                    metadata["promo_discount_type"] = promo.get("discount_type")
                    metadata["promo_discount_value"] = promo.get("discount_value")
                    metadata["base_amount"] = offline_base_price
                
                # Add email to metadata if available (for receipt generation)
                if getattr(user, "email", None):
                    metadata["customer_email"] = user.email
                
                # Create payment with correct arguments
                payment_info = await payment_processor.create_payment(
                    user_id=user_id,
                    amount=offline_price,
                    currency=Config.PAYMENT_CURRENCY,
                    description=f"Офлайн курс «Главный герой» - тариф {offline_name}",
                    metadata=metadata
                )
                payment_id = payment_info.get("id") or payment_info.get("payment_id")
                payment_url = payment_info.get("confirmation", {}).get("confirmation_url") or payment_info.get("payment_url")
                
                logger.info(f"   Offline payment created: {payment_id}")
                
                # Show payment information
                payment_note = ""
                if Config.PAYMENT_PROVIDER.lower() == "mock":
                    payment_note = "\n\n<i>Примечание: Это тестовая система оплаты. Платеж автоматически завершится через 5 секунд.</i>\n\nЧерез 5 секунд нажмите кнопку 'Проверить статус оплаты'."
                else:
                    payment_note = "\n\n<i>После оплаты нажмите кнопку 'Проверить статус оплаты' для подтверждения.</i>"
                
                currency_symbol = "₽" if Config.PAYMENT_CURRENCY == "RUB" else Config.PAYMENT_CURRENCY
                
                await callback.message.edit_text(
                    f"💳 <b>Требуется оплата</b>\n\n"
                    f"Программа: <b>офлайн · ГЛАВНЫЙ ГЕРОЙ</b>\n"
                    f"Тариф: <b>{offline_name}</b>\n"
                    + (f"🎟 Промокод: <code>{promo_code}</code>\n" if promo_code else "")
                    + f"Сумма: {offline_price:.0f}{currency_symbol}\n\n"
                    + f"Нажмите кнопку ниже для завершения оплаты:{payment_note}\n\n"
                    + f"<i>Не забудьте после оплаты прислать свое имя в Телеграм на @niktatv, чтобы вас включили в рабочую группу.</i>",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="🏧 Оплатить",
                                url=payment_url
                            )
                        ],
                        [
                            InlineKeyboardButton(
                                text="🔎 Проверить оплату",
                                callback_data=f"check_payment:{payment_id}"
                            )
                        ]
                    ])
                )
            else:
                # Online tariff (existing logic)
                logger.info(f"   Tariff: {tariff.value}, User: {user_id}")

                # Receipt/email required for some YooKassa shops
                if self._receipt_required() and not getattr(user, "email", None):
                    self._awaiting_email[user_id] = {"kind": "pay", "tariff": tariff.value, "program": self._selected_program.get(user_id)}
                    await callback.message.answer(
                        "✉️ Для оплаты нужен email для отправки чека.\n"
                        "Пожалуйста, отправьте ваш email одним сообщением (пример: name@gmail.com)."
                    )
                    return
                
                promo_code = await self._get_user_promo_code(user_id)

                # Initiate payment
                payment_info = await self.payment_service.initiate_payment(
                    user_id=user_id,
                    tariff=tariff,
                    referral_partner_id=user.referral_partner_id,
                    customer_email=getattr(user, "email", None),
                    course_program=self._selected_program.get(user_id),
                    promo_code=promo_code,
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
                
                base_price = await self.payment_service.get_tariff_base_price(tariff)
                price, _ = await self.payment_service._apply_promo_to_amount(base_price, promo_code)
                currency_symbol = "₽" if Config.PAYMENT_CURRENCY == "RUB" else Config.PAYMENT_CURRENCY
                
                await callback.message.edit_text(
                    f"💳 <b>Требуется оплата</b>\n\n"
                    f"Тариф: <b>{tariff.value.upper()}</b>\n"
                    + (f"🎟 Промокод: <code>{promo_code}</code>\n" if promo_code else "")
                    + f"Сумма: {price:.0f}{currency_symbol}\n\n"
                    + f"Нажмите кнопку ниже для завершения оплаты:{payment_note}",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="🏧 Оплатить",
                                url=payment_url
                            )
                        ],
                        [
                            InlineKeyboardButton(
                                text="🔎 Проверить оплату",
                                callback_data=f"check_payment:{payment_id}"
                            )
                        ]
                    ])
                )
            
            logger.info(f"   Payment message sent to user")
        except Exception as e:
            logger.error(f"❌ Error in handle_payment_initiate: {e}", exc_info=True)
            safe_err = self._format_payment_error(e)
            try:
                await callback.message.edit_text(
                    "❌ Ошибка при создании платежа.\n\n"
                    f"Диагностика: <code>{safe_err}</code>\n\n"
                    "Чаще всего это: требования YooKassa к чеку (receipt/54‑ФЗ) или неверные настройки магазина/ключей."
                )
            except Exception:
                try:
                    await callback.message.answer(
                        "❌ Ошибка при создании платежа.\n\n"
                        f"Диагностика: <code>{safe_err}</code>"
                    )
                except Exception:
                    pass
        
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
        
        # Enable talk-to-human mode (all next messages will be forwarded until stopped)
        self._talk_mode_users.add(user_id)
        
        await callback.message.answer(
            f"💬 <b>Поговорить с человеком</b>\n\n"
            f"👋 Привет, {first_name}!\n\n"
            f"✍️ Напишите сообщение или отправьте голосовое прямо здесь 👇\n\n"
            f"📤 Ваш вопрос будет отправлен куратору, и мы ответим вам как можно скорее ⚡\n\n"
            f"💡 <i>Можете задать любой вопрос о курсе, тарифах или оплате.</i>"
            ,
            reply_markup=self._talk_mode_keyboard()
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
        promo_code = await self._get_user_promo_code(user_id)
        current_price = await self.payment_service.get_tariff_base_price(current_tariff)
        
        # Определяем доступные тарифы для апгрейда
        available_upgrades: list[Tariff] = []
        if current_tariff == Tariff.BASIC:
            available_upgrades = [Tariff.FEEDBACK, Tariff.PRACTIC]
        elif current_tariff == Tariff.FEEDBACK:
            available_upgrades = [Tariff.PRACTIC]
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
            f"Ваш текущий тариф: <b>{current_tariff.value.upper()}</b> ({current_price:.0f}₽)\n"
            + (f"🎟 Промокод: <code>{promo_code}</code>\n" if promo_code else "")
            + "\n"
            + f"Доступные тарифы для апгрейда:\n\n"
        )
        
        # Создаем клавиатуру с доступными тарифами
        keyboard_buttons = []
        for tariff in available_upgrades:
            price = await self.payment_service.get_tariff_base_price(tariff)
            price_diff_base = max(0.0, float(price) - float(current_price))
            price_diff, _ = await self.payment_service._apply_promo_to_amount(price_diff_base, promo_code)
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
                text="⬅️ Отмена",
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

        # Legal consent required before entering course
        if not await self._ensure_legal_consent(message.chat.id, user_id):
            self._pending_after_legal[user_id] = {"kind": "go_to_course"}
            return
        
        await message.answer(
            "🚀 <b>Переход в курс</b>\n\n"
            "Нажмите на ссылку ниже, чтобы перейти в курс-бот:\n\n"
            "🤖 <a href='https://t.me/StartNowAI_bot?start=course'>@StartNowAI_bot</a>",
            disable_web_page_preview=False
        )
    
    async def handle_keyboard_select_tariff(self, message: Message):
        """Handle 'Выбор тарифа' button from persistent keyboard - show only tariff descriptions."""
        await self._show_program_tariff_menu(message)
    
    async def handle_keyboard_online(self, message: Message):
        """Handle 'Онлайн' button from persistent keyboard."""
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
    
    async def handle_keyboard_offline(self, message: Message):
        """Handle 'Офлайн' button from persistent keyboard."""
        await send_typing_action(self.bot, message.chat.id, 0.5)
        
        offline_description = (
            f"{create_premium_separator()}\n"
            f"🎬 <b>Главный герой</b>\n"
            f"медиа-практикум по интервью\n"
            f"📍 Москва · 2 дня живой практики · офлайн · камеры включены\n"
            f"{create_premium_separator()}\n\n"
            f"💡 <b>Это не лекция про «как надо»</b>\n"
            f"Это интенсив, где вы реально берёте и даёте интервью, попадаете в кадр, ошибаетесь, получаете разбор — и делаете лучше уже в тот же день.\n"
            f"{create_premium_separator()}\n\n"
            f"📌 <b>В итоге у вас:</b>\n"
            f"✅ прокачанный навык интервью\n"
            f"✅ опыт работы в реальных условиях\n"
            f"✅ готовый видеоконтент, который можно публиковать и использовать для продвижения\n"
            f"{create_premium_separator()}\n\n"
            f"🎯 <b>«Главный герой»</b> — это медиа-практикум с профессиональной съёмкой, где участники проходят путь от диалога к публичности. Вы не просто учитесь брать и давать интервью — вы становитесь героем собственного медиа-материала, работаете в кадре, получаете обратную связь и выходите с готовым контентом, который можно использовать дальше.\n\n"
            f"💬 Этот формат создан для тех, кому важно говорить и быть услышанным\n"
            f"{create_premium_separator()}\n\n"
            f"👥 <b>Для кого:</b>\n"
            f"💼 <b>Предприниматель</b> — чтобы улучшить навыки нетворкинга и управления\n"
            f"🎤 <b>Спикер, коуч, психолог</b> — чтобы быстро и детально распаковывать людей и информацию\n"
            f"💼 <b>Менеджер по продажам</b> — чтобы отточить искусство диалога и продавать больше\n"
            f"📚 <b>Преподаватель</b> — чтобы использовать вопросы как инструмент для достижения лучших результатов у учеников\n"
            f"🎙️ <b>Блогер/подкастер</b> — чтобы начать вести интервью или усовершенствовать навыки\n"
            f"📰 <b>Журналист/продюсер</b> — чтобы войти в мир СМИ\n"
            f"⭐ <b>Эксперт</b> — чтобы активнее расти и развиваться через правильные вопросы\n"
            f"💫 <b>Любой человек</b> — желающий сделать свои диалоги, а значит и жизнь, более насыщенными и интересными\n"
            f"{create_premium_separator()}\n\n"
            f"📋 <b>Как проходит практикум:</b>\n"
            f"✅ Краткая вводная и подготовка\n"
            f"✅ Серия интервью: 15 минут интервью → 15 минут профессионального разбора\n"
            f"✅ Вы поочерёдно выступаете в роли интервьюера и героя\n"
            f"✅ Обратная связь — сразу, по ходу процесса\n"
            f"✅ Повторная практика через неделю с учётом ошибок и роста\n"
            f"{create_premium_separator()}\n\n"
            f"🎥 <b>Формат работы:</b>\n"
            f"📍 Москва, очно\n"
            f"👥 Группа — до 8 активных участников\n"
            f"🎬 Оператор, 3 камеры, свет, звук\n"
            f"{create_premium_separator()}\n\n"
            f"💎 Живой практикум «Главный герой» — превращаем теорию интервью в работающую систему. Проверено на <b>3000+ интервью</b>.\n\n"
            f"📱 <b>Не забудьте после оплаты прислать свое имя в Телеграм на <a href='https://t.me/niktatv'>@niktatv</a></b>, чтобы вас включили в рабочую группу.\n"
            f"{create_premium_separator()}\n\n"
            f"🎯 <b>Почему этот формат работает. Вы:</b>\n"
            f"👁️ видите себя со стороны\n"
            f"👂 слышите, как реально звучите\n"
            f"🧠 понимаете, где теряется смысл, а где появляется глубина\n"
            f"🎬 исправляете это не «в голове», а в кадре\n\n"
            f"💎 <b>И главное</b> — вы выходите не только с навыком, но и с медиа-результатом.\n"
            f"{create_premium_separator()}\n\n"
            f"💰 <b>Тарифы:</b>\n\n"
            f"👂 <b>СЛУШАТЕЛЬ</b>\n"
            f"• Присутствие\n"
            f"• Лекционная часть\n"
            f"• Обсуждение\n"
            f"• Нетворкинг\n"
            f"💵 <b>6 000 ₽</b>\n\n"
            f"🎯 <b>АКТИВИСТ</b>\n"
            f"• Всё, что в прошлом тарифе\n"
            f"• Берёт интервью как ведущий\n"
            f"• Даёт интервью как спикер\n"
            f"• Разбор от тренеров\n"
            f"💵 <b>12 000 ₽</b>\n\n"
            f"📹 <b>МЕДИА-ПЕРСОНА</b>\n"
            f"• Всё, что в прошлом тарифе\n"
            f"• Получает смонтированные видео\n"
            f"• 2 видеоинтервью по 10-15 мин\n"
            f"💵 <b>22 000 ₽</b>\n\n"
            f"👑 <b>ГЛАВНЫЙ ГЕРОЙ</b>\n"
            f"• Всё, что в прошлом тарифе\n"
            f"• 10 рилсов для продвижения\n"
            f"• Личная стратегическая онлайн-консультация\n"
            f"💵 <b>30 000 ₽</b>\n"
            f"{create_premium_separator()}\n\n"
            f"👨‍🏫 С верой в ваши возможности, <b>Артём Никитин</b>\n"
            f"Разрабатываю идеи, создаю текстовый, аудио- и видеоконтент с 2000 года как телеведущий, журналист, диктор, киносценарист и режиссёр. Провёл <b>3000+ интервью</b>."
        )
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💎 Тарифы офлайн",
                    callback_data="sales:tariffs:offline"
                )
            ]
        ])
        
        await message.answer(offline_description, reply_markup=keyboard, disable_web_page_preview=False)
    
    async def handle_offline_info(self, callback: CallbackQuery):
        """Handle 'Назад к описанию' button from offline tariffs - show full offline course description."""
        try:
            await callback.answer()
        except Exception:
            pass
        
        # Use the same description as handle_keyboard_offline
        offline_description = (
            f"{create_premium_separator()}\n"
            f"🎬 <b>Главный герой</b>\n"
            f"медиа-практикум по интервью\n"
            f"📍 Москва · 2 дня живой практики · офлайн · камеры включены\n"
            f"{create_premium_separator()}\n\n"
            f"💡 <b>Это не лекция про «как надо»</b>\n"
            f"Это интенсив, где вы реально берёте и даёте интервью, попадаете в кадр, ошибаетесь, получаете разбор — и делаете лучше уже в тот же день.\n"
            f"{create_premium_separator()}\n\n"
            f"📌 <b>В итоге у вас:</b>\n"
            f"✅ прокачанный навык интервью\n"
            f"✅ опыт работы в реальных условиях\n"
            f"✅ готовый видеоконтент, который можно публиковать и использовать для продвижения\n"
            f"{create_premium_separator()}\n\n"
            f"🎯 <b>«Главный герой»</b> — это медиа-практикум с профессиональной съёмкой, где участники проходят путь от диалога к публичности. Вы не просто учитесь брать и давать интервью — вы становитесь героем собственного медиа-материала, работаете в кадре, получаете обратную связь и выходите с готовым контентом, который можно использовать дальше.\n\n"
            f"💬 Этот формат создан для тех, кому важно говорить и быть услышанным\n"
            f"{create_premium_separator()}\n\n"
            f"👥 <b>Для кого:</b>\n"
            f"💼 <b>Предприниматель</b> — чтобы улучшить навыки нетворкинга и управления\n"
            f"🎤 <b>Спикер, коуч, психолог</b> — чтобы быстро и детально распаковывать людей и информацию\n"
            f"💼 <b>Менеджер по продажам</b> — чтобы отточить искусство диалога и продавать больше\n"
            f"📚 <b>Преподаватель</b> — чтобы использовать вопросы как инструмент для достижения лучших результатов у учеников\n"
            f"🎙️ <b>Блогер/подкастер</b> — чтобы начать вести интервью или усовершенствовать навыки\n"
            f"📰 <b>Журналист/продюсер</b> — чтобы войти в мир СМИ\n"
            f"⭐ <b>Эксперт</b> — чтобы активнее расти и развиваться через правильные вопросы\n"
            f"💫 <b>Любой человек</b> — желающий сделать свои диалоги, а значит и жизнь, более насыщенными и интересными\n"
            f"{create_premium_separator()}\n\n"
            f"📋 <b>Как проходит практикум:</b>\n"
            f"✅ Краткая вводная и подготовка\n"
            f"✅ Серия интервью: 15 минут интервью → 15 минут профессионального разбора\n"
            f"✅ Вы поочерёдно выступаете в роли интервьюера и героя\n"
            f"✅ Обратная связь — сразу, по ходу процесса\n"
            f"✅ Повторная практика через неделю с учётом ошибок и роста\n"
            f"{create_premium_separator()}\n\n"
            f"🎥 <b>Формат работы:</b>\n"
            f"📍 Москва, очно\n"
            f"👥 Группа — до 8 активных участников\n"
            f"🎬 Оператор, 3 камеры, свет, звук\n"
            f"{create_premium_separator()}\n\n"
            f"💎 Живой практикум «Главный герой» — превращаем теорию интервью в работающую систему. Проверено на <b>3000+ интервью</b>.\n\n"
            f"📱 <b>Не забудьте после оплаты прислать свое имя в Телеграм на <a href='https://t.me/niktatv'>@niktatv</a></b>, чтобы вас включили в рабочую группу.\n"
            f"{create_premium_separator()}\n\n"
            f"🎯 <b>Почему этот формат работает. Вы:</b>\n"
            f"👁️ видите себя со стороны\n"
            f"👂 слышите, как реально звучите\n"
            f"🧠 понимаете, где теряется смысл, а где появляется глубина\n"
            f"🎬 исправляете это не «в голове», а в кадре\n\n"
            f"💎 <b>И главное</b> — вы выходите не только с навыком, но и с медиа-результатом.\n"
            f"{create_premium_separator()}\n\n"
            f"💰 <b>Тарифы:</b>\n\n"
            f"👂 <b>СЛУШАТЕЛЬ</b>\n"
            f"• Присутствие\n"
            f"• Лекционная часть\n"
            f"• Обсуждение\n"
            f"• Нетворкинг\n"
            f"💵 <b>6 000 ₽</b>\n\n"
            f"🎯 <b>АКТИВИСТ</b>\n"
            f"• Всё, что в прошлом тарифе\n"
            f"• Берёт интервью как ведущий\n"
            f"• Даёт интервью как спикер\n"
            f"• Разбор от тренеров\n"
            f"💵 <b>12 000 ₽</b>\n\n"
            f"📹 <b>МЕДИА-ПЕРСОНА</b>\n"
            f"• Всё, что в прошлом тарифе\n"
            f"• Получает смонтированные видео\n"
            f"• 2 видеоинтервью по 10-15 мин\n"
            f"💵 <b>22 000 ₽</b>\n\n"
            f"👑 <b>ГЛАВНЫЙ ГЕРОЙ</b>\n"
            f"• Всё, что в прошлом тарифе\n"
            f"• 10 рилсов для продвижения\n"
            f"• Личная стратегическая онлайн-консультация\n"
            f"💵 <b>30 000 ₽</b>\n"
            f"{create_premium_separator()}\n\n"
            f"👨‍🏫 С верой в ваши возможности, <b>Артём Никитин</b>\n"
            f"Разрабатываю идеи, создаю текстовый, аудио- и видеоконтент с 2000 года как телеведущий, журналист, диктор, киносценарист и режиссёр. Провёл <b>3000+ интервью</b>."
        )
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💎 Тарифы офлайн",
                    callback_data="sales:tariffs:offline"
                )
            ]
        ])
        
        try:
            await callback.message.edit_text(offline_description, reply_markup=keyboard, disable_web_page_preview=False)
        except Exception:
            # If edit fails, send new message
            await callback.message.answer(offline_description, reply_markup=keyboard, disable_web_page_preview=False)
    
    async def handle_question_from_sales(self, message: Message):
        """Handle question text from sales bot (when user clicked 'Talk to human')."""
        user_id = message.from_user.id

        # Only handle when talk-to-human mode is enabled
        if user_id not in self._talk_mode_users:
            raise SkipHandler()
        
        # Форматируем вопрос для ПУП
        question_data = await self.question_service.create_question(
            user_id=user_id,
            lesson_id=None,
            question_text=message.text,
            context="Вопрос из продающего бота"
        )
        admin_message = await self.question_service.format_question_for_admin(question_data)
        admin_message += "\n\n📍 <b>Источник:</b> Продающий бот (sales bot)"
        
        # Log question activity
        try:
            await self.db.log_user_activity(user_id, "sales", "question", "support")
        except Exception:
            pass
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💬 Ответить",
                    callback_data=f"reply_question:{user_id}:0"
                )
            ]
        ])
        
        # Send to admin bot (PUP) if configured
        # Check both token and chat_id (chat_id can be negative for groups, so check != 0)
        from utils.admin_helpers import is_admin_bot_configured, send_to_admin_bot
        if not is_admin_bot_configured():
            await message.answer(
                "❌ Сейчас поддержка недоступна: не настроен ПУП.\n\n"
                "Откройте ПУП и нажмите /start, либо проверьте переменные окружения: ADMIN_BOT_TOKEN и ADMIN_CHAT_ID.",
                reply_markup=self._talk_mode_keyboard()
            )
            return

        try:
            sent = await send_to_admin_bot(admin_message, reply_markup=keyboard)
            if not sent:
                raise RuntimeError("send_to_admin_bot returned False")
            logger.info(f"✅ Question from sales bot sent to admin bot (PUP) from user {user_id}")
        except Exception as e:
            logger.error(f"Error sending to admin bot: {e}", exc_info=True)
            await message.answer(
                "❌ Не удалось отправить сообщение кураторам.\n\n"
                "Проверьте настройки ADMIN_BOT_TOKEN и ADMIN_CHAT_ID.",
                reply_markup=self._talk_mode_keyboard()
            )
            return
        
        await message.answer(
            "✅ <b>Сообщение отправлено!</b>\n\n"
            "📤 Я переслал сообщение кураторам 👥.\n"
            "⏳ Мы ответим вам как можно скорее.\n\n"
            "Чтобы завершить диалог — нажмите «✅ Завершить».",
            reply_markup=self._talk_mode_keyboard()
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

                # Offline payments are created directly via payment_processor with custom metadata.
                # They are not processed by PaymentService (Tariff enum doesn't include offline_*).
                payment_details = None
                metadata = {}
                try:
                    if hasattr(self.payment_processor, "get_payment_details"):
                        payment_details = await self.payment_processor.get_payment_details(payment_id)
                except Exception:
                    payment_details = None

                if isinstance(payment_details, dict):
                    metadata = (
                        payment_details.get("metadata")
                        or (payment_details.get("payment") or {}).get("metadata")
                        or {}
                    )

                is_offline = False
                try:
                    offline_flag = str(metadata.get("offline_tariff", "")).strip().lower()
                    tariff_key = str(metadata.get("tariff", "")).strip().lower()
                    is_offline = offline_flag in ("true", "1", "yes") or tariff_key.startswith("offline_")
                except Exception:
                    is_offline = False

                if is_offline:
                    offline_name = metadata.get("tariff_name") or "ОФЛАЙН"
                    promo_code = str(metadata.get("promo_code") or "").strip()
                    safe_user_id = int(metadata.get("user_id") or callback.from_user.id)

                    if promo_code:
                        try:
                            await self.db.increment_promo_code_use(promo_code)
                        except Exception:
                            logger.warning("Failed to increment promo usage for offline payment", exc_info=True)
                        try:
                            await self.db.clear_user_promo_code(safe_user_id)
                        except Exception:
                            logger.warning("Failed to clear user promo code for offline payment", exc_info=True)

                    await callback.message.edit_text(
                        "✅ <b>Оплата подтверждена!</b>\n\n"
                        "Программа: <b>офлайн · ГЛАВНЫЙ ГЕРОЙ</b>\n"
                        f"Тариф: <b>{offline_name}</b>\n"
                        + (f"🎟 Промокод: <code>{promo_code}</code>\n" if promo_code else "")
                        + "\n"
                        + "<i>Не забудьте после оплаты прислать свое имя в Телеграм на @niktatv, чтобы вас включили в рабочую группу.</i>"
                    )
                    return

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
                                text="🔎 Проверить оплату",
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
    
    async def _start_test(self, message: Message, user):
        """Start onboarding test for new user."""
        self._test_state[user.user_id] = {
            "step": 1,
            "results": {}
        }
        await self._show_test_step_1(message, user)
    
    async def _show_test_step_1(self, message: Message, user):
        """Show step 1: Skills assessment."""
        text = (
            "📋 <b>ТЕСТИРОВАНИЕ</b>\n\n"
            "Помогите нам лучше настроить курс под вас!\n\n"
            "<b>Этап 1 из 3: Оценка компетенций</b>\n\n"
            "Оцените свои навыки от 1 до 5:\n\n"
            "1️⃣ <b>Умение задавать вопросы</b>"
        )
        buttons = []
        row = []
        for i in range(1, 6):
            row.append(InlineKeyboardButton(
                text=str(i),
                callback_data=f"test:skill:asking:{i}"
            ))
            if len(row) == 5:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
        await message.answer(text, reply_markup=keyboard)
    
    async def handle_test_skill_rating(self, callback: CallbackQuery):
        """Handle skill rating selection."""
        try:
            await callback.answer()
        except:
            pass
        
        user_id = callback.from_user.id
        if user_id not in self._test_state:
            await callback.message.answer("❌ Тестирование не начато.")
            return
        
        parts = callback.data.split(":")
        skill_type = parts[2]  # "asking", "answering", "listening"
        rating = int(parts[3])
        
        state = self._test_state[user_id]
        state["results"][skill_type] = rating
        
        # Move to next skill or next step
        if skill_type == "asking":
            # Show next skill: answering
            text = (
                "📋 <b>ТЕСТИРОВАНИЕ</b>\n\n"
                "<b>Этап 1 из 3: Оценка компетенций</b>\n\n"
                "2️⃣ <b>Умение отвечать на вопросы</b>"
            )
            buttons = []
            row = []
            for i in range(1, 6):
                row.append(InlineKeyboardButton(
                    text=str(i),
                    callback_data=f"test:skill:answering:{i}"
                ))
                if len(row) == 5:
                    buttons.append(row)
                    row = []
            if row:
                buttons.append(row)
            keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
            await callback.message.edit_text(text, reply_markup=keyboard)
        
        elif skill_type == "answering":
            # Show next skill: listening
            text = (
                "📋 <b>ТЕСТИРОВАНИЕ</b>\n\n"
                "<b>Этап 1 из 3: Оценка компетенций</b>\n\n"
                "3️⃣ <b>Умение слушать и слышать собеседника</b>"
            )
            buttons = []
            row = []
            for i in range(1, 6):
                row.append(InlineKeyboardButton(
                    text=str(i),
                    callback_data=f"test:skill:listening:{i}"
                ))
                if len(row) == 5:
                    buttons.append(row)
                    row = []
            if row:
                buttons.append(row)
            keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
            await callback.message.edit_text(text, reply_markup=keyboard)
        
        elif skill_type == "listening":
            # Move to step 2: time selection
            state["step"] = 2
            await self._show_test_step_2(callback.message, user_id)
    
    async def _show_test_step_2(self, message: Message, user_id: int):
        """Show step 2: Time selection."""
        text = (
            "📋 <b>ТЕСТИРОВАНИЕ</b>\n\n"
            "<b>Этап 2 из 3: Настройка времени</b>\n\n"
            "Выберите удобное время для получения новых заданий:"
        )
        buttons = []
        popular_times = ["06:00", "07:00", "08:00", "08:30", "09:00", "10:00", "12:00", "18:00", "20:00"]
        row = []
        for time_str in popular_times:
            row.append(InlineKeyboardButton(
                text=time_str,
                callback_data=f"test:time:lesson:{time_str}"
            ))
            if len(row) == 3:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
        
        buttons.append([InlineKeyboardButton(
            text="✏️ Ввести своё время",
            callback_data="test:time:lesson:custom"
        )])
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
        await message.answer(text, reply_markup=keyboard)
    
    async def handle_test_time_selection(self, callback: CallbackQuery):
        """Handle time selection in test."""
        try:
            await callback.answer()
        except:
            pass
        
        user_id = callback.from_user.id
        if user_id not in self._test_state:
            await callback.message.answer("❌ Тестирование не начато.")
            return
        
        parts = callback.data.split(":")
        time_type = parts[2]  # "lesson" or "reminder_start" or "reminder_end"
        
        if time_type == "lesson":
            if parts[3] == "custom":
                self._test_state[user_id]["awaiting_time"] = "lesson"
                await callback.message.answer(
                    "⏰ Введите время получения заданий в формате ЧЧ:ММ (например, 09:30):"
                )
                return
            else:
                time_str = parts[3]
                self._test_state[user_id]["results"]["lesson_time"] = time_str
                # Show reminder time selection (always ask, will be used if persistence > 0)
                await self._show_test_step_2_reminder_start(callback.message, user_id)
        
        elif time_type == "reminder_start":
            if parts[3] == "custom":
                self._test_state[user_id]["awaiting_time"] = "reminder_start"
                await callback.message.answer(
                    "🕐 Введите время начала уведомлений в формате ЧЧ:ММ (например, 09:30):"
                )
                return
            else:
                time_str = parts[3]
                self._test_state[user_id]["results"]["reminder_start"] = time_str
                # Show reminder end selection
                await self._show_test_step_2_reminder_end(callback.message, user_id)
        
        elif time_type == "reminder_end":
            if parts[3] == "custom":
                self._test_state[user_id]["awaiting_time"] = "reminder_end"
                await callback.message.answer(
                    "🕐 Введите время конца уведомлений в формате ЧЧ:ММ (например, 22:00):"
                )
                return
            else:
                time_str = parts[3]
                self._test_state[user_id]["results"]["reminder_end"] = time_str
                # Move to step 3: mentor settings
                self._test_state[user_id]["step"] = 3
                await self._show_test_step_3(callback.message, user_id)
    
    async def _show_test_step_2_reminder_start(self, message: Message, user_id: int):
        """Show reminder start time selection."""
        text = (
            "📋 <b>ТЕСТИРОВАНИЕ</b>\n\n"
            "<b>Этап 2 из 3: Настройка времени</b>\n\n"
            "Выберите время начала промежутка уведомлений:"
        )
        buttons = []
        popular_times = ["09:00", "09:30", "10:00", "11:00", "12:00"]
        row = []
        for time_str in popular_times:
            row.append(InlineKeyboardButton(
                text=time_str,
                callback_data=f"test:time:reminder_start:{time_str}"
            ))
            if len(row) == 3:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
        
        buttons.append([InlineKeyboardButton(
            text="✏️ Ввести своё время",
            callback_data="test:time:reminder_start:custom"
        )])
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
        await message.answer(text, reply_markup=keyboard)
    
    async def _show_test_step_2_reminder_end(self, message: Message, user_id: int):
        """Show reminder end time selection."""
        text = (
            "📋 <b>ТЕСТИРОВАНИЕ</b>\n\n"
            "<b>Этап 2 из 3: Настройка времени</b>\n\n"
            "Выберите время конца промежутка уведомлений:"
        )
        buttons = []
        popular_times = ["18:00", "19:00", "20:00", "21:00", "22:00", "23:00"]
        row = []
        for time_str in popular_times:
            row.append(InlineKeyboardButton(
                text=time_str,
                callback_data=f"test:time:reminder_end:{time_str}"
            ))
            if len(row) == 3:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
        
        buttons.append([InlineKeyboardButton(
            text="✏️ Ввести своё время",
            callback_data="test:time:reminder_end:custom"
        )])
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
        await message.answer(text, reply_markup=keyboard)
    
    async def _show_test_step_3(self, message: Message, user_id: int):
        """Show step 3: Mentor settings."""
        text = (
            "📋 <b>ТЕСТИРОВАНИЕ</b>\n\n"
            "<b>Этап 3 из 3: Настройка наставника</b>\n\n"
            "Выберите характеристики наставника:\n\n"
            "1️⃣ <b>Настойчивость</b> (0-5):\n"
            "Насколько настойчиво наставник должен напоминать о заданиях?"
        )
        buttons = []
        row = []
        for i in range(6):  # 0-5
            row.append(InlineKeyboardButton(
                text=str(i),
                callback_data=f"test:mentor:persistence:{i}"
            ))
            if len(row) == 6:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
        await message.answer(text, reply_markup=keyboard)
    
    async def handle_test_mentor_setting(self, callback: CallbackQuery):
        """Handle mentor setting selection."""
        try:
            await callback.answer()
        except:
            pass
        
        user_id = callback.from_user.id
        if user_id not in self._test_state:
            await callback.message.answer("❌ Тестирование не начато.")
            return
        
        parts = callback.data.split(":")
        setting_type = parts[2]  # "persistence", "temperature", "charisma"
        value = int(parts[3])
        
        state = self._test_state[user_id]
        state["results"][setting_type] = value
        
        # Move to next setting or complete test
        if setting_type == "persistence":
            # Show temperature
            text = (
                "📋 <b>ТЕСТИРОВАНИЕ</b>\n\n"
                "<b>Этап 3 из 3: Настройка наставника</b>\n\n"
                "2️⃣ <b>Температура (вежливость)</b> (0-5):\n"
                "Насколько вежливым и мягким должен быть наставник?"
            )
            buttons = []
            row = []
            for i in range(6):  # 0-5
                row.append(InlineKeyboardButton(
                    text=str(i),
                    callback_data=f"test:mentor:temperature:{i}"
                ))
                if len(row) == 6:
                    buttons.append(row)
                    row = []
            if row:
                buttons.append(row)
            keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
            await callback.message.edit_text(text, reply_markup=keyboard)
        
        elif setting_type == "temperature":
            # Show charisma
            text = (
                "📋 <b>ТЕСТИРОВАНИЕ</b>\n\n"
                "<b>Этап 3 из 3: Настройка наставника</b>\n\n"
                "3️⃣ <b>Харизма</b> (0-5):\n"
                "Насколько харизматичным должен быть наставник?"
            )
            buttons = []
            row = []
            for i in range(6):  # 0-5
                row.append(InlineKeyboardButton(
                    text=str(i),
                    callback_data=f"test:mentor:charisma:{i}"
                ))
                if len(row) == 6:
                    buttons.append(row)
                    row = []
            if row:
                buttons.append(row)
            keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
            await callback.message.edit_text(text, reply_markup=keyboard)
        
        elif setting_type == "charisma":
            # Complete test and apply settings
            await self._complete_test(callback.message, user_id)
    
    async def _complete_test(self, message: Message, user_id: int):
        """Complete test and apply settings to user."""
        state = self._test_state.get(user_id)
        if not state:
            await message.answer("❌ Тестирование не найдено.")
            return
        
        results = state["results"]
        user = await self.user_service.get_user(user_id)
        if not user:
            await message.answer("❌ Пользователь не найден.")
            return
        
        # Apply test results to user
        user.question_asking_skill = results.get("asking")
        user.question_answering_skill = results.get("answering")
        user.listening_skill = results.get("listening")
        user.mentor_persistence = results.get("persistence")
        user.mentor_temperature = results.get("temperature")
        user.mentor_charisma = results.get("charisma")
        
        # Apply time settings
        if results.get("lesson_time"):
            user.lesson_delivery_time_local = results["lesson_time"]
        if results.get("reminder_start"):
            user.mentor_reminder_start_local = results["reminder_start"]
        if results.get("reminder_end"):
            user.mentor_reminder_end_local = results["reminder_end"]
        
        # Apply mentor persistence to reminder frequency
        # Map persistence (0-5) to reminder frequency (0-5)
        if user.mentor_persistence is not None:
            user.mentor_reminders = user.mentor_persistence
        
        await self.db.update_user(user)
        
        # Clear test state
        del self._test_state[user_id]
        
        # Show completion message and continue with onboarding
        await message.answer(
            "✅ <b>Тестирование завершено!</b>\n\n"
            "Спасибо за ответы. Настройки применены.\n\n"
            "Сейчас мы подготовим для вас курс..."
        )
        
        # Continue with normal onboarding (without test)
        await asyncio.sleep(1)
        await self._complete_onboarding(message, user, is_upgrade=False)
    
    async def handle_test_time_input(self, message: Message):
        """Handle time input in test."""
        user_id = message.from_user.id
        text = message.text.strip()
        
        # Check if user is in test and awaiting time input
        if user_id not in self._test_state:
            raise SkipHandler()
        
        state = self._test_state[user_id]
        if "awaiting_time" not in state:
            raise SkipHandler()
        
        # Check time format
        import re
        time_pattern = re.compile(r'^([0-1]?[0-9]|2[0-3]):([0-5][0-9])$')
        if not time_pattern.match(text):
            await message.answer("❌ Неверный формат времени. Используйте формат ЧЧ:ММ (например, 09:30)")
            return
        
        # Parse time
        try:
            hh, mm = text.split(":")
            hour = int(hh)
            minute = int(mm)
            if hour < 0 or hour > 23 or minute < 0 or minute > 59:
                raise ValueError("Invalid time")
        except (ValueError, IndexError):
            await message.answer("❌ Неверный формат времени. Используйте формат ЧЧ:ММ (например, 09:30)")
            return
        
        time_type = state["awaiting_time"]
        del state["awaiting_time"]
        
        if time_type == "lesson":
            state["results"]["lesson_time"] = text
            # Show reminder start time selection
            await self._show_test_step_2_reminder_start(message, user_id)
        elif time_type == "reminder_start":
            state["results"]["reminder_start"] = text
            await self._show_test_step_2_reminder_end(message, user_id)
        elif time_type == "reminder_end":
            state["results"]["reminder_end"] = text
            state["step"] = 3
            await self._show_test_step_3(message, user_id)
    
    async def _grant_access_and_notify(self, message: Message, user, is_upgrade: bool = False):
        """
        Grant access to course and send onboarding message.
        
        This is called after successful payment to:
        1. Start test (for new users)
        2. Send onboarding message
        3. Invite user to course bot
        4. Invite user to appropriate groups
        
        Args:
            message: Message object to reply to
            user: User object
            is_upgrade: True if this is a tariff upgrade, False if new access
        """
        # For new users (not upgrades), start test before granting full access
        if not is_upgrade:
            await self._start_test(message, user)
            return
        
        # For upgrades, continue with normal onboarding
        await self._complete_onboarding(message, user, is_upgrade=True)
    
    async def _complete_onboarding(self, message: Message, user, is_upgrade: bool = False):
        """
        Complete onboarding after test (or for upgrades).
        
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
                        text="➡️ Перейти в курс",
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
        Использует тот же метод, что и CourseBot, чтобы урок отправлялся вместе с заданием.
        
        Args:
            user_id: Telegram user ID
        """
        if not self.lesson_loader:
            logger.warning(f"LessonLoader not available, cannot send lesson 0 to user {user_id}")
            return
        
        course_bot_instance = None
        try:
            # Импортируем CourseBot для использования его метода отправки урока
            from bots.course_bot import CourseBot
            from aiogram import Bot
            from aiogram.client.default import DefaultBotProperties
            from aiogram.enums import ParseMode
            
            # Создаем временный экземпляр CourseBot для отправки урока
            # Используем его метод _send_lesson_from_json, который автоматически отправляет задание
            course_bot_instance = CourseBot()
            
            # Get lesson 0 data
            lesson_data = self.lesson_loader.get_lesson(0)
            if not lesson_data:
                logger.warning(f"Lesson 0 not found for user {user_id}")
                return
            
            # Get user from database
            user = await self.user_service.get_user(user_id)
            if not user:
                logger.error(f"User {user_id} not found")
                return

            # Legal consent required before sending lessons
            if not getattr(user, "legal_accepted_at", None):
                try:
                    await self.bot.send_message(
                        user_id,
                        self._legal_consent_text(),
                        reply_markup=self._legal_consent_keyboard(),
                        disable_web_page_preview=True
                    )
                except Exception:
                    pass
                logger.warning(f"User {user_id} has not accepted legal terms yet; skipping lesson 0 send")
                return
            
            # Используем метод CourseBot для отправки урока с заданием
            # Этот метод автоматически отправляет задание вместе с уроком
            logger.info(f"📚 Sending lesson 0 with assignment to user {user_id}")
            await course_bot_instance._send_lesson_from_json(user, lesson_data, day=0)
            logger.info(f"✅ Lesson 0 with assignment sent to user {user_id}")
            
        except Exception as e:
            logger.error(f"Error in _send_lesson_0_to_user for user {user_id}: {e}", exc_info=True)
            raise
        finally:
            # Важно: закрываем aiohttp-сессию бота, чтобы не копить ресурсы/подключения
            try:
                if course_bot_instance and getattr(course_bot_instance, "bot", None):
                    await course_bot_instance.bot.session.close()
            except Exception:
                pass
    
    async def start(self):
        """Start the bot."""
        try:
            # ВАЖНО: Подключаем базу данных ПЕРВЫМ делом
            logger.info("Connecting to database...")
            try:
                await self.db.connect()
                logger.info("✅ Database connected")
            except Exception as db_error:
                logger.error(f"❌ Failed to connect to database: {db_error}", exc_info=True)
                # Пробуем еще раз
                try:
                    await self.db.connect()
                    logger.info("✅ Database connected on retry")
                except Exception as retry_error:
                    logger.error(f"❌ Database connection retry failed: {retry_error}", exc_info=True)
                    raise
            
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
            logger.info(f"   Callback query handlers: {len(self.dp.callback_query.handlers)}")
            for i, handler in enumerate(self.dp.callback_query.handlers):
                callback_name = handler.callback.__name__ if hasattr(handler, 'callback') else 'unknown'
                filters_info = str(handler.filters) if hasattr(handler, 'filters') else 'no filters'
                logger.info(f"   [{i+1}] {callback_name} (filters: {filters_info[:50]})")
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
