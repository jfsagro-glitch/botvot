"""
Lesson service for managing course lessons and delivery.

Handles lesson retrieval, scheduling, and delivery logic.
"""

from datetime import datetime, timedelta
from typing import Optional, List

from core.database import Database
from core.models import User, Lesson, UserProgress
from core.config import Config


class LessonService:
    """Service for lesson management and delivery."""
    
    def __init__(self, db: Database):
        self.db = db
    
    async def get_lesson_for_day(self, day_number: int) -> Optional[Lesson]:
        """Get lesson for a specific day."""
        return await self.db.get_lesson_by_day(day_number)
    
    async def get_user_current_lesson(self, user: User) -> Optional[Lesson]:
        """Get the current lesson for a user based on their progress."""
        if not user.has_access() or not user.start_date:
            return None
        
        return await self.get_lesson_for_day(user.current_day)
    
    async def should_send_lesson(self, user: User) -> bool:
        """
        Determine if a lesson should be sent to the user.
        
        Checks if enough time has passed since the last lesson
        or if it's the first lesson.
        """
        if not user.has_access() or not user.start_date:
            return False
        
        # First lesson (day 1) should be sent immediately
        if user.current_day == 1:
            return True
        
        # Calculate when the next lesson should be sent
        # Day 1 starts at start_date, day 2 at start_date + 24h, etc.
        expected_lesson_time = user.start_date + timedelta(
            days=user.current_day - 1,
            hours=Config.LESSON_INTERVAL_HOURS
        )
        
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

