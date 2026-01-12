"""
Data models for the Telegram Course Platform.

This module defines all data structures used throughout the system:
- User information and tariff assignments
- Tariff definitions
- Lesson content and structure
- User progress tracking
- Referral tracking
- Assignment submissions and feedback
"""

from enum import Enum
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, List


class Tariff(str, Enum):
    """Course tariff levels."""
    BASIC = "basic"           # Content only, no feedback
    FEEDBACK = "feedback"     # Content + leader feedback
    PREMIUM = "premium"       # Content + feedback + premium community
    PRACTIC = "practic"       # Content + feedback + 3 online interviews with professional review


@dataclass
class User:
    """User model representing a course participant."""
    user_id: int
    username: Optional[str]
    first_name: Optional[str]
    last_name: Optional[str]
    tariff: Optional[Tariff]
    referral_partner_id: Optional[str]  # Partner who referred this user
    start_date: Optional[datetime]      # When user started the course
    current_day: int                     # Current lesson day (1-30)
    mentor_reminders: int = 0            # Frequency of mentor reminders (0-5, 0 = disabled)
    last_mentor_reminder: Optional[datetime] = None  # Last time mentor reminder was sent
    created_at: datetime
    updated_at: datetime
    
    def has_access(self) -> bool:
        """Check if user has active course access."""
        return self.tariff is not None
    
    def can_receive_feedback(self) -> bool:
        """Check if user's tariff includes feedback."""
        return self.tariff in [Tariff.FEEDBACK, Tariff.PREMIUM, Tariff.PRACTIC]
    
    def has_premium_access(self) -> bool:
        """Check if user has premium community access."""
        return self.tariff == Tariff.PREMIUM
    
    def has_practic_access(self) -> bool:
        """Check if user has PRACTIC tariff (includes interviews)."""
        return self.tariff == Tariff.PRACTIC


@dataclass
class Lesson:
    """Lesson model representing a course lesson."""
    lesson_id: int
    day_number: int              # Day of course (1-30)
    title: str
    content_text: str            # Main lesson text
    image_url: Optional[str]     # Optional image URL
    video_url: Optional[str]     # Optional video URL
    assignment_text: Optional[str]  # Optional assignment description
    created_at: datetime
    
    def has_assignment(self) -> bool:
        """Check if lesson includes an assignment."""
        return self.assignment_text is not None


@dataclass
class UserProgress:
    """Tracks user's progress through the course."""
    progress_id: int
    user_id: int
    lesson_id: int
    day_number: int
    completed: bool
    completed_at: Optional[datetime]
    created_at: datetime


@dataclass
class Referral:
    """Partner referral tracking."""
    referral_id: int
    partner_id: str              # Unique partner identifier
    referred_user_id: int
    created_at: datetime


@dataclass
class Assignment:
    """Assignment submission and feedback."""
    assignment_id: int
    user_id: int
    lesson_id: int
    day_number: int
    submission_text: Optional[str]
    submission_media_ids: Optional[str]  # JSON array of media file IDs
    admin_feedback: Optional[str]
    admin_feedback_at: Optional[datetime]
    submitted_at: datetime
    status: str  # 'submitted', 'reviewed', 'feedback_sent'

