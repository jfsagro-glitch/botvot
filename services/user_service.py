"""
User service for managing user accounts and access.

Handles user creation, updates, tariff assignment, and access control.
"""

from datetime import datetime, timedelta, time
from typing import Optional

from core.database import Database
from core.models import User, Tariff
from core.config import Config

try:
    from zoneinfo import ZoneInfo  # Python 3.9+
except Exception:  # pragma: no cover
    ZoneInfo = None


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
        
        return await self.db.create_user(user_id, username, first_name, last_name)
    
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
        - start_date = tomorrow at 9:00 (lesson 1 will be sent tomorrow at 9:00)
        """
        user = await self.get_or_create_user(user_id)
        
        # Only grant access if user doesn't already have it
        if not user.has_access():
            user.tariff = tariff
            # Set start_date to tomorrow at 09:00 in configured timezone (default: Europe/Moscow),
            # stored as naive UTC datetime for backwards compatibility.
            now_utc = datetime.utcnow()
            if ZoneInfo is not None:
                try:
                    tz = ZoneInfo(Config.SCHEDULE_TIMEZONE)
                except Exception:
                    tz = ZoneInfo("UTC")
                now_local = now_utc.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz)
                tomorrow_local_date = (now_local + timedelta(days=1)).date()
                start_local = datetime.combine(tomorrow_local_date, time(9, 0), tzinfo=tz)
                user.start_date = start_local.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
            else:
                # Fallback to previous behavior: 06:00 UTC ≈ 09:00 MSK
                tomorrow = now_utc + timedelta(days=1)
                user.start_date = tomorrow.replace(hour=6, minute=0, second=0, microsecond=0)
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

