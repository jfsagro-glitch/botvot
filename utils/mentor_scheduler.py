"""
Система напоминаний от наставника.

Работает параллельно с LessonScheduler и отправляет напоминания
пользователям о необходимости выполнить задание текущего урока.
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Callable

from core.database import Database
from core.models import User

logger = logging.getLogger(__name__)


class MentorReminderScheduler:
    """
    Планировщик напоминаний от наставника.
    
    Проверяет пользователей с включенными напоминаниями и отправляет
    им напоминания о задании текущего урока согласно выбранной частоте.
    """
    
    def __init__(self, db: Database, reminder_callback: Callable):
        """
        Инициализация планировщика напоминаний.
        
        Args:
            db: Экземпляр базы данных
            reminder_callback: Асинхронная функция для отправки напоминания (user)
        """
        self.db = db
        self.reminder_callback = reminder_callback
        self.running = False
    
    async def start(self, check_interval_seconds: int = 1800):
        """
        Запуск планировщика напоминаний.
        
        Args:
            check_interval_seconds: Как часто проверять пользователей (по умолчанию 30 минут)
        """
        self.running = True
        logger.info("👨‍🏫 Mentor Reminder Scheduler started")
        
        while self.running:
            try:
                await self._check_and_send_reminders()
            except Exception as e:
                logger.error(f"Error in mentor reminder scheduler: {e}", exc_info=True)
            
            await asyncio.sleep(check_interval_seconds)
    
    def stop(self):
        """Остановка планировщика напоминаний."""
        self.running = False
        logger.info("👨‍🏫 Mentor Reminder Scheduler stopped")
    
    async def _check_and_send_reminders(self):
        """Проверяет всех пользователей и отправляет напоминания тем, кому нужно."""
        users = await self.db.get_users_with_access()
        now = datetime.utcnow()
        
        for user in users:
            try:
                # Пропускаем пользователей с отключенными напоминаниями
                if user.mentor_reminders == 0:
                    continue
                
                # Пропускаем пользователей, которые завершили курс
                from core.config import Config
                if user.current_day > Config.COURSE_DURATION_DAYS:
                    continue
                
                # Определяем интервал между напоминаниями (в часах)
                # Если пользователь выбрал N напоминаний в день, то интервал = 24/N часов
                hours_per_reminder = 24.0 / user.mentor_reminders
                interval_hours = timedelta(hours=hours_per_reminder)
                
                # Проверяем, прошло ли достаточно времени с момента последнего напоминания
                if user.last_mentor_reminder:
                    time_since_last = now - user.last_mentor_reminder
                    if time_since_last < interval_hours:
                        # Еще не время для следующего напоминания
                        continue
                else:
                    # Первое напоминание - отправляем сразу
                    pass
                
                # ВАЖНО: Проверяем, не отправлено ли уже задание для текущего дня
                # Если задание уже отправлено, не отправляем напоминания
                has_assignment = await self.db.has_assignment_for_day(user.user_id, user.current_day)
                if has_assignment:
                    logger.debug(f"   ⏭️ User {user.user_id} already submitted assignment for day {user.current_day}, skipping reminder")
                    continue
                
                # Отправляем напоминание
                await self.reminder_callback(user)
                
            except Exception as e:
                logger.error(f"Error processing mentor reminder for user {user.user_id}: {e}", exc_info=True)
