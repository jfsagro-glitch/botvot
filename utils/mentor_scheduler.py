"""
Система напоминаний от наставника.

Работает параллельно с LessonScheduler и отправляет напоминания
пользователям о необходимости выполнить задание текущего урока.
"""

import asyncio
import logging
from datetime import datetime, timedelta, time, timezone
from typing import Callable

from core.database import Database
from core.models import User
from core.config import Config
from utils.schedule_timezone import get_schedule_timezone, format_tz

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
    
    async def start(self, check_interval_seconds: int = 300):
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
        now_utc = datetime.now(timezone.utc)

        # Prepare timezone + daily window (local time)
        tz = get_schedule_timezone()
        local_now = now_utc.astimezone(tz)

        def _parse_hhmm(value: str, default: time) -> time:
            try:
                hh, mm = (value or "").strip().split(":", 1)
                return time(hour=int(hh), minute=int(mm))
            except Exception:
                return default
        
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
                if not user.mentor_reminders or user.mentor_reminders == 0:
                    skipped_disabled += 1
                    continue
                enabled += 1
                
                # Логируем для отладки
                logger.debug(
                    f"   👤 mentor_reminder check: user={user.user_id} "
                    f"reminders={user.mentor_reminders} current_day={user.current_day} "
                    f"last_reminder={user.last_mentor_reminder}"
                )
                
                # Пропускаем пользователей, которые завершили курс
                # NOTE: do NOT re-import Config here; it causes UnboundLocalError earlier in the function.
                if user.current_day > Config.COURSE_DURATION_DAYS:
                    skipped_finished += 1
                    continue
                
                # Respect the allowed local-time window (e.g., 09:30–22:00).
                # Use user's custom window if set, otherwise use config default.
                # We do NOT send reminders outside this window.
                user_window_start_str = getattr(user, "mentor_reminder_start_local", None) or Config.MENTOR_REMINDER_START_LOCAL
                user_window_end_str = getattr(user, "mentor_reminder_end_local", None) or Config.MENTOR_REMINDER_END_LOCAL
                
                window_start_t = _parse_hhmm(user_window_start_str, time(9, 30))
                window_end_t = _parse_hhmm(user_window_end_str, time(22, 0))
                user_window_start_dt = datetime.combine(local_now.date(), window_start_t, tzinfo=tz)
                user_window_end_dt = datetime.combine(local_now.date(), window_end_t, tzinfo=tz)
                # Guard against misconfig where end <= start (window spans midnight)
                if user_window_end_dt <= user_window_start_dt:
                    user_window_end_dt = user_window_end_dt + timedelta(days=1)
                
                user_now = local_now

                if user_now < user_window_start_dt or user_now > user_window_end_dt:
                    skipped_window += 1
                    logger.debug(
                        f"   ⏰ mentor_reminder: user={user.user_id} skip=window "
                        f"(now={user_now.strftime('%H:%M')}, "
                        f"window={window_start_t.strftime('%H:%M')}-{window_end_t.strftime('%H:%M')})"
                    )
                    continue

                # Distribute reminders evenly within the window.
                window_duration = user_window_end_dt - user_window_start_dt
                # Для N напоминаний создаем N интервалов, чтобы равномерно распределить
                # Например, для 4 напоминаний: [0, 1/4, 2/4, 3/4] от начала окна
                interval = window_duration / max(user.mentor_reminders, 1)
                
                # Проверяем, прошло ли достаточно времени с момента последнего напоминания
                should_send = False
                if user.last_mentor_reminder:
                    last_local = user.last_mentor_reminder.replace(tzinfo=timezone.utc).astimezone(tz)
                    # If last reminder was before today's window start, treat it as "not sent today" - send immediately
                    # Compare dates to check if it's a different day
                    if last_local.date() < user_now.date():
                        # Last reminder was yesterday or earlier - send first reminder of today
                        logger.debug(f"   📅 mentor_reminder: user={user.user_id} last was yesterday, sending first today")
                        should_send = True
                    elif last_local < user_window_start_dt:
                        # Last reminder was earlier today but before window start - send first reminder in window
                        logger.debug(f"   ⏰ mentor_reminder: user={user.user_id} last was before window, sending first in window")
                        should_send = True
                    else:
                        # Last reminder was within today's window - check interval
                        time_since_last = user_now - last_local
                        if time_since_last >= interval:
                            should_send = True
                            logger.debug(
                                f"   ✅ mentor_reminder: user={user.user_id} interval passed "
                                f"(since_last={time_since_last.total_seconds()/60:.1f}min, "
                                f"interval={interval.total_seconds()/60:.1f}min)"
                            )
                        else:
                            skipped_interval += 1
                            logger.debug(
                                f"   ⏱️ mentor_reminder: user={user.user_id} skip=interval "
                                f"(since_last={time_since_last.total_seconds()/60:.1f}min, "
                                f"interval={interval.total_seconds()/60:.1f}min)"
                            )
                else:
                    # First reminder ever: only after window start (already ensured)
                    logger.debug(f"   🆕 mentor_reminder: user={user.user_id} first reminder ever")
                    should_send = True
                
                if not should_send:
                    continue
                
                # ВАЖНО: Проверяем, не отправлено ли уже задание для текущего дня
                # Если задание уже отправлено, не отправляем напоминания
                activity = await self.db.has_assignment_activity_for_day(user.user_id, user.current_day)
                if activity:
                    # We don't distinguish started vs submitted here to save queries.
                    skipped_started += 1
                    logger.debug(f"   ⏭️ mentor_reminder: user={user.user_id} day={user.current_day} skip=activity")
                    continue
                
                # Отправляем напоминание
                logger.info(f"   📤 Sending mentor reminder to user {user.user_id} (day {user.current_day}, reminder #{sent + 1}/{user.mentor_reminders})")
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
                f"tz={format_tz(tz)}"
            )
        except Exception:
            pass
