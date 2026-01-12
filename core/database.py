"""
Database abstraction layer for the Telegram Course Platform.

Provides a clean interface for database operations using SQLite.
All database interactions go through this layer, making it easy
to switch to PostgreSQL or another database in the future.
"""

import aiosqlite
import json
from datetime import datetime
from typing import Optional, List
from pathlib import Path

from core.models import User, Tariff, Lesson, UserProgress, Referral, Assignment
from core.config import Config


class Database:
    """Database connection and query manager."""
    
    def __init__(self, db_path: str = None):
        self.db_path = db_path or Config.DATABASE_PATH
        Config.ensure_data_directory()
    
    async def connect(self):
        """Create database connection and initialize schema."""
        self.conn = await aiosqlite.connect(self.db_path)
        self.conn.row_factory = aiosqlite.Row
        await self._init_schema()
    
    async def close(self):
        """Close database connection."""
        await self.conn.close()
    
    async def _init_schema(self):
        """Initialize database schema."""
        # Users table
        await self.conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                tariff TEXT,
                referral_partner_id TEXT,
                start_date TEXT,
                current_day INTEGER DEFAULT 1,
                mentor_reminders INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        
        # Миграция: добавляем поле mentor_reminders, если его нет
        try:
            await self.conn.execute("""
                ALTER TABLE users ADD COLUMN mentor_reminders INTEGER DEFAULT 0
            """)
            await self.conn.commit()
        except Exception:
            # Поле уже существует, игнорируем ошибку
            pass
        
        # Миграция: добавляем поле last_mentor_reminder, если его нет
        try:
            await self.conn.execute("""
                ALTER TABLE users ADD COLUMN last_mentor_reminder TEXT
            """)
            await self.conn.commit()
        except Exception:
            # Поле уже существует, игнорируем ошибку
            pass
        
        # Lessons table
        await self.conn.execute("""
            CREATE TABLE IF NOT EXISTS lessons (
                lesson_id INTEGER PRIMARY KEY AUTOINCREMENT,
                day_number INTEGER NOT NULL UNIQUE,
                title TEXT NOT NULL,
                content_text TEXT NOT NULL,
                image_url TEXT,
                video_url TEXT,
                assignment_text TEXT,
                created_at TEXT NOT NULL
            )
        """)
        
        # User progress table
        await self.conn.execute("""
            CREATE TABLE IF NOT EXISTS user_progress (
                progress_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                lesson_id INTEGER NOT NULL,
                day_number INTEGER NOT NULL,
                completed BOOLEAN DEFAULT 0,
                completed_at TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(user_id),
                FOREIGN KEY (lesson_id) REFERENCES lessons(lesson_id),
                UNIQUE(user_id, lesson_id)
            )
        """)
        
        # Referrals table
        await self.conn.execute("""
            CREATE TABLE IF NOT EXISTS referrals (
                referral_id INTEGER PRIMARY KEY AUTOINCREMENT,
                partner_id TEXT NOT NULL,
                referred_user_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (referred_user_id) REFERENCES users(user_id),
                UNIQUE(partner_id, referred_user_id)
            )
        """)
        
        # Assignments table
        await self.conn.execute("""
            CREATE TABLE IF NOT EXISTS assignments (
                assignment_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                lesson_id INTEGER NOT NULL,
                day_number INTEGER NOT NULL,
                submission_text TEXT,
                submission_media_ids TEXT,
                admin_feedback TEXT,
                admin_feedback_at TEXT,
                submitted_at TEXT NOT NULL,
                status TEXT DEFAULT 'submitted',
                FOREIGN KEY (user_id) REFERENCES users(user_id),
                FOREIGN KEY (lesson_id) REFERENCES lessons(lesson_id)
            )
        """)
        
        await self.conn.commit()
    
    # User operations
    async def get_user(self, user_id: int) -> Optional[User]:
        """Get user by ID."""
        # Убеждаемся, что соединение установлено и активно
        try:
            if not hasattr(self, 'conn') or self.conn is None:
                await self.connect()
            # Проверяем, что соединение действительно работает
            try:
                async with self.conn.execute("SELECT 1") as cursor:
                    await cursor.fetchone()
            except Exception:
                # Соединение неактивно, переподключаемся
                try:
                    await self.close()
                except:
                    pass
                await self.connect()
        except Exception as conn_error:
            # Если не удалось подключиться, пробуем еще раз
            try:
                await self.connect()
            except Exception:
                raise conn_error
        
        async with self.conn.execute(
            "SELECT * FROM users WHERE user_id = ?", (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            return self._row_to_user(row)
    
    async def create_user(self, user_id: int, username: Optional[str] = None,
                         first_name: Optional[str] = None,
                         last_name: Optional[str] = None) -> User:
        """Create a new user."""
        # Убеждаемся, что соединение установлено и активно
        try:
            if not hasattr(self, 'conn') or self.conn is None:
                await self.connect()
            # Проверяем, что соединение действительно работает
            try:
                async with self.conn.execute("SELECT 1") as cursor:
                    await cursor.fetchone()
            except Exception:
                # Соединение неактивно, переподключаемся
                try:
                    await self.close()
                except:
                    pass
                await self.connect()
        except Exception as conn_error:
            # Если не удалось подключиться, пробуем еще раз
            try:
                await self.connect()
            except Exception:
                raise conn_error
        
        now = datetime.utcnow().isoformat()
        await self.conn.execute("""
            INSERT INTO users (user_id, username, first_name, last_name, 
                             mentor_reminders, created_at, updated_at)
            VALUES (?, ?, ?, ?, 0, ?, ?)
        """, (user_id, username, first_name, last_name, now, now))
        await self.conn.commit()
        return await self.get_user(user_id)
    
    async def update_user(self, user: User):
        """Update user information."""
        # Убеждаемся, что соединение установлено и активно
        try:
            if not hasattr(self, 'conn') or self.conn is None:
                await self.connect()
            # Проверяем, что соединение действительно работает
            try:
                async with self.conn.execute("SELECT 1") as cursor:
                    await cursor.fetchone()
            except Exception:
                # Соединение неактивно, переподключаемся
                try:
                    await self.close()
                except:
                    pass
                await self.connect()
        except Exception as conn_error:
            # Если не удалось подключиться, пробуем еще раз
            try:
                await self.connect()
            except Exception:
                raise conn_error
        
        await self.conn.execute("""
            UPDATE users SET
                username = ?, first_name = ?, last_name = ?,
                tariff = ?, referral_partner_id = ?,
                start_date = ?, current_day = ?, mentor_reminders = ?, last_mentor_reminder = ?,
                updated_at = ?
            WHERE user_id = ?
        """, (
            user.username, user.first_name, user.last_name,
            user.tariff.value if user.tariff else None,
            user.referral_partner_id,
            user.start_date.isoformat() if user.start_date else None,
            user.current_day, user.mentor_reminders,
            user.last_mentor_reminder.isoformat() if user.last_mentor_reminder else None,
            datetime.utcnow().isoformat(),
            user.user_id
        ))
        await self.conn.commit()
    
    async def get_users_with_access(self) -> List[User]:
        """Get all users with active course access."""
        async with self.conn.execute(
            "SELECT * FROM users WHERE tariff IS NOT NULL"
        ) as cursor:
            rows = await cursor.fetchall()
            return [self._row_to_user(row) for row in rows]
    
    # Lesson operations
    async def get_lesson_by_day(self, day_number: int) -> Optional[Lesson]:
        """Get lesson by day number."""
        async with self.conn.execute(
            "SELECT * FROM lessons WHERE day_number = ?", (day_number,)
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            return self._row_to_lesson(row)
    
    async def create_lesson(self, day_number: int, title: str, content_text: str,
                           image_url: Optional[str] = None,
                           video_url: Optional[str] = None,
                           assignment_text: Optional[str] = None) -> Lesson:
        """Create a new lesson."""
        now = datetime.utcnow().isoformat()
        await self.conn.execute("""
            INSERT INTO lessons (day_number, title, content_text, image_url,
                               video_url, assignment_text, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (day_number, title, content_text, image_url, video_url,
              assignment_text, now))
        await self.conn.commit()
        
        async with self.conn.execute(
            "SELECT * FROM lessons WHERE day_number = ?", (day_number,)
        ) as cursor:
            row = await cursor.fetchone()
            return self._row_to_lesson(row)
    
    async def get_all_lessons(self) -> List[Lesson]:
        """Get all lessons ordered by day number."""
        async with self.conn.execute(
            "SELECT * FROM lessons ORDER BY day_number"
        ) as cursor:
            rows = await cursor.fetchall()
            return [self._row_to_lesson(row) for row in rows]
    
    # Progress operations
    async def get_user_progress(self, user_id: int, lesson_id: int) -> Optional[UserProgress]:
        """Get user progress for a specific lesson."""
        async with self.conn.execute("""
            SELECT * FROM user_progress 
            WHERE user_id = ? AND lesson_id = ?
        """, (user_id, lesson_id)) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            return self._row_to_progress(row)
    
    async def mark_lesson_completed(self, user_id: int, lesson_id: int, day_number: int):
        """Mark a lesson as completed for a user."""
        now = datetime.utcnow().isoformat()
        await self.conn.execute("""
            INSERT OR REPLACE INTO user_progress 
            (user_id, lesson_id, day_number, completed, completed_at, created_at)
            VALUES (?, ?, ?, 1, ?, ?)
        """, (user_id, lesson_id, day_number, now, now))
        await self.conn.commit()
    
    # Referral operations
    async def create_referral(self, partner_id: str, referred_user_id: int) -> Referral:
        """Create a referral record."""
        now = datetime.utcnow().isoformat()
        await self.conn.execute("""
            INSERT INTO referrals (partner_id, referred_user_id, created_at)
            VALUES (?, ?, ?)
        """, (partner_id, referred_user_id, now))
        await self.conn.commit()
        
        async with self.conn.execute("""
            SELECT * FROM referrals 
            WHERE partner_id = ? AND referred_user_id = ?
        """, (partner_id, referred_user_id)) as cursor:
            row = await cursor.fetchone()
            return self._row_to_referral(row)
    
    async def get_referral_stats(self, partner_id: str) -> int:
        """Get number of referrals for a partner."""
        async with self.conn.execute("""
            SELECT COUNT(*) as count FROM referrals WHERE partner_id = ?
        """, (partner_id,)) as cursor:
            row = await cursor.fetchone()
            return row["count"]
    
    # Assignment operations
    async def create_assignment(self, user_id: int, lesson_id: int, day_number: int,
                               submission_text: Optional[str] = None,
                               submission_media_ids: Optional[List[str]] = None) -> Assignment:
        """Create an assignment submission."""
        now = datetime.utcnow().isoformat()
        media_json = json.dumps(submission_media_ids) if submission_media_ids else None
        
        await self.conn.execute("""
            INSERT INTO assignments 
            (user_id, lesson_id, day_number, submission_text, 
             submission_media_ids, submitted_at, status)
            VALUES (?, ?, ?, ?, ?, ?, 'submitted')
        """, (user_id, lesson_id, day_number, submission_text, media_json, now))
        await self.conn.commit()
        
        async with self.conn.execute("""
            SELECT * FROM assignments 
            WHERE user_id = ? AND lesson_id = ? AND submitted_at = ?
        """, (user_id, lesson_id, now)) as cursor:
            row = await cursor.fetchone()
            return self._row_to_assignment(row)
    
    async def get_assignment(self, assignment_id: int) -> Optional[Assignment]:
        """Get assignment by ID."""
        async with self.conn.execute(
            "SELECT * FROM assignments WHERE assignment_id = ?", (assignment_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            return self._row_to_assignment(row)
    
    async def has_assignment_for_day(self, user_id: int, day_number: int) -> bool:
        """
        Check if user has submitted an assignment for a specific day.
        
        Args:
            user_id: User ID
            day_number: Day number
            
        Returns:
            True if assignment exists for this user and day, False otherwise
        """
        # Убеждаемся, что соединение установлено и активно
        try:
            if not hasattr(self, 'conn') or self.conn is None:
                await self.connect()
            # Проверяем, что соединение действительно работает
            try:
                async with self.conn.execute("SELECT 1") as cursor:
                    await cursor.fetchone()
            except Exception:
                # Соединение неактивно, переподключаемся
                try:
                    await self.close()
                except:
                    pass
                await self.connect()
        except Exception as conn_error:
            # Если не удалось подключиться, пробуем еще раз
            try:
                await self.connect()
            except Exception:
                raise conn_error
        
        async with self.conn.execute(
            "SELECT COUNT(*) FROM assignments WHERE user_id = ? AND day_number = ?",
            (user_id, day_number)
        ) as cursor:
            row = await cursor.fetchone()
            count = row[0] if row else 0
            return count > 0
    
    async def get_pending_assignments(self) -> List[Assignment]:
        """Get all assignments pending admin feedback."""
        async with self.conn.execute("""
            SELECT * FROM assignments 
            WHERE status = 'submitted' AND admin_feedback IS NULL
            ORDER BY submitted_at
        """) as cursor:
            rows = await cursor.fetchall()
            return [self._row_to_assignment(row) for row in rows]
    
    async def update_assignment_feedback(self, assignment_id: int, feedback: str):
        """Update assignment with admin feedback."""
        now = datetime.utcnow().isoformat()
        await self.conn.execute("""
            UPDATE assignments SET
                admin_feedback = ?,
                admin_feedback_at = ?,
                status = 'reviewed'
            WHERE assignment_id = ?
        """, (feedback, now, assignment_id))
        await self.conn.commit()
    
    async def mark_feedback_sent(self, assignment_id: int):
        """Mark feedback as sent to user."""
        await self.conn.execute("""
            UPDATE assignments SET status = 'feedback_sent'
            WHERE assignment_id = ?
        """, (assignment_id,))
        await self.conn.commit()
    
    # Helper methods for row conversion
    def _row_to_user(self, row) -> User:
        """Convert database row to User object."""
        return User(
            user_id=row["user_id"],
            username=row["username"],
            first_name=row["first_name"],
            last_name=row["last_name"],
            tariff=Tariff(row["tariff"]) if row["tariff"] else None,
            referral_partner_id=row["referral_partner_id"],
            start_date=datetime.fromisoformat(row["start_date"]) if row["start_date"] else None,
            current_day=row["current_day"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            mentor_reminders=row["mentor_reminders"] if "mentor_reminders" in row.keys() else 0,
            last_mentor_reminder=datetime.fromisoformat(row["last_mentor_reminder"]) if ("last_mentor_reminder" in row.keys() and row["last_mentor_reminder"]) else None
        )
    
    def _row_to_lesson(self, row) -> Lesson:
        """Convert database row to Lesson object."""
        return Lesson(
            lesson_id=row["lesson_id"],
            day_number=row["day_number"],
            title=row["title"],
            content_text=row["content_text"],
            image_url=row["image_url"],
            video_url=row["video_url"],
            assignment_text=row["assignment_text"],
            created_at=datetime.fromisoformat(row["created_at"])
        )
    
    def _row_to_progress(self, row) -> UserProgress:
        """Convert database row to UserProgress object."""
        return UserProgress(
            progress_id=row["progress_id"],
            user_id=row["user_id"],
            lesson_id=row["lesson_id"],
            day_number=row["day_number"],
            completed=bool(row["completed"]),
            completed_at=datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None,
            created_at=datetime.fromisoformat(row["created_at"])
        )
    
    def _row_to_referral(self, row) -> Referral:
        """Convert database row to Referral object."""
        return Referral(
            referral_id=row["referral_id"],
            partner_id=row["partner_id"],
            referred_user_id=row["referred_user_id"],
            created_at=datetime.fromisoformat(row["created_at"])
        )
    
    def _row_to_assignment(self, row) -> Assignment:
        """Convert database row to Assignment object."""
        media_ids = json.loads(row["submission_media_ids"]) if row["submission_media_ids"] else None
        return Assignment(
            assignment_id=row["assignment_id"],
            user_id=row["user_id"],
            lesson_id=row["lesson_id"],
            day_number=row["day_number"],
            submission_text=row["submission_text"],
            submission_media_ids=media_ids,
            admin_feedback=row["admin_feedback"],
            admin_feedback_at=datetime.fromisoformat(row["admin_feedback_at"]) if row["admin_feedback_at"] else None,
            submitted_at=datetime.fromisoformat(row["submitted_at"]),
            status=row["status"]
        )

