"""
User service for managing user accounts and access.

Handles user creation, updates, tariff assignment, and access control.
"""

from datetime import datetime, timedelta
from typing import Optional

from core.database import Database
from core.models import User, Tariff


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
            # Set start_date to tomorrow at 9:00 (UTC+3 = 6:00 UTC)
            # For simplicity, we'll use 6:00 UTC (9:00 MSK)
            now = datetime.utcnow()
            tomorrow = now + timedelta(days=1)
            # Set to 6:00 UTC (9:00 MSK)
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

