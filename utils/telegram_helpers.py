"""
Telegram helper utilities.

Common functions for formatting messages, creating keyboards, etc.
"""

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from typing import List, Optional

from core.models import Tariff, Lesson
from core.config import Config


def create_tariff_keyboard() -> InlineKeyboardMarkup:
    """Create keyboard for tariff selection."""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="📚 BASIC - $100",
                callback_data="tariff:basic"
            )
        ],
        [
            InlineKeyboardButton(
                text="💬 FEEDBACK - $200",
                callback_data="tariff:feedback"
            )
        ],
        [
            InlineKeyboardButton(
                text="⭐ PREMIUM - $300",
                callback_data="tariff:premium"
            )
        ]
    ])
    return keyboard


def create_lesson_keyboard(lesson: Lesson, general_group_id: str) -> InlineKeyboardMarkup:
    """
    Create keyboard for lesson interactions.
    
    Includes buttons for:
    - Submit assignment (if lesson has assignment)
    - Ask a question
    - Go to discussion
    """
    buttons = []
    
    if lesson.has_assignment():
        buttons.append([
            InlineKeyboardButton(
                text="📝 Отправить задание",
                callback_data=f"assignment:submit:{lesson.lesson_id}"
            )
        ])
    
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
    """Format tariff description for display."""
    descriptions = {
        Tariff.BASIC: (
            "📚 <b>БАЗОВЫЙ тариф - $100</b>\n\n"
            "✅ Полный контент курса (30 дней)\n"
            "✅ Ежедневные автоматические уроки\n"
            "❌ Без обратной связи по заданиям\n"
            "✅ Доступ к общему сообществу"
        ),
        Tariff.FEEDBACK: (
            "💬 <b>С ОБРАТНОЙ СВЯЗЬЮ тариф - $200</b>\n\n"
            "✅ Полный контент курса (30 дней)\n"
            "✅ Ежедневные автоматические уроки\n"
            "✅ Персональная обратная связь по заданиям\n"
            "✅ Доступ к общему сообществу"
        ),
        Tariff.PREMIUM: (
            "⭐ <b>ПРЕМИУМ тариф - $300</b>\n\n"
            "✅ Полный контент курса (30 дней)\n"
            "✅ Ежедневные автоматические уроки\n"
            "✅ Персональная обратная связь по заданиям\n"
            "✅ Доступ к общему сообществу\n"
            "✅ Доступ к премиум сообществу"
        )
    }
    return descriptions.get(tariff, "")

