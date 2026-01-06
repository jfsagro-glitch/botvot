"""
Setup script to configure .env file with your bot token.

Run this script to create your .env file with the Sales Bot token.
You'll still need to add your Course Bot token and other settings.
"""

import os
from pathlib import Path

def create_env_file():
    """Create .env file with Sales Bot token."""
    env_content = """# Telegram Bot Tokens
# Sales Bot Token (GameChangerQ_bot)
SALES_BOT_TOKEN=8320633481:AAH4xZLPOARQL2U7XkvwAgZxDG_VQmoo468

# Course Bot Token - CREATE A SECOND BOT VIA @BotFather
# Go to @BotFather, send /newbot, create a second bot for course delivery
COURSE_BOT_TOKEN=your_course_bot_token_here

# Admin Chat ID (for assignment feedback)
# Get this by:
# 1. Send a message to your Sales Bot: t.me/GameChangerQ_bot
# 2. Visit: https://api.telegram.org/bot8320633481:AAH4xZLPOARQL2U7XkvwAgZxDG_VQmoo468/getUpdates
# 3. Find your chat ID in the response
ADMIN_CHAT_ID=your_admin_chat_id_here

# Telegram Group Chat IDs (optional)
# Add your bots to groups as admins, send a message, then check getUpdates
GENERAL_GROUP_ID=your_general_group_id_here
PREMIUM_GROUP_ID=your_premium_group_id_here

# Database
DATABASE_PATH=./data/course_platform.db

# Course Settings
COURSE_DURATION_DAYS=30
LESSON_INTERVAL_HOURS=24
"""
    
    env_path = Path(".env")
    
    if env_path.exists():
        response = input(".env file already exists. Overwrite? (y/n): ")
        if response.lower() != 'y':
            print("Cancelled. .env file not modified.")
            return
    
    with open(env_path, 'w') as f:
        f.write(env_content)
    
    print("[SUCCESS] .env file created successfully!")
    print("\nNext steps:")
    print("1. Create a second bot via @BotFather for course delivery")
    print("2. Update COURSE_BOT_TOKEN in .env")
    print("3. Get your admin chat ID and update ADMIN_CHAT_ID")
    print("4. (Optional) Set up Telegram groups and update group IDs")
    print("\nSee QUICK_START.md for detailed instructions.")

if __name__ == "__main__":
    create_env_file()

