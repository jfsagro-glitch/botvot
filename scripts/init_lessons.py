"""
Script to initialize sample lessons in the database.

Run this script to populate the database with course lessons.
You can modify this script to add your actual course content.
"""

import asyncio
from core.database import Database
from core.config import Config


async def init_lessons():
    """Initialize sample lessons."""
    db = Database()
    await db.connect()
    
    try:
        # Sample lessons - replace with your actual course content
        lessons = [
            {
                "day_number": 1,
                "title": "Welcome to the Course",
                "content_text": (
                    "Welcome to Day 1! This is your first lesson.\n\n"
                    "In this course, you'll learn valuable skills over 30 days.\n\n"
                    "Let's get started!"
                ),
                "assignment_text": "Introduce yourself in the discussion group. What do you hope to learn?"
            },
            {
                "day_number": 2,
                "title": "Foundation Concepts",
                "content_text": (
                    "Today we'll cover the foundational concepts.\n\n"
                    "These concepts form the basis for everything else in the course."
                ),
                "assignment_text": "Write a short summary of the key concepts you learned today."
            },
            {
                "day_number": 3,
                "title": "Practical Application",
                "content_text": (
                    "Now let's apply what we've learned.\n\n"
                    "Practice makes perfect!"
                ),
                "assignment_text": "Complete the practice exercise and share your results."
            },
            # Add more lessons here...
            # For a full 30-day course, you would add lessons for days 4-30
        ]
        
        # Create lessons
        for lesson_data in lessons:
            existing = await db.get_lesson_by_day(lesson_data["day_number"])
            if not existing:
                await db.create_lesson(
                    day_number=lesson_data["day_number"],
                    title=lesson_data["title"],
                    content_text=lesson_data["content_text"],
                    assignment_text=lesson_data.get("assignment_text")
                )
                print(f"Created lesson for day {lesson_data['day_number']}")
            else:
                print(f"Lesson for day {lesson_data['day_number']} already exists")
        
        print("\n✅ Lessons initialized successfully!")
        print(f"Created {len(lessons)} lessons.")
        print("\nNote: This is a sample. Add your actual course content by:")
        print("1. Editing this script")
        print("2. Or using the database directly")
        print("3. Or creating an admin interface")
        
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(init_lessons())

