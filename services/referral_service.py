"""
Referral service for managing partner referrals.

Handles referral tracking and statistics.
"""

from core.database import Database


class ReferralService:
    """Service for referral management."""
    
    def __init__(self, db: Database):
        self.db = db
    
    async def track_referral(self, partner_id: str, referred_user_id: int):
        """Track a referral from a partner."""
        await self.db.create_referral(partner_id, referred_user_id)
    
    async def get_referral_count(self, partner_id: str) -> int:
        """Get the number of successful referrals for a partner."""
        return await self.db.get_referral_stats(partner_id)

