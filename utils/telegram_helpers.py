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
                text="⭐ ПРЕМИУМ - 8000₽",
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
            "📚 <b>БАЗОВЫЙ тариф - 3000₽</b>\n\n"
            "<b>Что включено:</b>\n"
            "✅ 30 дней автоматических уроков\n"
            "✅ Ежедневные материалы: текст, картинки, видео, ссылки\n"
            "✅ Практические задания к каждому уроку\n"
            "✅ Выполняйте задания в своем темпе\n\n"
            "<b>Особенности:</b>\n"
            "• Полный доступ ко всему контенту курса\n"
            "• Задания можно выполнять как удобно\n"
            "• Без обратной связи от лидера\n"
            "• Доступ к общему сообществу участников"
        ),
        Tariff.FEEDBACK: (
            "💬 <b>С ОБРАТНОЙ СВЯЗЬЮ тариф - 5000₽</b>\n\n"
            "<b>Что включено:</b>\n"
            "✅ Всё из Базового тарифа\n"
            "✅ Персональная обратная связь от лидера\n"
            "✅ Проверка выполненных заданий\n"
            "✅ Ответы на ваши вопросы\n\n"
            "<b>Особенности:</b>\n"
            "• Лидер проверяет ваши задания\n"
            "• Получаете персональные комментарии\n"
            "• Можете задавать вопросы и получать ответы\n"
            "• Доступ к общему сообществу участников\n\n"
            "<b>Для кого:</b>\n"
            "Для тех, кто хочет не просто пройти курс, а получить персональную поддержку и обратную связь."
        ),
        Tariff.PREMIUM: (
            "⭐ <b>ПРЕМИУМ тариф - 8000₽</b>\n\n"
            "<b>Что включено:</b>\n"
            "✅ Всё из тарифа с обратной связью\n"
            "✅ Доступ в премиум сообщество\n"
            "✅ Общение с единомышленниками\n"
            "✅ Обсуждение заданий и вопросов\n\n"
            "<b>Особенности:</b>\n"
            "• Персональная обратная связь от лидера\n"
            "• Премиум сообщество активных участников\n"
            "• Обсуждение заданий с другими участниками\n"
            "• Среда роста и поддержки\n"
            "• Доступ к опыту предыдущих участников\n\n"
            "<b>Для кого:</b>\n"
            "Для тех, кто хочет максимальный результат: обратная связь + среда единомышленников, где можно обсуждать, делиться опытом и расти вместе."
        )
    }
    return descriptions.get(tariff, "")

