"""
Helper functions for sending messages to admin bot.
"""

import logging
from typing import Optional
from aiogram import Bot
from core.config import Config

logger = logging.getLogger(__name__)


async def send_to_admin_bot(
    message_text: str,
    reply_markup: Optional[object] = None,
    photo_file_id: Optional[str] = None,
    video_file_id: Optional[str] = None,
    document_file_id: Optional[str] = None
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
    if not Config.ADMIN_BOT_TOKEN:
        logger.warning("ADMIN_BOT_TOKEN not configured, cannot send to admin bot")
        return False
    
    if not Config.ADMIN_CHAT_ID:
        logger.warning("ADMIN_CHAT_ID not configured, cannot send to admin bot")
        return False
    
    try:
        admin_bot = Bot(token=Config.ADMIN_BOT_TOKEN)
        
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
        else:
            await admin_bot.send_message(
                Config.ADMIN_CHAT_ID,
                message_text,
                reply_markup=reply_markup
            )
        
        await admin_bot.session.close()
        return True
    except Exception as e:
        logger.error(f"Error sending message to admin bot: {e}", exc_info=True)
        return False
