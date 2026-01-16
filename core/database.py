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
        self.conn = None
    
    async def connect(self):
        """Create database connection and initialize schema."""
        self.conn = await aiosqlite.connect(self.db_path)
        self.conn.row_factory = aiosqlite.Row
        await self._init_schema()
    
    async def close(self):
        """Close database connection."""
        if getattr(self, "conn", None) is not None:
            await self.conn.close()
        self.conn = None
    
    async def _ensure_connection(self):
        """
        Ensure database connection is established and active.
        
        - Connects if not connected
        - Reconnects if connection is stale/broken
        """
        if getattr(self, "conn", None) is None:
            await self.connect()
            return
        
        try:
            async with self.conn.execute("SELECT 1") as cursor:
                await cursor.fetchone()
        except Exception:
            try:
                await self.close()
            except Exception:
                pass
            await self.connect()
    
    async def _init_schema(self):
        """Initialize database schema."""
        # Users table
        await self.conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                email TEXT,
                tariff TEXT,
                referral_partner_id TEXT,
                start_date TEXT,
                current_day INTEGER DEFAULT 1,
                mentor_reminders INTEGER DEFAULT 0,
                legal_accepted_at TEXT,
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

        # Миграция: добавляем поле legal_accepted_at, если его нет
        try:
            await self.conn.execute("""
                ALTER TABLE users ADD COLUMN legal_accepted_at TEXT
            """)
            await self.conn.commit()
        except Exception:
            # Поле уже существует, игнорируем ошибку
            pass

        # Миграция: добавляем поле email, если его нет
        try:
            await self.conn.execute("""
                ALTER TABLE users ADD COLUMN email TEXT
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
        # Index for hot path: mentor reminders and "has assignment" checks.
        await self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_assignments_user_day
            ON assignments(user_id, day_number)
        """)

        # Assignment intents ("user clicked submit assignment" flag)
        # Used to stop mentor reminders once the user has started submission flow,
        # even if they haven't sent the final answer yet.
        await self.conn.execute("""
            CREATE TABLE IF NOT EXISTS assignment_intents (
                user_id INTEGER NOT NULL,
                day_number INTEGER NOT NULL,
                started_at TEXT NOT NULL,
                PRIMARY KEY (user_id, day_number)
            )
        """)

        # Processed payments table (idempotency for webhooks)
        await self.conn.execute("""
            CREATE TABLE IF NOT EXISTS processed_payments (
                payment_id TEXT PRIMARY KEY,
                processed_at TEXT NOT NULL
            )
        """)

        # Simple key-value settings storage (for runtime binding like curator group chat_id)
        await self.conn.execute("""
            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)

        # Promo codes (discounts applied in SalesBot / PaymentService)
        await self.conn.execute("""
            CREATE TABLE IF NOT EXISTS promo_codes (
                code TEXT PRIMARY KEY,
                discount_type TEXT NOT NULL,          -- 'percent' or 'amount'
                discount_value REAL NOT NULL,         -- percent: 0-100, amount: currency units
                created_at TEXT NOT NULL,
                expires_at TEXT,
                max_uses INTEGER,
                used_count INTEGER NOT NULL DEFAULT 0,
                active INTEGER NOT NULL DEFAULT 1,
                created_by INTEGER
            )
        """)

        # Per-user active promo code (to "auto apply" until cleared)
        await self.conn.execute("""
            CREATE TABLE IF NOT EXISTS user_promo_codes (
                user_id INTEGER PRIMARY KEY,
                promo_code TEXT NOT NULL,
                applied_at TEXT NOT NULL
            )
        """)
        
        # User sessions table (for tracking online time and bot visits)
        await self.conn.execute("""
            CREATE TABLE IF NOT EXISTS user_sessions (
                session_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                bot_type TEXT NOT NULL,
                session_start TEXT NOT NULL,
                session_end TEXT,
                duration_seconds INTEGER,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        """)
        
        # User activity table (for tracking actions and sections)
        await self.conn.execute("""
            CREATE TABLE IF NOT EXISTS user_activity (
                activity_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                bot_type TEXT NOT NULL,
                action_type TEXT NOT NULL,
                section TEXT,
                details TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        """)
        
        # Indexes for performance
        await self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_user_sessions_user_id
            ON user_sessions(user_id)
        """)
        await self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_user_activity_user_id
            ON user_activity(user_id)
        """)
        
        await self.conn.commit()

    # Payment operations (webhook idempotency)
    async def is_payment_processed(self, payment_id: str) -> bool:
        """Return True if payment_id was already processed."""
        await self._ensure_connection()
        async with self.conn.execute(
            "SELECT 1 FROM processed_payments WHERE payment_id = ? LIMIT 1",
            (payment_id,),
        ) as cursor:
            row = await cursor.fetchone()
            return bool(row)

    async def mark_payment_processed(self, payment_id: str):
        """Mark payment_id as processed (idempotent)."""
        await self._ensure_connection()
        now = datetime.utcnow().isoformat()
        # INSERT OR IGNORE to be safe under retries/concurrency
        await self.conn.execute(
            "INSERT OR IGNORE INTO processed_payments (payment_id, processed_at) VALUES (?, ?)",
            (payment_id, now),
        )
        await self.conn.commit()

    async def try_mark_payment_processed(self, payment_id: str) -> bool:
        """
        Attempt to mark payment_id as processed.

        Returns True if the record was inserted by this call, False if it already existed.
        """
        await self._ensure_connection()
        now = datetime.utcnow().isoformat()
        cursor = await self.conn.execute(
            "INSERT OR IGNORE INTO processed_payments (payment_id, processed_at) VALUES (?, ?)",
            (payment_id, now),
        )
        await self.conn.commit()
        return cursor.rowcount == 1

    async def reset_user_data(self, user_id: int):
        """
        Hard reset a user: removes access, progress, assignments and referral records.
        After this, /start will create a clean user again.
        """
        await self._ensure_connection()
        try:
            await self.conn.execute("BEGIN")
            # Order matters due to FK references
            await self.conn.execute("DELETE FROM user_progress WHERE user_id = ?", (user_id,))
            await self.conn.execute("DELETE FROM assignments WHERE user_id = ?", (user_id,))
            await self.conn.execute("DELETE FROM assignment_intents WHERE user_id = ?", (user_id,))
            await self.conn.execute("DELETE FROM referrals WHERE referred_user_id = ?", (user_id,))
            await self.conn.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
            await self.conn.commit()
        except Exception:
            try:
                await self.conn.rollback()
            except Exception:
                pass
            raise

    # App settings (key/value)
    async def get_setting(self, key: str) -> Optional[str]:
        await self._ensure_connection()
        async with self.conn.execute(
            "SELECT value FROM app_settings WHERE key = ?",
            (key,),
        ) as cursor:
            row = await cursor.fetchone()
            return row["value"] if row else None

    async def set_setting(self, key: str, value: str):
        await self._ensure_connection()
        now = datetime.utcnow().isoformat()
        await self.conn.execute(
            """
            INSERT INTO app_settings (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
            """,
            (key, value, now),
        )
        await self.conn.commit()

    # Pricing settings (stored in app_settings)
    @staticmethod
    def _online_price_key(tariff_value: str) -> str:
        return f"price:online:{(tariff_value or '').strip().lower()}"

    @staticmethod
    def _offline_price_key(tariff_key: str) -> str:
        return f"price:offline:{(tariff_key or '').strip().lower()}"

    async def get_online_tariff_price(self, tariff: Tariff, default: float) -> float:
        raw = await self.get_setting(self._online_price_key(tariff.value))
        if raw is None:
            return float(default)
        try:
            return float(str(raw).strip())
        except Exception:
            return float(default)

    async def set_online_tariff_price(self, tariff: Tariff, price: float):
        await self.set_setting(self._online_price_key(tariff.value), str(float(price)))

    async def get_offline_tariff_price(self, tariff_key: str, default: float) -> float:
        raw = await self.get_setting(self._offline_price_key(tariff_key))
        if raw is None:
            return float(default)
        try:
            return float(str(raw).strip())
        except Exception:
            return float(default)

    async def set_offline_tariff_price(self, tariff_key: str, price: float):
        await self.set_setting(self._offline_price_key(tariff_key), str(float(price)))

    # Promo codes
    async def create_promo_code(
        self,
        code: str,
        discount_type: str,
        discount_value: float,
        *,
        max_uses: Optional[int] = None,
        expires_at: Optional[datetime] = None,
        created_by: Optional[int] = None,
    ):
        await self._ensure_connection()
        now = datetime.utcnow().isoformat()
        await self.conn.execute(
            """
            INSERT INTO promo_codes (code, discount_type, discount_value, created_at, expires_at, max_uses, used_count, active, created_by)
            VALUES (?, ?, ?, ?, ?, ?, 0, 1, ?)
            """,
            (
                code.strip(),
                discount_type.strip().lower(),
                float(discount_value),
                now,
                expires_at.isoformat() if expires_at else None,
                int(max_uses) if max_uses is not None else None,
                int(created_by) if created_by is not None else None,
            ),
        )
        await self.conn.commit()

    async def get_valid_promo_code(self, code: str) -> Optional[dict]:
        await self._ensure_connection()
        code = (code or "").strip()
        if not code:
            return None
        async with self.conn.execute(
            """
            SELECT code, discount_type, discount_value, created_at, expires_at, max_uses, used_count, active
            FROM promo_codes
            WHERE code = ?
            """,
            (code,),
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            if int(row["active"] or 0) != 1:
                return None
            if row["expires_at"]:
                try:
                    if datetime.fromisoformat(row["expires_at"]) < datetime.utcnow():
                        return None
                except Exception:
                    return None
            max_uses = row["max_uses"]
            used_count = int(row["used_count"] or 0)
            if max_uses is not None and used_count >= int(max_uses):
                return None
            return dict(row)

    async def increment_promo_code_use(self, code: str) -> bool:
        await self._ensure_connection()
        code = (code or "").strip()
        if not code:
            return False
        cursor = await self.conn.execute(
            """
            UPDATE promo_codes
            SET used_count = used_count + 1
            WHERE code = ?
              AND active = 1
              AND (max_uses IS NULL OR used_count < max_uses)
              AND (expires_at IS NULL OR expires_at > ?)
            """,
            (code, datetime.utcnow().isoformat()),
        )
        await self.conn.commit()
        return cursor.rowcount == 1

    async def list_promo_codes(self, limit: int = 20) -> list[dict]:
        await self._ensure_connection()
        async with self.conn.execute(
            """
            SELECT code, discount_type, discount_value, created_at, expires_at, max_uses, used_count, active
            FROM promo_codes
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (int(limit),),
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def deactivate_promo_code(self, code: str) -> bool:
        """Soft-delete: mark promo code inactive."""
        await self._ensure_connection()
        code = (code or "").strip()
        if not code:
            return False
        cursor = await self.conn.execute(
            "UPDATE promo_codes SET active = 0 WHERE code = ?",
            (code,),
        )
        await self.conn.commit()
        return cursor.rowcount == 1

    # User promo codes
    async def set_user_promo_code(self, user_id: int, promo_code: str):
        await self._ensure_connection()
        now = datetime.utcnow().isoformat()
        await self.conn.execute(
            """
            INSERT INTO user_promo_codes (user_id, promo_code, applied_at)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET promo_code=excluded.promo_code, applied_at=excluded.applied_at
            """,
            (int(user_id), (promo_code or "").strip(), now),
        )
        await self.conn.commit()

    async def get_user_promo_code(self, user_id: int) -> Optional[str]:
        await self._ensure_connection()
        async with self.conn.execute(
            "SELECT promo_code FROM user_promo_codes WHERE user_id = ?",
            (int(user_id),),
        ) as cursor:
            row = await cursor.fetchone()
            return (row["promo_code"] if row else None) or None

    async def clear_user_promo_code(self, user_id: int):
        await self._ensure_connection()
        await self.conn.execute("DELETE FROM user_promo_codes WHERE user_id = ?", (int(user_id),))
        await self.conn.commit()
    
    # User operations
    async def get_user(self, user_id: int) -> Optional[User]:
        """Get user by ID."""
        await self._ensure_connection()
        
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
        """Create a new user. Raises ValueError if user limit (200) is reached."""
        await self._ensure_connection()
        
        # Check user limit (200 users max)
        async with self.conn.execute("SELECT COUNT(*) FROM users") as cursor:
            row = await cursor.fetchone()
            total_users = row[0] if row else 0
        
        if total_users >= 200:
            raise ValueError("Достигнут лимит пользователей (200). Регистрация новых пользователей временно недоступна.")
        
        now = datetime.utcnow().isoformat()
        await self.conn.execute("""
            INSERT INTO users (user_id, username, first_name, last_name, email,
                             mentor_reminders, legal_accepted_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, NULL, 0, NULL, ?, ?)
        """, (user_id, username, first_name, last_name, now, now))
        await self.conn.commit()
        return await self.get_user(user_id)
    
    async def update_user(self, user: User):
        """Update user information."""
        await self._ensure_connection()
        
        await self.conn.execute("""
            UPDATE users SET
                username = ?, first_name = ?, last_name = ?, email = ?,
                tariff = ?, referral_partner_id = ?,
                start_date = ?, current_day = ?, mentor_reminders = ?, last_mentor_reminder = ?,
                legal_accepted_at = ?,
                updated_at = ?
            WHERE user_id = ?
        """, (
            user.username, user.first_name, user.last_name, getattr(user, "email", None),
            user.tariff.value if user.tariff else None,
            user.referral_partner_id,
            user.start_date.isoformat() if user.start_date else None,
            user.current_day, user.mentor_reminders,
            user.last_mentor_reminder.isoformat() if user.last_mentor_reminder else None,
            user.legal_accepted_at.isoformat() if getattr(user, "legal_accepted_at", None) else None,
            datetime.utcnow().isoformat(),
            user.user_id
        ))
        await self.conn.commit()
    
    async def get_users_with_access(self) -> List[User]:
        """Get all users with active course access."""
        await self._ensure_connection()
        async with self.conn.execute(
            "SELECT * FROM users WHERE tariff IS NOT NULL"
        ) as cursor:
            rows = await cursor.fetchall()
            return [self._row_to_user(row) for row in rows]
    
    # Lesson operations
    async def get_lesson_by_day(self, day_number: int) -> Optional[Lesson]:
        """Get lesson by day number."""
        await self._ensure_connection()
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
        await self._ensure_connection()
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
        await self._ensure_connection()
        async with self.conn.execute(
            "SELECT * FROM lessons ORDER BY day_number"
        ) as cursor:
            rows = await cursor.fetchall()
            return [self._row_to_lesson(row) for row in rows]
    
    # Progress operations
    async def get_user_progress(self, user_id: int, lesson_id: int) -> Optional[UserProgress]:
        """Get user progress for a specific lesson."""
        await self._ensure_connection()
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
        await self._ensure_connection()
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
        await self._ensure_connection()
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
        await self._ensure_connection()
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
        await self._ensure_connection()
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
        await self._ensure_connection()
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
        await self._ensure_connection()
        
        async with self.conn.execute(
            "SELECT COUNT(*) FROM assignments WHERE user_id = ? AND day_number = ?",
            (user_id, day_number)
        ) as cursor:
            row = await cursor.fetchone()
            count = row[0] if row else 0
            return count > 0

    async def mark_assignment_intent(self, user_id: int, day_number: int):
        """Mark that user clicked 'submit assignment' for a specific day (idempotent)."""
        await self._ensure_connection()
        now = datetime.utcnow().isoformat()
        await self.conn.execute(
            """
            INSERT OR IGNORE INTO assignment_intents (user_id, day_number, started_at)
            VALUES (?, ?, ?)
            """,
            (user_id, day_number, now),
        )
        await self.conn.commit()

    async def has_assignment_intent_for_day(self, user_id: int, day_number: int) -> bool:
        """Return True if user already clicked 'submit assignment' for this day."""
        await self._ensure_connection()
        async with self.conn.execute(
            "SELECT 1 FROM assignment_intents WHERE user_id = ? AND day_number = ? LIMIT 1",
            (user_id, day_number),
        ) as cursor:
            row = await cursor.fetchone()
            return bool(row)

    async def has_assignment_activity_for_day(self, user_id: int, day_number: int) -> bool:
        """
        Fast check used by mentor reminders:
        returns True if user has EITHER started assignment flow (clicked submit) OR submitted an assignment.
        Implemented as a single DB round-trip.
        """
        await self._ensure_connection()
        async with self.conn.execute(
            """
            SELECT
              EXISTS(SELECT 1 FROM assignment_intents WHERE user_id = ? AND day_number = ?) OR
              EXISTS(SELECT 1 FROM assignments WHERE user_id = ? AND day_number = ?)
            """,
            (user_id, day_number, user_id, day_number),
        ) as cursor:
            row = await cursor.fetchone()
            return bool(row[0]) if row else False
    
    async def get_pending_assignments(self) -> List[Assignment]:
        """Get all assignments pending admin feedback."""
        await self._ensure_connection()
        async with self.conn.execute("""
            SELECT * FROM assignments 
            WHERE status = 'submitted' AND admin_feedback IS NULL
            ORDER BY submitted_at
        """) as cursor:
            rows = await cursor.fetchall()
            return [self._row_to_assignment(row) for row in rows]
    
    async def update_assignment_feedback(self, assignment_id: int, feedback: str):
        """Update assignment with admin feedback."""
        await self._ensure_connection()
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
        await self._ensure_connection()
        await self.conn.execute("""
            UPDATE assignments SET status = 'feedback_sent'
            WHERE assignment_id = ?
        """, (assignment_id,))
        await self.conn.commit()
    
    # User statistics methods
    async def log_user_session(self, user_id: int, bot_type: str, session_start: datetime, session_end: Optional[datetime] = None, duration_seconds: Optional[int] = None):
        """Log user session (bot visit)."""
        await self._ensure_connection()
        await self.conn.execute("""
            INSERT INTO user_sessions (user_id, bot_type, session_start, session_end, duration_seconds)
            VALUES (?, ?, ?, ?, ?)
        """, (user_id, bot_type, session_start.isoformat(), session_end.isoformat() if session_end else None, duration_seconds))
        await self.conn.commit()
    
    async def log_user_activity(self, user_id: int, bot_type: str, action_type: str, section: Optional[str] = None, details: Optional[str] = None):
        """Log user activity (action, section visited)."""
        await self._ensure_connection()
        now = datetime.utcnow().isoformat()
        await self.conn.execute("""
            INSERT INTO user_activity (user_id, bot_type, action_type, section, details, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (user_id, bot_type, action_type, section, details, now))
        await self.conn.commit()
    
    async def get_user_statistics(self, user_id: int) -> dict:
        """Get detailed statistics for a user."""
        await self._ensure_connection()
        
        stats = {
            "user_id": user_id,
            "total_online_time_seconds": 0,
            "total_bot_visits": 0,
            "sales_bot_visits": 0,
            "course_bot_visits": 0,
            "questions_count": 0,
            "assignments_submitted": 0,
            "assignments_completed": 0,
            "activity_by_section": {},
            "activity_by_action": {}
        }
        
        # Total online time
        async with self.conn.execute("""
            SELECT SUM(duration_seconds) as total_time
            FROM user_sessions
            WHERE user_id = ? AND duration_seconds IS NOT NULL
        """, (user_id,)) as cursor:
            row = await cursor.fetchone()
            stats["total_online_time_seconds"] = row[0] if row and row[0] else 0
        
        # Bot visits
        async with self.conn.execute("""
            SELECT bot_type, COUNT(*) as count
            FROM user_sessions
            WHERE user_id = ?
            GROUP BY bot_type
        """, (user_id,)) as cursor:
            rows = await cursor.fetchall()
            for row in rows:
                count = row[1]
                stats["total_bot_visits"] += count
                if row[0] == "sales":
                    stats["sales_bot_visits"] = count
                elif row[0] == "course":
                    stats["course_bot_visits"] = count
        
        # Questions count - we'll need to track this separately, for now estimate from activity
        async with self.conn.execute("""
            SELECT COUNT(*) FROM user_activity
            WHERE user_id = ? AND action_type = 'question'
        """, (user_id,)) as cursor:
            row = await cursor.fetchone()
            stats["questions_count"] = row[0] if row else 0
        
        # Assignments
        async with self.conn.execute("""
            SELECT COUNT(*) FROM assignments WHERE user_id = ?
        """, (user_id,)) as cursor:
            row = await cursor.fetchone()
            stats["assignments_submitted"] = row[0] if row else 0
        
        async with self.conn.execute("""
            SELECT COUNT(*) FROM assignments
            WHERE user_id = ? AND admin_feedback IS NOT NULL AND admin_feedback != ''
        """, (user_id,)) as cursor:
            row = await cursor.fetchone()
            stats["assignments_completed"] = row[0] if row else 0
        
        # Activity by section
        async with self.conn.execute("""
            SELECT section, COUNT(*) as count
            FROM user_activity
            WHERE user_id = ? AND section IS NOT NULL
            GROUP BY section
            ORDER BY count DESC
        """, (user_id,)) as cursor:
            rows = await cursor.fetchall()
            for row in rows:
                stats["activity_by_section"][row[0]] = row[1]
        
        # Activity by action
        async with self.conn.execute("""
            SELECT action_type, COUNT(*) as count
            FROM user_activity
            WHERE user_id = ?
            GROUP BY action_type
            ORDER BY count DESC
        """, (user_id,)) as cursor:
            rows = await cursor.fetchall()
            for row in rows:
                stats["activity_by_action"][row[0]] = row[1]
        
        return stats
    
    # Helper methods for row conversion
    def _row_to_user(self, row) -> User:
        """Convert database row to User object."""
        return User(
            user_id=row["user_id"],
            username=row["username"],
            first_name=row["first_name"],
            last_name=row["last_name"],
            email=row["email"] if ("email" in row.keys() and row["email"]) else None,
            tariff=Tariff(row["tariff"]) if row["tariff"] else None,
            referral_partner_id=row["referral_partner_id"],
            start_date=datetime.fromisoformat(row["start_date"]) if row["start_date"] else None,
            current_day=row["current_day"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            mentor_reminders=row["mentor_reminders"] if "mentor_reminders" in row.keys() else 0,
            last_mentor_reminder=datetime.fromisoformat(row["last_mentor_reminder"]) if ("last_mentor_reminder" in row.keys() and row["last_mentor_reminder"]) else None,
            legal_accepted_at=datetime.fromisoformat(row["legal_accepted_at"]) if ("legal_accepted_at" in row.keys() and row["legal_accepted_at"]) else None
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
