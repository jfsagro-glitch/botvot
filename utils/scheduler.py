"""
Lesson scheduling system.

Handles automatic lesson delivery based on user start dates and day progression.
"""

import asyncio
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
        while self.running:
            try:
                await self._check_and_deliver_lessons()
            except Exception as e:
                print(f"Error in lesson scheduler: {e}")
            
            await asyncio.sleep(check_interval_seconds)
    
    def stop(self):
        """Stop the scheduler."""
        self.running = False
    
    async def _check_and_deliver_lessons(self):
        """Check all users and deliver lessons that are due."""
        users = await self.db.get_users_with_access()
        
        for user in users:
            try:
                # Check if lesson should be sent
                if await self.lesson_service.should_send_lesson(user):
                    lesson = await self.lesson_service.get_user_current_lesson(user)
                    
                    if lesson:
                        # Deliver lesson
                        await self.delivery_callback(user, lesson)
                        
                        # Mark lesson as completed and advance to next day
                        await self.lesson_service.mark_lesson_completed(
                            user.user_id, lesson.lesson_id, lesson.day_number
                        )
                        await self.lesson_service.advance_user_to_next_day(user)
            except Exception as e:
                print(f"Error processing lesson for user {user.user_id}: {e}")

