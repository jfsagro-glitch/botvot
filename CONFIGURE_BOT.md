# Configure Your Bot Token

Your Sales Bot token has been provided. Here's how to set it up:

## Step 1: Create .env File

Create a file named `.env` in the project root with the following content:

```env
# Telegram Bot Tokens
# Sales Bot Token (StartNowQ_bot)
SALES_BOT_TOKEN=your_sales_bot_token_here

# Course Bot Token - CREATE A SECOND BOT VIA @BotFather
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

## Step 2: Get Your Admin Chat ID

1. Open Telegram and go to: `t.me/StartNowQ_bot`
2. Send any message to your bot (e.g., `/start`)
3. Visit this URL in your browser:
   ```
   https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates
   ```
4. Look for your chat ID in the JSON response. It will look like:
   ```json
   "chat":{"id":123456789,"first_name":"Your Name"}
   ```
5. Copy that number and update `ADMIN_CHAT_ID` in `.env`

## Step 3: Create Course Bot

You need a second bot for course delivery:

1. Go to Telegram and message `@BotFather`
2. Send `/newbot`
3. Follow the instructions to create a bot (e.g., name it "StartNowAI_bot")
4. Copy the token you receive
5. Update `COURSE_BOT_TOKEN` in `.env`

## Step 4: (Optional) Set Up Telegram Groups

1. Create a Telegram group for general discussion
2. Create another group for premium users (optional)
3. Add both bots as administrators to the groups
4. Send a message in each group
5. Visit the getUpdates URL again to find group IDs (they're negative numbers like `-1001234567890`)
6. Update `GENERAL_GROUP_ID` and `PREMIUM_GROUP_ID` in `.env`

## Step 5: Test Your Bot

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

2. Initialize the database:
   ```bash
   python scripts/init_lessons.py
   ```

3. Run the Sales Bot:
   ```bash
   python -m bots.sales_bot
   ```

4. Test in Telegram:
   - Go to `t.me/StartNowQ_bot`
   - Send `/start`
   - You should see course information and tariff options

## Security Reminder

⚠️ **Keep your token secure!**
- Never commit `.env` to git (it's already in `.gitignore`)
- Don't share your token publicly
- If your token is compromised, revoke it via @BotFather and create a new one

## Next Steps

Once your bot is running:
1. See `QUICK_START.md` for next steps
2. See `SETUP.md` for detailed setup instructions
3. Edit `scripts/init_lessons.py` to add your actual course content

