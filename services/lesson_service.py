"""
Lesson service for managing course lessons and delivery.

Handles lesson retrieval, scheduling, and delivery logic.
"""

from datetime import datetime, timedelta
from typing import Optional, List

from core.database import Database
from core.models import User, Lesson, UserProgress
from core.config import Config

# Импортируем LessonLoader с проверкой, чтобы избежать циклических зависимостей
try:
    from services.lesson_loader import LessonLoader
    LESSON_LOADER_AVAILABLE = True
except ImportError:
    LESSON_LOADER_AVAILABLE = False
    LessonLoader = None


class LessonService:
    """Service for lesson management and delivery."""
    
    def __init__(self, db: Database):
        self.db = db
        # Инициализируем загрузчик из JSON, если доступен
        if LESSON_LOADER_AVAILABLE and LessonLoader:
            self.lesson_loader = LessonLoader()
        else:
            self.lesson_loader = None
    
    async def get_lesson_for_day(self, day_number: int) -> Optional[Lesson]:
        """
        Get lesson for a specific day.
        
        Сначала пытается загрузить из JSON, если нет - из базы данных.
        """
        # Пробуем загрузить из JSON
        if self.lesson_loader:
            lesson_model = self.lesson_loader.convert_to_lesson_model(day_number)
            if lesson_model:
                return lesson_model
        
        # Если нет в JSON, загружаем из базы данных
        return await self.db.get_lesson_by_day(day_number)
    
    async def get_user_current_lesson(self, user: User) -> Optional[Lesson]:
        """Get the current lesson for a user based on their progress."""
        if not user.has_access() or not user.start_date:
            return None
        
        return await self.get_lesson_for_day(user.current_day)
    
    async def should_send_lesson(self, user: User) -> bool:
        """
        Determine if a lesson should be sent to the user.
        
        Logic:
        - Lesson 0 is sent immediately after purchase (handled separately)
        - Lesson 1 is sent on start_date at 9:00 (6:00 UTC)
        - Subsequent lessons are sent daily at 9:00 (6:00 UTC)
        """
        if not user.has_access() or not user.start_date:
            return False
        
        # Lesson 0 is sent immediately after purchase, not by scheduler
        if user.current_day == 0:
            return False
        
        # Calculate when the lesson should be sent
        # start_date is set to tomorrow at 9:00 (6:00 UTC) when access is granted
        # Lesson 1 should be sent at start_date
        # Lesson 2 should be sent at start_date + 1 day
        # etc.
        expected_lesson_time = user.start_date + timedelta(days=user.current_day - 1)
        
        return datetime.utcnow() >= expected_lesson_time
    
    async def get_next_lesson_day(self, user: User) -> Optional[int]:
        """Get the next lesson day number for a user."""
        if not user.has_access():
            return None
        
        if user.current_day >= Config.COURSE_DURATION_DAYS:
            return None  # Course completed
        
        return user.current_day
    
    async def mark_lesson_completed(self, user_id: int, lesson_id: int, day_number: int):
        """Mark a lesson as completed for a user."""
        await self.db.mark_lesson_completed(user_id, lesson_id, day_number)
    
    async def advance_user_to_next_day(self, user: User):
        """Advance user to the next lesson day."""
        if user.current_day < Config.COURSE_DURATION_DAYS:
            user.current_day += 1
            await self.db.update_user(user)
    
    async def get_all_lessons(self) -> List[Lesson]:
        """Get all lessons in the course."""
        return await self.db.get_all_lessons()
