"""
Question service for managing user questions.

Handles question routing, FAQ, and question tracking.
"""

from datetime import datetime
from typing import Optional, List
from core.database import Database
from core.models import User


class QuestionService:
    """Service for question management."""
    
    def __init__(self, db: Database):
        self.db = db
    
    async def create_question(
        self,
        user_id: int,
        lesson_id: Optional[int] = None,
        question_text: str = None,
        context: Optional[str] = None
    ) -> dict:
        """
        Create a question record.
        
        In production, you might want to store questions in database.
        For now, returns question info for forwarding to admin.
        """
        user = await self.db.get_user(user_id)
        
        return {
            "user_id": user_id,
            "user_name": f"{user.first_name} {user.last_name or ''}".strip() if user else "Unknown",
            "username": user.username if user else None,
            "lesson_id": lesson_id,
            "question_text": question_text,
            "context": context,
            "timestamp": datetime.utcnow().isoformat()
        }
    
    async def format_question_for_admin(self, question_data: dict) -> str:
        """Format question for admin chat."""
        user_info = f"{question_data['user_name']}"
        if question_data.get('username'):
            user_info += f" (@{question_data['username']})"
        
        message = f"❓ <b>Новый вопрос</b>\n\n"
        message += f"Пользователь: {user_info}\n"
        message += f"ID: {question_data['user_id']}\n"
        
        if question_data.get('lesson_id'):
            message += f"Урок: День {question_data['lesson_id']}\n"
        
        if question_data.get('context'):
            message += f"Контекст: {question_data['context']}\n"
        
        message += f"\n<b>Вопрос:</b>\n{question_data.get('question_text', 'Нет текста')}"
        
        return message

