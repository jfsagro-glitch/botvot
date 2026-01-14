"""
Система напоминаний от наставника.

Работает параллельно с LessonScheduler и отправляет напоминания
пользователям о необходимости выполнить задание текущего урока.
"""

import asyncio
import logging
from datetime import datetime, timedelta, time
from typing import Callable

from core.database import Database
from core.models import User
from core.config import Config

try:
    from zoneinfo import ZoneInfo  # Python 3.9+
except Exception:  # pragma: no cover
    ZoneInfo = None

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
        try:
            logger.info(
                f"👨‍🏫 Reminder window (local): {Config.MENTOR_REMINDER_START_LOCAL}–{Config.MENTOR_REMINDER_END_LOCAL} "
                f"TZ={Config.SCHEDULE_TIMEZONE}"
            )
        except Exception:
            pass
        
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
        now_utc = datetime.utcnow()

        # Prepare timezone + daily window (local time)
        tz = None
        if ZoneInfo is not None:
            try:
                tz = ZoneInfo(Config.SCHEDULE_TIMEZONE)
            except Exception:
                tz = ZoneInfo("UTC")
        # If zoneinfo not available, fall back to UTC behavior
        local_now = now_utc if tz is None else now_utc.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz)

        def _parse_hhmm(value: str, default: time) -> time:
            try:
                hh, mm = (value or "").strip().split(":", 1)
                return time(hour=int(hh), minute=int(mm))
            except Exception:
                return default

        window_start_t = _parse_hhmm(Config.MENTOR_REMINDER_START_LOCAL, time(9, 30))
        window_end_t = _parse_hhmm(Config.MENTOR_REMINDER_END_LOCAL, time(22, 0))
        window_start_dt = local_now.replace(hour=window_start_t.hour, minute=window_start_t.minute, second=0, microsecond=0)
        window_end_dt = local_now.replace(hour=window_end_t.hour, minute=window_end_t.minute, second=0, microsecond=0)
        # Guard against misconfig where end <= start
        if window_end_dt <= window_start_dt:
            window_end_dt = window_start_dt + timedelta(hours=12, minutes=30)
        
        enabled = 0
        sent = 0
        skipped_disabled = 0
        skipped_finished = 0
        skipped_window = 0
        skipped_interval = 0
        skipped_has_assignment = 0
        skipped_started = 0
        errors = 0

        for user in users:
            try:
                # Пропускаем пользователей с отключенными напоминаниями
                if user.mentor_reminders == 0:
                    skipped_disabled += 1
                    continue
                enabled += 1
                
                # Пропускаем пользователей, которые завершили курс
                from core.config import Config
                if user.current_day > Config.COURSE_DURATION_DAYS:
                    skipped_finished += 1
                    continue
                
                # Respect the allowed local-time window (e.g., 09:30–22:00).
                # We do NOT send reminders outside this window.
                if tz is not None:
                    user_now = local_now
                    user_window_start = window_start_dt
                    user_window_end = window_end_dt
                else:
                    # UTC fallback
                    user_now = now_utc
                    user_window_start = now_utc.replace(hour=window_start_t.hour, minute=window_start_t.minute, second=0, microsecond=0)
                    user_window_end = now_utc.replace(hour=window_end_t.hour, minute=window_end_t.minute, second=0, microsecond=0)
                    if user_window_end <= user_window_start:
                        user_window_end = user_window_start + timedelta(hours=12, minutes=30)

                if user_now < user_window_start or user_now > user_window_end:
                    skipped_window += 1
                    continue

                # Distribute reminders evenly within the window.
                window_duration = user_window_end - user_window_start
                interval = window_duration / max(user.mentor_reminders, 1)
                
                # Проверяем, прошло ли достаточно времени с момента последнего напоминания
                if user.last_mentor_reminder:
                    if tz is not None:
                        last_local = user.last_mentor_reminder.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz)
                        # If last reminder was before today's window, treat it as "not sent today"
                        if last_local < user_window_start:
                            pass
                        else:
                            time_since_last = user_now - last_local
                            if time_since_last < interval:
                                skipped_interval += 1
                                continue
                    else:
                        time_since_last = now_utc - user.last_mentor_reminder
                        if time_since_last < interval:
                            skipped_interval += 1
                            continue
                else:
                    # First reminder of the day: only after window start (already ensured)
                    pass
                
                # ВАЖНО: Проверяем, не отправлено ли уже задание для текущего дня
                # Если задание уже отправлено, не отправляем напоминания
                activity = await self.db.has_assignment_activity_for_day(user.user_id, user.current_day)
                if activity:
                    # We don't distinguish started vs submitted here to save queries.
                    skipped_started += 1
                    logger.debug(f"   ⏭️ mentor_reminder: user={user.user_id} day={user.current_day} skip=activity")
                    continue
                
                # Отправляем напоминание
                await self.reminder_callback(user)
                sent += 1
                
            except Exception as e:
                errors += 1
                logger.error(f"Error processing mentor reminder for user {user.user_id}: {e}", exc_info=True)

        # High-signal periodic diagnostics (INFO) so we can debug "not coming" in production logs.
        try:
            logger.info(
                "👨‍🏫 Mentor reminders tick: "
                f"users={len(users)} enabled={enabled} sent={sent} "
                f"skipped_disabled={skipped_disabled} skipped_finished={skipped_finished} "
                f"skipped_window={skipped_window} skipped_interval={skipped_interval} "
                f"skipped_activity={skipped_started} errors={errors} "
                f"local_now={local_now.strftime('%Y-%m-%d %H:%M')} "
                f"window={window_start_t.strftime('%H:%M')}-{window_end_t.strftime('%H:%M')} "
                f"tz={getattr(Config, 'SCHEDULE_TIMEZONE', 'UTC')}"
            )
        except Exception:
            pass
