"""
Community service for managing Telegram group access.

Handles group invitations and access control based on tariffs.
"""

from typing import List

from core.models import User, Tariff
from core.config import Config


class CommunityService:
    """Service for community/group access management."""
    
    def __init__(self):
        self.general_group_id = Config.GENERAL_GROUP_ID
        self.premium_group_id = Config.PREMIUM_GROUP_ID
    
    def get_groups_for_user(self, user: User) -> List[str]:
        """
        Get list of group chat IDs user should have access to.
        
        Returns list of group chat IDs based on user's tariff.
        """
        if not user.has_access():
            return []
        
        groups = []
        
        # All paid users get access to general group
        if self.general_group_id:
            groups.append(self.general_group_id)
        
        # Premium users also get premium group access
        if user.has_premium_access() and self.premium_group_id:
            groups.append(self.premium_group_id)
        
        return groups
    
    def get_group_invite_link(self, group_id: str) -> str:
        """
        Generate or retrieve group invite link.
        
        In production, you might want to:
        - Generate invite links via Telegram Bot API
        - Store and reuse existing invite links
        - Create invite links with expiration
        
        For now, returns a placeholder format.
        """
        # In production, use bot.create_chat_invite_link() or similar
        return f"https://t.me/+{group_id}"

