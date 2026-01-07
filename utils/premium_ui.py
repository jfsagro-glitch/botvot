"""
Premium UI utilities for enhanced bot experience.

This module provides:
- Animated message sending
- Premium formatting
- Progress indicators
- Visual enhancements
"""

import asyncio
from typing import Optional
from aiogram import Bot
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ChatAction


async def send_typing_action(bot: Bot, chat_id: int, duration: float = 1.0):
    """Send typing action to show bot is working."""
    await bot.send_chat_action(chat_id, ChatAction.TYPING)
    await asyncio.sleep(duration)


async def send_animated_message(
    bot: Bot,
    chat_id: int,
    text: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
    typing_duration: float = 0.5
):
    """
    Send message with typing animation.
    
    Args:
        bot: Bot instance
        chat_id: Target chat ID
        text: Message text
        reply_markup: Optional keyboard
        typing_duration: Duration of typing animation
    """
    await send_typing_action(bot, chat_id, typing_duration)
    return await bot.send_message(chat_id, text, reply_markup=reply_markup)


def create_progress_bar(current: int, total: int, length: int = 10) -> str:
    """Create a visual progress bar."""
    filled = int((current / total) * length)
    bar = "█" * filled + "░" * (length - filled)
    percentage = int((current / total) * 100)
    return f"{bar} {percentage}%"


def format_premium_header(title: str) -> str:
    """Format premium header with decorative elements."""
    return f"✨ {title} ✨"


def format_premium_section(title: str, content: str) -> str:
    """Format premium section with title and content."""
    return f"━━━━━━━━━━━━━━━━━━━━\n<b>{title}</b>\n━━━━━━━━━━━━━━━━━━━━\n\n{content}"


def create_premium_separator() -> str:
    """Create a decorative separator."""
    return "━━━━━━━━━━━━━━━━━━━━"


def format_tariff_card(tariff_name: str, price: float, features: list, emoji: str = "⭐") -> str:
    """Format tariff as a premium card."""
    features_text = "\n".join([f"  {feature}" for feature in features])
    return (
        f"{emoji} <b>{tariff_name}</b> {emoji}\n"
        f"💰 <b>{price:.0f}₽</b>\n\n"
        f"{features_text}"
    )


def create_success_animation() -> str:
    """Create success message with animation emojis."""
    return "🎉✨🎊✨🎉"


def create_loading_animation() -> str:
    """Create loading animation."""
    return "⏳"


def format_price(amount: float, currency: str = "RUB") -> str:
    """Format price with currency symbol."""
    if currency == "RUB":
        return f"{amount:.0f}₽"
    return f"{amount:.2f} {currency}"


def create_premium_keyboard(buttons: list) -> InlineKeyboardMarkup:
    """Create premium keyboard with better styling."""
    keyboard_buttons = []
    for button in buttons:
        if isinstance(button, list):
            # Multiple buttons in a row
            row = []
            for btn in button:
                row.append(InlineKeyboardButton(**btn))
            keyboard_buttons.append(row)
        else:
            # Single button in a row
            keyboard_buttons.append([InlineKeyboardButton(**button)])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)

