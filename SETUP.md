# Setup Guide

This guide will help you set up and run the Telegram Course Platform.

## Step 1: Create Telegram Bots

1. Open Telegram and search for `@BotFather`
2. Send `/newbot` command
3. Follow instructions to create two bots:
   - **Sales Bot**: For handling payments and access
   - **Course Bot**: For delivering lessons
4. Save the bot tokens you receive

## Step 2: Get Chat IDs

### Admin Chat ID
1. Start a chat with your bot
2. Send any message to the bot
3. Visit: `https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates`
4. Find your chat ID in the response (look for `"chat":{"id":123456789}`)

### Group Chat IDs
1. Create or use existing Telegram groups
2. Add your bots to the groups as administrators
3. Send a message in each group
4. Visit the same API endpoint to find group IDs (they're negative numbers like `-1001234567890`)

## Step 3: Install Dependencies

```bash
pip install -r requirements.txt
```

## Step 4: Configure Environment

1. Create a `.env` file in the project root:

```env
# Telegram Bot Tokens
SALES_BOT_TOKEN=your_sales_bot_token_here
COURSE_BOT_TOKEN=your_course_bot_token_here

# Admin Chat ID (for assignment feedback)
ADMIN_CHAT_ID=your_admin_chat_id_here

# Telegram Group Chat IDs
GENERAL_GROUP_ID=your_general_group_id_here
PREMIUM_GROUP_ID=your_premium_group_id_here

# Database
DATABASE_PATH=./data/course_platform.db

# Course Settings
COURSE_DURATION_DAYS=30
LESSON_INTERVAL_HOURS=24
```

2. Replace all placeholder values with your actual values

## Step 5: Initialize Database and Lessons

```bash
python scripts/init_lessons.py
```

This will:
- Create the database
- Initialize the schema
- Add sample lessons (you should edit `scripts/init_lessons.py` with your actual course content)

## Step 6: Run the Bots

You need to run both bots simultaneously. Open two terminal windows:

### Terminal 1: Sales Bot
```bash
python -m bots.sales_bot
```

### Terminal 2: Course Bot
```bash
python -m bots.course_bot
```

## Step 7: Test the System

1. **Test Sales Bot**:
   - Send `/start` to your sales bot
   - Select a tariff
   - Complete payment (mock payment auto-completes in 5 seconds)
   - Verify you receive onboarding message

2. **Test Course Bot**:
   - Send `/start` to your course bot
   - Verify you receive your first lesson
   - Test assignment submission (if you have FEEDBACK/PREMIUM tariff)

3. **Test Assignment Feedback**:
   - Submit an assignment via course bot
   - Check admin chat for assignment notification
   - Reply to the assignment message with feedback
   - Verify user receives feedback

## Troubleshooting

### Bot not responding
- Check that bot tokens are correct in `.env`
- Verify bots are running (check terminal output)
- Make sure you've started the bot with `/start` command

### Database errors
- Ensure `data/` directory exists and is writable
- Check database path in `.env`
- Try deleting database file and re-running `init_lessons.py`

### Lessons not being delivered
- Check that lessons exist in database
- Verify user has active access (tariff assigned)
- Check scheduler is running (should see logs in course bot terminal)
- Verify user's `start_date` is set

### Assignment feedback not working
- Verify `ADMIN_CHAT_ID` is correct
- Make sure you're replying to the assignment message (not sending new message)
- Check that assignment ID is in the message you're replying to

## Next Steps

1. **Add Your Course Content**: Edit `scripts/init_lessons.py` with your actual 30-day course content
2. **Customize Messages**: Update bot messages in `bots/sales_bot.py` and `bots/course_bot.py`
3. **Integrate Real Payment**: Replace mock payment with your payment provider (see README.md)
4. **Set Up Groups**: Configure actual group invitation logic
5. **Deploy**: Consider deploying to a server for 24/7 operation

## Production Deployment

For production use, consider:

- **Process Manager**: Use `supervisor` or `systemd` to manage bot processes
- **Database**: Migrate to PostgreSQL for better performance
- **Monitoring**: Add logging and error tracking (Sentry, etc.)
- **Backups**: Set up automated database backups
- **Webhooks**: Use Telegram webhooks instead of polling for better performance
- **Security**: Implement proper webhook signature validation for payments

