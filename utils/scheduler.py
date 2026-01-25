"""
Lesson scheduling system.

Handles automatic lesson delivery based on user start dates and day progression.
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import List

from core.database import Database
from core.models import User
from core.config import Config
from services.lesson_service import LessonService
from services.user_service import UserService


class LessonScheduler:
    """
    Schedules and delivers lessons automatically.
    
    This service runs in the background and checks for users
    who should receive their next lesson.
    """
    
    def __init__(self, db: Database, lesson_service: LessonService, 
                 user_service: UserService, delivery_callback):
        """
        Initialize scheduler.
        
        Args:
            db: Database instance
            lesson_service: LessonService instance
            user_service: UserService instance
            delivery_callback: Async function(user, lesson) to deliver lesson
        """
        self.db = db
        self.lesson_service = lesson_service
        self.user_service = user_service
        self.delivery_callback = delivery_callback
        self.running = False
    
    async def start(self, check_interval_seconds: int = 300):
        """
        Start the scheduler.
        
        Args:
            check_interval_seconds: How often to check for lessons to send (default: 5 minutes)
        """
        self.running = True
        logger = logging.getLogger(__name__)
        while self.running:
            try:
                await self._check_and_deliver_lessons()
            except Exception as e:
                logger.error(f"Error in lesson scheduler: {e}", exc_info=True)
            
            await asyncio.sleep(check_interval_seconds)
    
    def stop(self):
        """Stop the scheduler."""
        self.running = False
    
    async def _check_and_deliver_lessons(self):
        """Check all users and deliver lessons that are due."""
        logger = logging.getLogger(__name__)
        logger.debug("Starting lesson delivery check...")
        
        # Avoid re-instantiating LessonLoader on every tick. Reuse the one created in LessonService if available.
        lesson_loader = getattr(self.lesson_service, "lesson_loader", None)
        
        users = await self.db.get_users_with_access()
        logger.debug(f"Checking {len(users)} users with access")
        
        delivered_count = 0
        skipped_count = 0
        
        for user in users:
            try:
                # Проверяем, не завершен ли курс
                if user.current_day > Config.COURSE_DURATION_DAYS:
                    skipped_count += 1
                    continue
                
                # Пропускаем урок 0 (он отправляется сразу после покупки)
                if user.current_day == 0:
                    # Переходим к уроку 1
                    logger.info(f"User {user.user_id}: Advancing from day 0 to day 1")
                    await self.lesson_service.advance_user_to_next_day(user)
                    skipped_count += 1
                    continue
                
                # Проверяем день тишины
                if lesson_loader and lesson_loader.is_silent_day(user.current_day):
                    # Пропускаем день тишины, но увеличиваем счетчик
                    if await self.lesson_service.should_send_lesson(user):
                        logger.info(f"User {user.user_id}: Silent day {user.current_day}, advancing to next day")
                        await self.lesson_service.advance_user_to_next_day(user)
                    skipped_count += 1
                    continue
                
                # Check if lesson should be sent
                if await self.lesson_service.should_send_lesson(user):
                    lesson = await self.lesson_service.get_user_current_lesson(user)
                    
                    if lesson:
                        logger.info(f"User {user.user_id}: Delivering lesson for day {user.current_day}")
                        # Deliver lesson
                        await self.delivery_callback(user, lesson)
                        
                        # Mark lesson as completed and advance to next day
                        await self.lesson_service.mark_lesson_completed(
                            user.user_id, lesson.lesson_id, lesson.day_number
                        )
                        await self.lesson_service.advance_user_to_next_day(user)
                        delivered_count += 1
                        logger.info(f"User {user.user_id}: Lesson delivered and advanced to day {user.current_day + 1}")
                    else:
                        logger.warning(f"User {user.user_id}: should_send_lesson returned True but no lesson found for day {user.current_day}")
                else:
                    skipped_count += 1
            except Exception as e:
                logger.error(f"Error processing lesson for user {user.user_id}: {e}", exc_info=True)
        
        if delivered_count > 0:
            logger.info(f"Lesson delivery check completed: {delivered_count} lessons delivered, {skipped_count} users skipped")
        else:
            logger.debug(f"Lesson delivery check completed: {delivered_count} lessons delivered, {skipped_count} users skipped")

