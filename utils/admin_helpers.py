"""
Helper functions for sending messages to admin bot.
"""

import logging
import asyncio
import time
from typing import Optional
from aiogram import Bot
from core.config import Config
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.types import BufferedInputFile

logger = logging.getLogger(__name__)

_ADMIN_BOT_CLIENT: Optional[Bot] = None
_ADMIN_BOT_LOOP: Optional[asyncio.AbstractEventLoop] = None
_ADMIN_CHAT_ID_CACHE: Optional[int] = None
_ADMIN_CHAT_ID_CACHE_LOOP: Optional[asyncio.AbstractEventLoop] = None
_ADMIN_CHAT_ID_CACHE_TS: float = 0.0
_ADMIN_CHAT_ID_CACHE_TTL_S: float = 60.0


def is_admin_bot_configured() -> bool:
    """
    Check if admin bot is properly configured.
    
    Returns:
        True if ADMIN_BOT_TOKEN is configured, False otherwise.
        NOTE: ADMIN_CHAT_ID may be sourced from env or DB at runtime.
    """
    return bool(Config.ADMIN_BOT_TOKEN)

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

def _parse_chat_id(raw: str) -> int:
    s = (raw or "").strip()
    if not s:
        return 0
    if s.startswith("#-") and s[2:].isdigit():
        return int(f"-100{s[2:]}")
    try:
        return int(s)
    except Exception:
        return 0

async def _resolve_admin_chat_id() -> int:
    """
    Resolve admin chat id from:
    1) env ADMIN_CHAT_ID (preferred)
    2) DB app_settings key 'pup_admin_chat_id' (auto-bound by PUP /start)
    3) DB app_settings key 'admin_chat_id' (legacy)
    """
    if Config.ADMIN_CHAT_ID != 0:
        return int(Config.ADMIN_CHAT_ID)

    global _ADMIN_CHAT_ID_CACHE, _ADMIN_CHAT_ID_CACHE_LOOP, _ADMIN_CHAT_ID_CACHE_TS
    loop = asyncio.get_running_loop()
    now = time.monotonic()
    if (
        _ADMIN_CHAT_ID_CACHE is not None
        and _ADMIN_CHAT_ID_CACHE_LOOP is loop
        and (now - _ADMIN_CHAT_ID_CACHE_TS) < _ADMIN_CHAT_ID_CACHE_TTL_S
    ):
        return int(_ADMIN_CHAT_ID_CACHE)

    try:
        from core.database import Database

        db = Database()
        await db.connect()
        try:
            raw = await db.get_setting("pup_admin_chat_id")
            if not raw:
                raw = await db.get_setting("admin_chat_id")
        finally:
            await db.close()

        chat_id = _parse_chat_id(raw or "")
        _ADMIN_CHAT_ID_CACHE = chat_id if chat_id != 0 else None
        _ADMIN_CHAT_ID_CACHE_LOOP = loop
        _ADMIN_CHAT_ID_CACHE_TS = now
        return int(chat_id)
    except Exception:
        logger.warning("Failed to resolve ADMIN_CHAT_ID from DB", exc_info=True)
        _ADMIN_CHAT_ID_CACHE = None
        _ADMIN_CHAT_ID_CACHE_LOOP = loop
        _ADMIN_CHAT_ID_CACHE_TS = now
        return 0


async def send_to_admin_bot(
    message_text: str,
    reply_markup: Optional[object] = None,
    photo_file_id: Optional[str] = None,
    video_file_id: Optional[str] = None,
    document_file_id: Optional[str] = None,
    voice_file_id: Optional[str] = None,
    voice_bytes: Optional[bytes] = None,
    voice_filename: str = "voice.ogg",
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
        logger.warning("Admin bot not configured (missing ADMIN_BOT_TOKEN)")
        return False
    
    try:
        chat_id = await _resolve_admin_chat_id()
        if chat_id == 0:
            logger.warning("Admin chat id not configured (set ADMIN_CHAT_ID or open PUP and send /start)")
            return False

        admin_bot = _get_admin_bot_client()
        
        if photo_file_id:
            await admin_bot.send_photo(
                chat_id,
                photo_file_id,
                caption=message_text,
                reply_markup=reply_markup
            )
        elif video_file_id:
            await admin_bot.send_video(
                chat_id,
                video_file_id,
                caption=message_text,
                reply_markup=reply_markup
            )
        elif document_file_id:
            await admin_bot.send_document(
                chat_id,
                document_file_id,
                caption=message_text,
                reply_markup=reply_markup
            )
        elif voice_bytes:
            await admin_bot.send_voice(
                chat_id,
                BufferedInputFile(voice_bytes, filename=voice_filename),
                caption=message_text,
                reply_markup=reply_markup,
            )
        elif voice_file_id:
            await admin_bot.send_voice(
                chat_id,
                voice_file_id,
                caption=message_text,
                reply_markup=reply_markup
            )
        else:
            await admin_bot.send_message(
                chat_id,
                message_text,
                reply_markup=reply_markup
            )
        return True
    except Exception as e:
        logger.error(f"Error sending message to admin bot: {e}", exc_info=True)
        return False
