"""
Telegram helper utilities.

Common functions for formatting messages, creating keyboards, etc.
"""

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from typing import List, Optional

from core.models import Tariff, Lesson
from core.config import Config


def create_persistent_keyboard() -> ReplyKeyboardMarkup:
    """Create persistent keyboard for sales bot with main buttons."""
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="⬆️ Апгрейд тарифа"),
                KeyboardButton(text="📚 Перейти в курс")
            ],
            [
                KeyboardButton(text="📋 Выбор тарифа"),
                KeyboardButton(text="📖 О курсе")
            ]
        ],
        resize_keyboard=True,
        persistent=True
    )
    return keyboard


def create_tariff_keyboard() -> InlineKeyboardMarkup:
    """Create keyboard for tariff selection with additional buttons."""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="📚 БАЗОВЫЙ - 3000₽",
                callback_data="tariff:basic"
            )
        ],
        [
            InlineKeyboardButton(
                text="💬 С ОБРАТНОЙ СВЯЗЬЮ - 5000₽",
                callback_data="tariff:feedback"
            )
        ],
        [
            InlineKeyboardButton(
                text="🎯 PRACTIC - 20000₽",
                callback_data="tariff:practic"
            )
        ],
        [
            InlineKeyboardButton(
                text="💬 Поговорить с человеком",
                callback_data="sales:talk_to_human"
            ),
            InlineKeyboardButton(
                text="📖 О курсе",
                callback_data="sales:about_course"
            )
        ]
    ])
    return keyboard


def create_upgrade_tariff_keyboard(sales_bot_username: str = "StartNowQ_bot") -> InlineKeyboardMarkup:
    """
    Create keyboard with button to upgrade tariff.
    
    Args:
        sales_bot_username: Username of the sales bot (without @)
    
    Returns:
        InlineKeyboardMarkup with upgrade button
    """
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="⬆️ Обновить тариф",
                url=f"https://t.me/{sales_bot_username}?start=upgrade"
            )
        ]
    ])
    return keyboard


def create_lesson_keyboard_from_json(lesson_data: dict, user, general_group_id: str) -> InlineKeyboardMarkup:
    """
    Create keyboard for lesson from JSON data.
    
    Args:
        lesson_data: Данные урока из JSON
        user: Пользователь (для проверки тарифа)
        general_group_id: ID общей группы
    """
    buttons = []
    
    # Кнопка "Сдать задание" (если есть задание)
    task = lesson_data.get("task") or lesson_data.get("task_basic") or lesson_data.get("task_feedback")
    if task:
        day = lesson_data.get("day_number", 1)
        buttons.append([
            InlineKeyboardButton(
                text="📝 Отправить задание",
                callback_data=f"assignment:submit:lesson_{day}"
            )
        ])
    
    # Кнопка "Задать вопрос" - только для тарифа FEEDBACK, PREMIUM, PRACTIC
    if user and user.tariff in [Tariff.FEEDBACK, Tariff.PREMIUM, Tariff.PRACTIC]:
        day = lesson_data.get("day_number", 1)
        buttons.append([
            InlineKeyboardButton(
                text="❓ Задать вопрос",
                callback_data=f"question:ask:lesson_{day}"
            )
        ])
    
    # Кнопка "Навигатор курса"
    buttons.append([
        InlineKeyboardButton(
            text="🧭 Навигатор курса",
            callback_data="navigator:open"
        )
    ])
    
    # Кнопка "Перейти в обсуждение" (если есть группа)
    if general_group_id:
        group_id_clean = str(general_group_id).replace('-100', '').replace('-', '')
        buttons.append([
            InlineKeyboardButton(
                text="💬 Перейти в обсуждение",
                url=f"https://t.me/c/{group_id_clean}"
            )
        ])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def create_lesson_keyboard(lesson: Lesson, general_group_id: str, user=None) -> InlineKeyboardMarkup:
    """
    Create keyboard for lesson interactions.
    
    Includes buttons for:
    - Submit assignment (if lesson has assignment)
    - Ask a question (only for FEEDBACK tariff)
    - Go to discussion
    
    Args:
        lesson: Lesson object
        general_group_id: ID общей группы
        user: User object (optional, for tariff check)
    """
    buttons = []
    
    if lesson.has_assignment():
        buttons.append([
            InlineKeyboardButton(
                text="📝 Отправить задание",
                callback_data=f"assignment:submit:{lesson.lesson_id}"
            )
        ])
    
    # Кнопка "Задать вопрос" - только для тарифа FEEDBACK, PREMIUM, PRACTIC
    if user and user.tariff in [Tariff.FEEDBACK, Tariff.PREMIUM, Tariff.PRACTIC]:
        buttons.append([
            InlineKeyboardButton(
                text="❓ Задать вопрос",
                callback_data=f"question:ask:{lesson.lesson_id}"
            )
        ])
    
    if general_group_id:
        # Используем правильный формат для группы Telegram
        # Для групп формат: https://t.me/c/CHAT_ID (без -100)
        group_id_clean = str(general_group_id).replace('-100', '').replace('-', '')
        buttons.append([
            InlineKeyboardButton(
                text="💬 Перейти в пространство участников",
                url=f"https://t.me/c/{group_id_clean}"
            )
        ])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def format_lesson_message(lesson: Lesson) -> str:
    """Format lesson content into a message."""
    message_parts = [
        f"📖 <b>День {lesson.day_number}: {lesson.title}</b>",
        "",
        lesson.content_text
    ]
    
    if lesson.video_url:
        message_parts.append(f"\n🎥 Видео: {lesson.video_url}")
    
    if lesson.assignment_text:
        message_parts.append(f"\n📝 <b>Задание:</b>\n{lesson.assignment_text}")
    
    # Добавляем призыв к обсуждению
    message_parts.append(
        f"\n💬 Хочешь обсудить задание или посмотреть, как делают другие?\n"
        f"Нажми кнопку ниже 👇"
    )
    
    return "\n".join(message_parts)


def format_tariff_description(tariff: Tariff) -> str:
    """Format tariff description for display with premium styling."""
    separator = "━━━━━━━━━━━━"
    descriptions = {
        Tariff.BASIC: (
            f"{separator}\n"
            f"📚 <b>БАЗОВЫЙ ТАРИФ</b> - 3000₽\n"
            f"{separator}\n\n"
            f"<b>✨ Что включено:</b>\n"
            f"  ✅ 30 дней автоматических уроков\n"
            f"  ✅ Ежедневные материалы: текст, картинки, видео, ссылки\n"
            f"  ✅ Практические задания к каждому уроку\n"
            f"  ✅ Выполняйте задания в своем темпе\n\n"
            f"{separator}\n\n"
            f"<b>🎯 Особенности:</b>\n"
            f"  • Полный доступ ко всему контенту курса\n"
            f"  • Задания можно выполнять как удобно\n"
            f"  • Без обратной связи от лидера\n"
            f"  • Доступ к общему сообществу участников"
        ),
        Tariff.FEEDBACK: (
            f"{separator}\n"
            f"💬 <b>С ОБРАТНОЙ СВЯЗЬЮ</b> - 5000₽\n"
            f"{separator}\n\n"
            f"<b>✨ Что включено:</b>\n"
            f"  ✅ Всё из Базового тарифа\n"
            f"  ✅ Персональная обратная связь от лидера\n"
            f"  ✅ Проверка выполненных заданий\n"
            f"  ✅ Ответы на ваши вопросы\n\n"
            f"{separator}\n\n"
            f"<b>🎯 Особенности:</b>\n"
            f"  • Лидер проверяет ваши задания\n"
            f"  • Получаете персональные комментарии\n"
            f"  • Можете задавать вопросы и получать ответы\n"
            f"  • Доступ к общему сообществу участников\n\n"
            f"{separator}\n\n"
            f"<b>👤 Для кого:</b>\n"
            f"Для тех, кто хочет не просто пройти курс, а получить персональную поддержку и обратную связь."
        ),
        Tariff.PREMIUM: (
            f"{separator}\n"
            f"⭐ <b>ПРЕМИУМ ТАРИФ</b> - 8000₽\n"
            f"{separator}\n\n"
            f"<b>✨ Что включено:</b>\n"
            f"  ✅ Всё из тарифа с обратной связью\n"
            f"  ✅ Доступ в премиум сообщество\n"
            f"  ✅ Общение с единомышленниками\n"
            f"  ✅ Обсуждение заданий и вопросов\n\n"
            f"{separator}\n\n"
            f"<b>🎯 Особенности:</b>\n"
            f"  • Персональная обратная связь от лидера\n"
            f"  • Премиум сообщество активных участников\n"
            f"  • Обсуждение заданий с другими участниками\n"
            f"  • Среда роста и поддержки\n"
            f"  • Доступ к опыту предыдущих участников\n\n"
            f"{separator}\n\n"
            f"<b>👤 Для кого:</b>\n"
            f"Для тех, кто хочет максимальный результат: обратная связь + среда единомышленников, где можно обсуждать, делиться опытом и расти вместе."
        ),
        Tariff.PRACTIC: (
            f"{separator}\n"
            f"🎯 <b>PRACTIC</b> - 20000₽\n"
            f"{separator}\n\n"
            f"<b>✨ Что включено:</b>\n"
            f"  ✅ Всё из тарифов Basic + Feedback\n"
            f"  ✅ Организация 3-х интервью онлайн\n"
            f"  ✅ Подбор собеседника\n"
            f"  ✅ Каждое интервью до 15 мин\n"
            f"  ✅ Видеозапись 3-х интервью\n"
            f"  ✅ Профессиональный разбор 3-х интервью от лидера или куратора\n\n"
            f"{separator}\n\n"
            f"<b>🎯 Особенности:</b>\n"
            f"  • Полный доступ ко всему контенту курса\n"
            f"  • Персональная обратная связь от лидера\n"
            f"  • Практика интервью в реальных условиях\n"
            f"  • Профессиональный разбор ваших интервью\n"
            f"  • Видеозапись для самостоятельного анализа\n\n"
            f"{separator}\n\n"
            f"<b>👤 Для кого:</b>\n"
            f"Для тех, кто хочет не только изучить теорию, но и применить знания на практике с профессиональной поддержкой и разбором."
        )
    }
    return descriptions.get(tariff, "")

