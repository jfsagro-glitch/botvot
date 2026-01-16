"""
Helper functions for sending messages to admin bot.
"""

import logging
import asyncio
from typing import Optional
from aiogram import Bot
from core.config import Config
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

logger = logging.getLogger(__name__)

_ADMIN_BOT_CLIENT: Optional[Bot] = None
_ADMIN_BOT_LOOP: Optional[asyncio.AbstractEventLoop] = None


def is_admin_bot_configured() -> bool:
    """
    Check if admin bot is properly configured.
    
    Returns:
        True if both ADMIN_BOT_TOKEN and ADMIN_CHAT_ID are configured, False otherwise
    """
    return bool(Config.ADMIN_BOT_TOKEN and Config.ADMIN_CHAT_ID != 0)

def _get_admin_bot_client() -> Bot:
    global _ADMIN_BOT_CLIENT, _ADMIN_BOT_LOOP
    loop = asyncio.get_running_loop()
    if _ADMIN_BOT_CLIENT is not None and _ADMIN_BOT_LOOP is loop:
        return _ADMIN_BOT_CLIENT
    _ADMIN_BOT_LOOP = loop
    _ADMIN_BOT_CLIENT = Bot(
        token=Config.ADMIN_BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    return _ADMIN_BOT_CLIENT


async def send_to_admin_bot(
    message_text: str,
    reply_markup: Optional[object] = None,
    photo_file_id: Optional[str] = None,
    video_file_id: Optional[str] = None,
    document_file_id: Optional[str] = None,
    voice_file_id: Optional[str] = None
) -> bool:
    """
    Send message to admin bot.
    
    Args:
        message_text: Text message to send
        reply_markup: Optional inline keyboard
        photo_file_id: Optional photo file_id
        video_file_id: Optional video file_id
        document_file_id: Optional document file_id
    
    Returns:
        True if sent successfully, False otherwise
    """
    if not is_admin_bot_configured():
        logger.warning("Admin bot not configured (missing ADMIN_BOT_TOKEN or ADMIN_CHAT_ID)")
        return False
    
    try:
        admin_bot = _get_admin_bot_client()
        
        if photo_file_id:
            await admin_bot.send_photo(
                Config.ADMIN_CHAT_ID,
                photo_file_id,
                caption=message_text,
                reply_markup=reply_markup
            )
        elif video_file_id:
            await admin_bot.send_video(
                Config.ADMIN_CHAT_ID,
                video_file_id,
                caption=message_text,
                reply_markup=reply_markup
            )
        elif document_file_id:
            await admin_bot.send_document(
                Config.ADMIN_CHAT_ID,
                document_file_id,
                caption=message_text,
                reply_markup=reply_markup
            )
        elif voice_file_id:
            await admin_bot.send_voice(
                Config.ADMIN_CHAT_ID,
                voice_file_id,
                caption=message_text,
                reply_markup=reply_markup
            )
        else:
            await admin_bot.send_message(
                Config.ADMIN_CHAT_ID,
                message_text,
                reply_markup=reply_markup
            )
        return True
    except Exception as e:
        logger.error(f"Error sending message to admin bot: {e}", exc_info=True)
        return False
