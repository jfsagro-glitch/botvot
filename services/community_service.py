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
        # Prefer explicitly configured invite links, because numeric chat IDs
        # cannot be converted to valid invite URLs.
        if group_id == Config.GENERAL_GROUP_ID and Config.GENERAL_GROUP_INVITE_LINK:
            return Config.GENERAL_GROUP_INVITE_LINK
        if group_id == Config.PREMIUM_GROUP_ID and Config.PREMIUM_GROUP_INVITE_LINK:
            return Config.PREMIUM_GROUP_INVITE_LINK
        
        # Fallback: if group_id already looks like a URL/invite slug, return as-is.
        # (This keeps backwards compatibility if someone stored an invite link in *_GROUP_ID.)
        if isinstance(group_id, str) and group_id.startswith("https://t.me/"):
            return group_id
        if isinstance(group_id, str) and group_id.startswith("https://web.telegram.org/"):
            return group_id
        if isinstance(group_id, str) and (group_id.startswith("+") or group_id.startswith("joinchat/")):
            return f"https://t.me/{group_id.lstrip('/')}"
        # Public groups/channels can be opened by username
        if isinstance(group_id, str) and group_id.startswith("@") and len(group_id) > 1:
            return f"https://t.me/{group_id[1:]}"
        if isinstance(group_id, str) and group_id and group_id.lstrip("@").isalnum() and not group_id.startswith("-"):
            # if user passed 'mygroup' without '@'
            return f"https://t.me/{group_id.lstrip('@')}"
        
        # Not configured
        return ""

