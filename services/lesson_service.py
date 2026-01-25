"""
Lesson service for managing course lessons and delivery.

Handles lesson retrieval, scheduling, and delivery logic.
"""

from datetime import datetime, timedelta, time, timezone
from typing import Optional, List

from core.database import Database
from core.models import User, Lesson, UserProgress
from core.config import Config
from utils.schedule_timezone import get_schedule_timezone

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
        - Lesson 1 is sent on start_date (configured local delivery time, stored in UTC)
        - Subsequent lessons are sent daily at the same delivery time
        - Uses user's custom lesson_delivery_time_local if set, otherwise uses Config default
        """
        import logging
        logger = logging.getLogger(__name__)
        
        if not user.has_access() or not user.start_date:
            logger.debug(f"User {user.user_id}: No access or no start_date")
            return False
        
        # Lesson 0 is sent immediately after purchase, not by scheduler
        if user.current_day == 0:
            logger.debug(f"User {user.user_id}: current_day is 0, skipping")
            return False
        
        # Get user's delivery time or use default
        delivery_time_str = getattr(user, "lesson_delivery_time_local", None) or Config.LESSON_DELIVERY_TIME_LOCAL
        
        # Parse delivery time
        try:
            hh, mm = delivery_time_str.strip().split(":", 1)
            delivery_t = time(hour=int(hh), minute=int(mm))
        except Exception:
            delivery_t = time(8, 30)  # Default fallback
            logger.warning(f"User {user.user_id}: Failed to parse delivery_time '{delivery_time_str}', using default 08:30")
        
        # Calculate expected lesson time in user's timezone
        tz = get_schedule_timezone()
        now_utc = datetime.now(timezone.utc)
        now_local = now_utc.astimezone(tz)
        
        # Calculate the day when lesson should be sent
        # start_date is stored as naive UTC, convert to local timezone
        start_date_utc = user.start_date.replace(tzinfo=timezone.utc) if user.start_date.tzinfo is None else user.start_date
        start_date_local = start_date_utc.astimezone(tz)
        
        # Calculate expected lesson date
        expected_lesson_date = start_date_local.date() + timedelta(days=user.current_day - 1)
        expected_lesson_datetime_local = datetime.combine(expected_lesson_date, delivery_t, tzinfo=tz)
        
        # Convert to UTC for comparison
        expected_lesson_time_utc = expected_lesson_datetime_local.astimezone(timezone.utc).replace(tzinfo=None)
        
        # Check if lesson should be sent (time has passed)
        should_send = datetime.utcnow() >= expected_lesson_time_utc
        
        # Log detailed information for debugging
        logger.info(
            f"User {user.user_id} (day {user.current_day}): "
            f"delivery_time={delivery_time_str}, "
            f"expected_date={expected_lesson_date}, "
            f"expected_time_local={expected_lesson_datetime_local.strftime('%Y-%m-%d %H:%M:%S %Z')}, "
            f"expected_time_utc={expected_lesson_time_utc.strftime('%Y-%m-%d %H:%M:%S')}, "
            f"now_utc={datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')}, "
            f"now_local={now_local.strftime('%Y-%m-%d %H:%M:%S %Z')}, "
            f"should_send={should_send}"
        )
        
        # Additional check: ensure we're not trying to send a lesson that's too far in the past
        # (more than 24 hours late - might indicate a problem)
        time_diff = (datetime.utcnow() - expected_lesson_time_utc).total_seconds()
        if should_send and time_diff > 86400:  # More than 24 hours late
            logger.warning(
                f"User {user.user_id}: Lesson is {time_diff/3600:.1f} hours late! "
                f"Expected: {expected_lesson_time_utc}, Now: {datetime.utcnow()}"
            )
        
        return should_send
    
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
