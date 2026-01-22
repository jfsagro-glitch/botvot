"""
Question service for managing user questions.

Handles question routing, FAQ, and question tracking.
"""

from datetime import datetime
from html import escape
from typing import Optional, List, Dict
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
        question_voice_file_id: Optional[str] = None,
        context: Optional[str] = None
    ) -> dict:
        """
        Create a question record and save to database.
        """
        user = await self.db.get_user(user_id)
        day_number = lesson_id if lesson_id else None
        
        # Сохраняем вопрос в базу данных
        await self.db._ensure_connection()
        created_at = datetime.utcnow().isoformat()
        
        cursor = await self.db.conn.execute("""
            INSERT INTO questions (user_id, lesson_id, day_number, question_text, question_voice_file_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (user_id, lesson_id, day_number, question_text, question_voice_file_id, created_at))
        await self.db.conn.commit()
        question_id = cursor.lastrowid
        
        return {
            "question_id": question_id,
            "user_id": user_id,
            "user_name": f"{user.first_name} {user.last_name or ''}".strip() if user else "Unknown",
            "username": user.username if user else None,
            "lesson_id": lesson_id,
            "day_number": day_number,
            "question_text": question_text,
            "question_voice_file_id": question_voice_file_id,
            "context": context,
            "timestamp": created_at
        }
    
    async def get_question(self, question_id: int) -> Optional[Dict]:
        """Get question by ID."""
        await self.db._ensure_connection()
        async with self.db.conn.execute("""
            SELECT * FROM questions WHERE question_id = ?
        """, (question_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                return dict(row)
        return None
    
    async def get_unanswered_questions(self, limit: int = 50) -> List[Dict]:
        """Get list of unanswered questions."""
        await self.db._ensure_connection()
        async with self.db.conn.execute("""
            SELECT q.*, u.first_name, u.last_name, u.username
            FROM questions q
            LEFT JOIN users u ON q.user_id = u.user_id
            WHERE q.answered_at IS NULL
            ORDER BY q.created_at DESC
            LIMIT ?
        """, (limit,)) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
    
    async def get_all_questions(self, limit: int = 50, offset: int = 0) -> List[Dict]:
        """Get all questions with pagination."""
        await self.db._ensure_connection()
        async with self.db.conn.execute("""
            SELECT q.*, u.first_name, u.last_name, u.username
            FROM questions q
            LEFT JOIN users u ON q.user_id = u.user_id
            ORDER BY q.created_at DESC
            LIMIT ? OFFSET ?
        """, (limit, offset)) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
    
    async def get_questions_stats(self) -> Dict:
        """Get statistics about questions."""
        await self.db._ensure_connection()
        
        async with self.db.conn.execute("""
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN answered_at IS NULL THEN 1 ELSE 0 END) as unanswered
            FROM questions
        """) as cursor:
            row = await cursor.fetchone()
            if row:
                return {
                    "total": row["total"] or 0,
                    "unanswered": row["unanswered"] or 0,
                    "answered": (row["total"] or 0) - (row["unanswered"] or 0)
                }
        return {"total": 0, "unanswered": 0, "answered": 0}
    
    async def answer_question(
        self,
        question_id: int,
        answer_text: Optional[str] = None,
        answer_voice_file_id: Optional[str] = None,
        answered_by_user_id: Optional[int] = None
    ) -> bool:
        """Mark question as answered."""
        await self.db._ensure_connection()
        answered_at = datetime.utcnow().isoformat()
        
        await self.db.conn.execute("""
            UPDATE questions
            SET answered_at = ?, answer_text = ?, answer_voice_file_id = ?, answered_by_user_id = ?
            WHERE question_id = ?
        """, (answered_at, answer_text, answer_voice_file_id, answered_by_user_id, question_id))
        await self.db.conn.commit()
        return True
    
    async def update_pup_message_id(self, question_id: int, pup_message_id: int) -> bool:
        """Update PUP message ID for question."""
        await self.db._ensure_connection()
        await self.db.conn.execute("""
            UPDATE questions
            SET pup_message_id = ?
            WHERE question_id = ?
        """, (pup_message_id, question_id))
        await self.db.conn.commit()
        return True
    
    async def format_question_for_admin(self, question_data: dict) -> str:
        """Format question for admin chat."""
        user_name = escape(str(question_data.get("user_name") or "Unknown"))
        username = question_data.get("username")
        user_info = user_name
        if username:
            user_info += f" (@{escape(str(username))})"
        
        question_id = question_data.get("question_id")
        message = f"❓ <b>Новый вопрос</b>"
        if question_id:
            message += f" #{question_id}"
        message += "\n\n"
        
        message += f"👤 Пользователь: {user_info}\n"
        message += f"🆔 ID: {question_data['user_id']}\n"
        
        if question_data.get('lesson_id') or question_data.get('day_number'):
            day = question_data.get('day_number') or question_data.get('lesson_id')
            message += f"📚 Урок: День {day}\n"
        
        if question_data.get('context'):
            message += f"📍 Контекст: {escape(str(question_data['context']))}\n"
        
        if question_data.get('question_text'):
            message += f"\n💭 <b>Вопрос:</b>\n{escape(str(question_data['question_text']))}"
        elif question_data.get('question_voice_file_id'):
            message += f"\n💭 <b>Вопрос:</b> Голосовое сообщение"
        
        return message
    
    async def format_question_for_list(self, question: dict) -> str:
        """Format question for list display."""
        user_name = escape(str(question.get("first_name") or "Unknown"))
        if question.get("last_name"):
            user_name += f" {escape(str(question['last_name']))}"
        username = question.get("username")
        if username:
            user_name += f" (@{escape(str(username))})"
        
        status = "✅" if question.get("answered_at") else "⏳"
        question_id = question.get("question_id")
        day = question.get("day_number") or question.get("lesson_id") or "?"
        
        text_preview = ""
        if question.get("question_text"):
            text = escape(str(question["question_text"]))
            if len(text) > 50:
                text_preview = text[:50] + "..."
            else:
                text_preview = text
        else:
            text_preview = "🎤 Голосовое сообщение"
        
        return f"{status} <b>#{question_id}</b> | День {day} | {user_name}\n{text_preview}"
