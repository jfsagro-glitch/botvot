# Quick Start Guide

## Step 1: Configure Your Bots

You've already created your **Sales Bot** (GameChangerQ_bot). Now you need:

### Create Course Bot
1. Go to Telegram and message `@BotFather`
2. Send `/newbot`
3. Follow instructions to create a second bot (e.g., "GameChangerQ_CourseBot")
4. Copy the token and update `COURSE_BOT_TOKEN` in `.env`

### Get Your Admin Chat ID
1. Send any message to your Sales Bot: `t.me/GameChangerQ_bot`
2. Visit: `https://api.telegram.org/bot8320633481:AAH4xZLPOARQL2U7XkvwAgZxDG_VQmoo468/getUpdates`
3. Find your chat ID in the response (look for `"chat":{"id":123456789}`)
4. Update `ADMIN_CHAT_ID` in `.env`

### Create Telegram Groups (Optional but Recommended)
1. Create a Telegram group for general discussion
2. Create another group for premium users (optional)
3. Add both bots as administrators to the groups
4. Send a message in each group
5. Check the getUpdates URL again to find group IDs (they're negative numbers like `-1001234567890`)
6. Update `GENERAL_GROUP_ID` and `PREMIUM_GROUP_ID` in `.env`

## Step 2: Install Dependencies

```bash
pip install -r requirements.txt
```

## Step 3: Initialize Database and Lessons

```bash
python scripts/init_lessons.py
```

**Important**: Edit `scripts/init_lessons.py` to add your actual 30-day course content before running!

## Step 4: Test Your Sales Bot

```bash
python -m bots.sales_bot
```

Then:
1. Open Telegram and go to `t.me/GameChangerQ_bot`
2. Send `/start`
3. You should see course information and tariff options
4. Select a tariff and test payment (mock payment auto-completes in 5 seconds)

## Step 5: Run Both Bots

You need both bots running simultaneously:

**Terminal 1 - Sales Bot:**
```bash
python -m bots.sales_bot
```

**Terminal 2 - Course Bot:**
```bash
python -m bots.course_bot
```

## Security Reminder

⚠️ **IMPORTANT**: Your bot token is sensitive! 
- Never commit `.env` to git (it's already in `.gitignore`)
- Don't share your token publicly
- If token is compromised, revoke it via @BotFather and create a new one

## Next Steps

1. **Add Course Content**: Edit `scripts/init_lessons.py` with your 30-day course
2. **Customize Messages**: Update bot messages in `bots/sales_bot.py` and `bots/course_bot.py`
3. **Test Payment Flow**: Complete a test purchase end-to-end
4. **Test Lesson Delivery**: Verify lessons are sent automatically
5. **Test Assignment Feedback**: Submit an assignment and test admin feedback

## Getting Help

- See `SETUP.md` for detailed setup instructions
- See `README.md` for system overview
- See `ARCHITECTURE.md` for technical details

