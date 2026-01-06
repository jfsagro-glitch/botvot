# Telegram Course Platform

A scalable, automated Telegram-based course ecosystem with payments, partner referrals, automated lessons, assignments, feedback, and community access.

## Architecture

The system consists of three main components:

1. **Sales & Payment Bot**: Handles referrals, course presentation, payment processing, and access granting
2. **Course Bot**: Delivers automated daily lessons, manages assignments, and handles user questions
3. **Community Groups**: Telegram groups with tiered access (General for all paid users, Premium for PREMIUM tariff)

## Project Structure

```
BOTVOT/
├── bots/
│   ├── sales_bot.py          # Sales & Payment Bot
│   └── course_bot.py          # Course Delivery Bot
├── core/
│   ├── models.py              # Data models (User, Tariff, Lesson, etc.)
│   ├── database.py            # Database abstraction layer
│   └── config.py              # Configuration management
├── services/
│   ├── user_service.py        # User management
│   ├── payment_service.py     # Payment processing
│   ├── lesson_service.py      # Lesson delivery and scheduling
│   ├── referral_service.py    # Partner referral tracking
│   ├── assignment_service.py  # Assignment routing and feedback
│   └── community_service.py   # Group access management
├── payment/
│   ├── __init__.py
│   ├── base.py                # Payment system abstraction
│   └── mock_payment.py        # Mock payment implementation
├── utils/
│   ├── scheduler.py           # Lesson scheduling logic
│   └── telegram_helpers.py   # Telegram utility functions
├── requirements.txt
├── .env.example
└── README.md
```

## Setup

### Prerequisites
- Python 3.8+
- Telegram Bot tokens (create via @BotFather)
- Telegram groups for community (optional but recommended)

### Installation

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Create `.env` file (copy from `.env.example`):
```bash
# Copy the example file
cp .env.example .env
```

3. Configure `.env`:
   - `SALES_BOT_TOKEN`: Token from @BotFather for sales bot
   - `COURSE_BOT_TOKEN`: Token from @BotFather for course bot
   - `ADMIN_CHAT_ID`: Your Telegram chat ID (for assignment feedback)
   - `GENERAL_GROUP_ID`: General discussion group ID (e.g., `-1001234567890`)
   - `PREMIUM_GROUP_ID`: Premium group ID (optional)
   - `DATABASE_PATH`: Path to SQLite database file

4. Initialize sample lessons:
```bash
python scripts/init_lessons.py
```

5. Run the bots (in separate terminals):
```bash
# Terminal 1: Sales Bot
python -m bots.sales_bot

# Terminal 2: Course Bot
python -m bots.course_bot
```

### Getting Telegram Chat IDs

- **Admin Chat ID**: Send a message to your bot, then visit `https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates` to find your chat ID
- **Group IDs**: Add bot to group as admin, send a message, then check the same API endpoint

## Key Features

### Tariffs
- **BASIC**: Content only, no feedback
- **FEEDBACK**: Content + leader feedback
- **PREMIUM**: Content + feedback + premium community

### Lesson System
- 30-day automated course flow
- One lesson per day, sent automatically
- Lessons can include text, images, video links, and assignments
- Persistent day tracking per user

### Assignment System
- **BASIC**: Automatic response explaining no feedback
- **FEEDBACK/PREMIUM**: Assignment forwarded to admin, reply routed back

### Community Access
- Automatic group invitations based on tariff
- General discussion group (all paid users)
- Premium group (PREMIUM tariff only)

## Extending the System

### Adding New Lessons

**Method 1: Using the script**
Edit `scripts/init_lessons.py` and add your lesson data, then run:
```bash
python scripts/init_lessons.py
```

**Method 2: Direct database**
```python
from core.database import Database
import asyncio

async def add_lesson():
    db = Database()
    await db.connect()
    await db.create_lesson(
        day_number=4,
        title="Your Lesson Title",
        content_text="Lesson content here...",
        image_url="https://example.com/image.jpg",  # Optional
        video_url="https://youtube.com/watch?v=...",  # Optional
        assignment_text="Assignment description..."  # Optional
    )
    await db.close()

asyncio.run(add_lesson())
```

Lessons are automatically scheduled based on `day_number` (1-30).

### Adding New Tariffs

1. **Add to enum** in `core/models.py`:
```python
class Tariff(str, Enum):
    BASIC = "basic"
    FEEDBACK = "feedback"
    PREMIUM = "premium"
    NEW_TARIFF = "new_tariff"  # Add here
```

2. **Add price** in `services/payment_service.py`:
```python
TARIFF_PRICES = {
    Tariff.BASIC: 100.0,
    Tariff.FEEDBACK: 200.0,
    Tariff.PREMIUM: 300.0,
    Tariff.NEW_TARIFF: 250.0,  # Add here
}
```

3. **Add description** in `utils/telegram_helpers.py`:
```python
def format_tariff_description(tariff: Tariff) -> str:
    descriptions = {
        # ... existing ...
        Tariff.NEW_TARIFF: "Your description here"
    }
```

4. **Update access logic** in `core/models.py` User class methods (`can_receive_feedback()`, `has_premium_access()`, etc.)

5. **Update community service** if tariff affects group access

### Adding New Communities

1. Add group chat ID to `.env`:
```
NEW_GROUP_ID=-1001234567890
```

2. Update `services/community_service.py`:
```python
def get_groups_for_user(self, user: User) -> List[str]:
    groups = []
    # ... existing logic ...
    
    # Add new group for specific tariff
    if user.tariff == Tariff.NEW_TARIFF and self.new_group_id:
        groups.append(self.new_group_id)
    
    return groups
```

3. Update bot invitation logic in `bots/sales_bot.py` `_grant_access_and_notify()` method

### Integrating Real Payment System

Replace `payment/mock_payment.py` with your payment provider:

1. Create new file (e.g., `payment/stripe_payment.py`)
2. Inherit from `PaymentProcessor` in `payment/base.py`
3. Implement required methods:
   - `create_payment()` - Create payment and return payment URL
   - `check_payment_status()` - Check if payment completed
   - `process_webhook()` - Handle payment webhooks
4. Update `bots/sales_bot.py` to use your payment processor:
```python
from payment.stripe_payment import StripePaymentProcessor

self.payment_processor = StripePaymentProcessor(api_key="...")
```

## Database Schema

- **users**: User information, tariff, referral source, start date, current day
- **lessons**: Lesson content and metadata (day_number, title, content, media, assignments)
- **user_progress**: User lesson progress and completion tracking
- **referrals**: Partner referral tracking (partner_id, referred_user_id)
- **assignments**: Assignment submissions and feedback (submission, admin_feedback, status)

## Workflow

### User Journey

1. **Discovery**: User clicks referral link or direct bot link
2. **Selection**: User views course info and selects tariff
3. **Payment**: User completes payment via payment processor
4. **Access**: System grants access, sends onboarding message, invites to groups
5. **Learning**: Course bot delivers daily lessons automatically
6. **Assignments**: User submits assignments (if tariff includes feedback)
7. **Feedback**: Admin reviews and sends feedback (FEEDBACK/PREMIUM tariffs)

### Admin Workflow

1. **Assignment Review**: Admin receives assignment submissions in admin chat
2. **Feedback**: Admin replies to assignment message with feedback
3. **Delivery**: System automatically sends feedback to user

## Architecture Details

### Service Layer
- **UserService**: User management and access control
- **PaymentService**: Payment processing and access granting
- **LessonService**: Lesson retrieval and scheduling logic
- **AssignmentService**: Assignment submission and feedback routing
- **CommunityService**: Group access management
- **ReferralService**: Partner referral tracking

### Scheduling System
- Background scheduler checks every 5 minutes for lessons to deliver
- Calculates expected delivery time based on user start_date + day_number
- Automatically advances users to next day after lesson delivery

### Payment Flow
1. User selects tariff → Payment initiated
2. Payment processor creates payment → Returns payment URL
3. User completes payment → Webhook or status check
4. Payment confirmed → Access granted automatically

## Production Considerations

- **Database**: Consider migrating to PostgreSQL for production
- **Payment**: Replace mock payment with real provider (Stripe, PayPal, etc.)
- **Scaling**: Use webhooks instead of polling for payment status
- **Groups**: Implement actual Telegram group invitation API calls
- **Monitoring**: Add logging and error tracking
- **Backup**: Implement database backups
- **Security**: Validate webhook signatures, sanitize user input

