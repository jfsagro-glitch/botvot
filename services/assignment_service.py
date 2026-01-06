"""
Assignment service for managing assignment submissions and feedback.

Handles assignment creation, routing to admins, and feedback delivery.
"""

from typing import Optional, List

from core.database import Database
from core.models import User, Assignment, Lesson


class AssignmentService:
    """Service for assignment management."""
    
    def __init__(self, db: Database):
        self.db = db
    
    async def submit_assignment(
        self,
        user: User,
        lesson: Lesson,
        submission_text: Optional[str] = None,
        submission_media_ids: Optional[List[str]] = None
    ) -> Assignment:
        """Submit an assignment for a lesson."""
        assignment = await self.db.create_assignment(
            user_id=user.user_id,
            lesson_id=lesson.lesson_id,
            day_number=lesson.day_number,
            submission_text=submission_text,
            submission_media_ids=submission_media_ids
        )
        return assignment
    
    async def get_assignment(self, assignment_id: int) -> Optional[Assignment]:
        """Get assignment by ID."""
        return await self.db.get_assignment(assignment_id)
    
    async def get_pending_assignments(self) -> List[Assignment]:
        """Get all assignments pending admin feedback."""
        return await self.db.get_pending_assignments()
    
    async def add_feedback(self, assignment_id: int, feedback: str):
        """Add admin feedback to an assignment."""
        await self.db.update_assignment_feedback(assignment_id, feedback)
    
    async def mark_feedback_sent(self, assignment_id: int):
        """Mark feedback as sent to user."""
        await self.db.mark_feedback_sent(assignment_id)

