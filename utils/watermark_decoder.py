"""
Утилита для декодирования водяных знаков из текста.

Используется для отслеживания утечек контента курса.
"""

import re
from typing import Optional


def decode_watermark(text: str) -> Optional[int]:
    """
    Декодирует ID пользователя из невидимого водяного знака в тексте.
    
    Args:
        text: Текст с водяным знаком
        
    Returns:
        ID пользователя или None, если водяной знак не найден
    """
    # Ищем маркер начала водяного знака: \u200B\u200C
    # и маркер конца: \u200D\uFEFF
    pattern = r'\u200B\u200C([\u200B\u200C\u200D\uFEFF]+)\u200D\uFEFF'
    match = re.search(pattern, text)
    
    if not match:
        return None
    
    watermark_chars = match.group(1)
    
    # Маппинг символов на цифры
    char_to_digit = {
        '\u200B': 0,  # Zero-width space
        '\u200C': 1,  # Zero-width non-joiner
        '\u200D': 2,  # Zero-width joiner
        '\uFEFF': 3,  # Zero-width no-break space
    }
    
    # Декодируем ID пользователя
    user_id_str = ""
    for char in watermark_chars:
        if char in char_to_digit:
            # Обратное преобразование: находим цифру по символу
            # Используем обратный маппинг
            digit = char_to_digit[char]
            user_id_str += str(digit)
        else:
            # Если встретили неизвестный символ, возвращаем None
            return None
    
    try:
        return int(user_id_str)
    except ValueError:
        return None


def extract_watermark_info(text: str) -> dict:
    """
    Извлекает информацию о водяном знаке из текста.
    
    Args:
        text: Текст с водяным знаком
        
    Returns:
        Словарь с информацией:
        - user_id: ID пользователя (если найден)
        - has_watermark: True, если водяной знак найден
        - watermark_position: Позиция водяного знака в тексте
    """
    pattern = r'\u200B\u200C([\u200B\u200C\u200D\uFEFF]+)\u200D\uFEFF'
    match = re.search(pattern, text)
    
    if not match:
        return {
            "user_id": None,
            "has_watermark": False,
            "watermark_position": None
        }
    
    user_id = decode_watermark(text)
    
    return {
        "user_id": user_id,
        "has_watermark": True,
        "watermark_position": match.start()
    }


def remove_watermark(text: str) -> str:
    """
    Удаляет водяной знак из текста.
    
    Args:
        text: Текст с водяным знаком
        
    Returns:
        Текст без водяного знака
    """
    pattern = r'\u200B\u200C[\u200B\u200C\u200D\uFEFF]+\u200D\uFEFF'
    return re.sub(pattern, '', text)
