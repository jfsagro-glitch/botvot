"""
User service for managing user accounts and access.

Handles user creation, updates, tariff assignment, and access control.
"""

from datetime import datetime, timedelta, time, timezone
from typing import Optional

from core.database import Database
from core.models import User, Tariff
from core.config import Config
from utils.schedule_timezone import get_schedule_timezone


class UserService:
    """Service for user management operations."""
    
    def __init__(self, db: Database):
        self.db = db
    
    async def get_or_create_user(
        self,
        user_id: int,
        username: Optional[str] = None,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None
    ) -> User:
        """Get existing user or create a new one."""
        user = await self.db.get_user(user_id)
        if user:
            # Update user info if changed
            if (user.username != username or 
                user.first_name != first_name or 
                user.last_name != last_name):
                user.username = username
                user.first_name = first_name
                user.last_name = last_name
                await self.db.update_user(user)
            return user
        
        try:
            return await self.db.create_user(user_id, username, first_name, last_name)
        except ValueError as e:
            # Re-raise ValueError (user limit reached) as-is
            raise
        except Exception as e:
            # Log other errors but don't fail silently
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Error creating user {user_id}: {e}", exc_info=True)
            raise
    
    async def grant_access(
        self,
        user_id: int,
        tariff: Tariff,
        referral_partner_id: Optional[str] = None
    ) -> User:
        """
        Grant course access to a user.
        
        Sets:
        - current_day = 0 (lesson 0 will be sent immediately)
        - start_date = tomorrow at configured local time (lesson 1 will be sent tomorrow at that time)
        """
        user = await self.get_or_create_user(user_id)
        
        # Only grant access if user doesn't already have it
        if not user.has_access():
            user.tariff = tariff
            # Set start_date to tomorrow at user's lesson_delivery_time_local or LESSON_DELIVERY_TIME_LOCAL
            # in configured timezone (default: Europe/Moscow), stored as naive UTC datetime for backwards compatibility.
            now_utc = datetime.now(timezone.utc)
            tz = get_schedule_timezone()
            now_local = now_utc.astimezone(tz)
            tomorrow_local_date = (now_local + timedelta(days=1)).date()
            # Use user's custom time if set, otherwise use config default
            delivery_time_str = getattr(user, "lesson_delivery_time_local", None) or Config.LESSON_DELIVERY_TIME_LOCAL
            # Parse "HH:MM" (fallback to 08:30)
            try:
                hh, mm = (delivery_time_str or "").strip().split(":", 1)
                delivery_t = time(hour=int(hh), minute=int(mm))
            except Exception:
                delivery_t = time(8, 30)
            start_local = datetime.combine(tomorrow_local_date, delivery_t, tzinfo=tz)
            user.start_date = start_local.astimezone(timezone.utc).replace(tzinfo=None)
            user.current_day = 0  # Lesson 0 will be sent immediately
            user.referral_partner_id = referral_partner_id
            
            # Record referral if partner ID provided
            if referral_partner_id:
                await self.db.create_referral(referral_partner_id, user_id)
            
            await self.db.update_user(user)
        
        return user
    
    async def update_user_day(self, user_id: int, day_number: int):
        """Update user's current lesson day."""
        user = await self.db.get_user(user_id)
        if user and user.has_access():
            user.current_day = day_number
            await self.db.update_user(user)
    
    async def get_user(self, user_id: int) -> Optional[User]:
        """Get user by ID."""
        return await self.db.get_user(user_id)
