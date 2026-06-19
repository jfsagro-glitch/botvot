# Architecture Overview

This document explains the system architecture and design decisions for the Telegram Course Platform.

## System Components

### 1. Sales & Payment Bot (`bots/sales_bot.py`)
**Purpose**: Handle user acquisition, payment processing, and access granting.

**Key Responsibilities**:
- Process referral links (`/start partner_id`)
- Display course information and tariffs
- Initiate payment processing
- Grant access after successful payment
- Invite users to appropriate Telegram groups
- Send onboarding messages

**Flow**:
1. User enters via `/start` or `/start partner_id`
2. Bot shows course info and tariff options
3. User selects tariff → Payment initiated
4. User completes payment (mock: auto-completes in 5s)
5. Bot checks payment status → Grants access
6. Bot sends onboarding + group invites

### 2. Course Bot (`bots/course_bot.py`)
**Purpose**: Deliver course content and handle user interactions.

**Key Responsibilities**:
- Deliver daily lessons automatically
- Handle assignment submissions
- Route assignments to admins (FEEDBACK/PREMIUM tariffs)
- Deliver admin feedback to users
- Track user progress
- Provide lesson navigation

**Flow**:
1. Scheduler checks for lessons due
2. Bot delivers lesson to user
3. User interacts (submit assignment, ask question)
4. Assignments routed to admin (if tariff includes feedback)
5. Admin replies → Feedback sent to user

### 3. Database Layer (`core/database.py`)
**Purpose**: Abstract data persistence.

**Design**:
- SQLite for simplicity (easily migratable to PostgreSQL)
- Async operations using `aiosqlite`
- Clean separation: models → database → services
- All queries go through Database class

**Tables**:
- `users`: User accounts, tariffs, progress
- `lessons`: Course content
- `user_progress`: Lesson completion tracking
- `referrals`: Partner referral records
- `assignments`: Submissions and feedback

### 4. Service Layer (`services/`)
**Purpose**: Business logic abstraction.

**Services**:
- **UserService**: User management, access control
- **PaymentService**: Payment processing, access granting
- **LessonService**: Lesson retrieval, scheduling logic
- **AssignmentService**: Assignment submission, feedback routing
- **CommunityService**: Group access management
- **ReferralService**: Partner tracking

**Design Pattern**: Each service encapsulates related business logic and database operations.

### 5. Payment System (`payment/`)
**Purpose**: Pluggable payment processing.

**Architecture**:
- Abstract base class `PaymentProcessor`
- Mock implementation for development
- Easy to swap for real providers (Stripe, PayPal, etc.)

**Interface**:
- `create_payment()`: Initiate payment
- `check_payment_status()`: Check completion
- `process_webhook()`: Handle payment notifications

### 6. Scheduling System (`utils/scheduler.py`)
**Purpose**: Automatic lesson delivery.

**How It Works**:
- Background task runs every 5 minutes
- Checks all users with access
- Calculates if lesson should be sent:
  - Day 1: Sent immediately at start_date
  - Day N: Sent at start_date + (N-1) * 24 hours
- Delivers lesson via callback
- Advances user to next day

**Design**: Decoupled from bot logic, uses callback pattern.

## Data Flow

### Payment Flow
```
User → Sales Bot → Payment Service → Payment Processor
                                    ↓
                              Payment Completed
                                    ↓
                          Payment Service → User Service
                                    ↓
                          Access Granted → Onboarding
```

### Lesson Delivery Flow
```
Scheduler (every 5 min) → Check Users
                              ↓
                    Calculate Next Lesson Time
                              ↓
                    Should Send? → Yes
                              ↓
                    Lesson Service → Get Lesson
                              ↓
                    Course Bot → Deliver to User
                              ↓
                    Mark Complete → Advance Day
```

### Assignment Flow
```
User → Course Bot → Assignment Service
                        ↓
                  Check Tariff
                        ↓
        BASIC: Auto-response (no feedback)
        FEEDBACK/PREMIUM: Forward to Admin
                        ↓
                  Admin Replies
                        ↓
                  Assignment Service → Course Bot
                        ↓
                  Feedback Delivered to User
```

## Key Design Decisions

### 1. Separation of Concerns
- **Bots**: Only handle Telegram interactions
- **Services**: Business logic
- **Database**: Data persistence
- **Payment**: Payment abstraction

### 2. Scalability Considerations
- Async/await throughout for non-blocking I/O
- Database abstraction allows easy migration
- Payment abstraction allows provider switching
- Service layer allows horizontal scaling

### 3. Extensibility
- **New Tariffs**: Add to enum, update services
- **New Lessons**: Add to database, auto-scheduled
- **New Payment Provider**: Implement `PaymentProcessor`
- **New Communities**: Update `CommunityService`

### 4. Production Readiness
- Comprehensive error handling
- Logging throughout
- Configuration via environment variables
- Database migrations ready
- Clean code structure

## Extension Points

### Adding a New Payment Provider
1. Create new file: `payment/stripe_payment.py`
2. Inherit from `PaymentProcessor`
3. Implement required methods
4. Update `bots/sales_bot.py` to use new processor

### Adding a New Tariff
1. Add to `Tariff` enum in `core/models.py`
2. Update `PaymentService.TARIFF_PRICES`
3. Update `format_tariff_description()` in `utils/telegram_helpers.py`
4. Update access logic in `User` model methods
5. Update `CommunityService` if affects group access

### Adding Lesson Content
1. Edit `scripts/init_lessons.py`
2. Run script to populate database
3. Or use database directly via `Database.create_lesson()`

### Customizing Messages
- Sales Bot messages: `bots/sales_bot.py`
- Course Bot messages: `bots/course_bot.py`
- Helper formatting: `utils/telegram_helpers.py`

## Security Considerations

### Current Implementation
- Input validation in services
- SQL parameterization (prevents injection)
- Environment variable configuration

### Production Recommendations
- Webhook signature validation for payments
- Rate limiting on bot endpoints
- Input sanitization for user messages
- Database connection pooling
- HTTPS for all external calls
- Secure storage of API keys

## Performance Considerations

### Current Implementation
- Async operations throughout
- Efficient database queries
- Background scheduling (non-blocking)

### Production Optimizations
- Database indexing on frequently queried fields
- Caching for lesson content
- Connection pooling
- Webhook-based payment status (instead of polling)
- Message queue for lesson delivery (if scaling to thousands)

## Monitoring & Maintenance

### Logging
- All bots log important events
- Error logging for debugging
- Payment transaction logging

### Database Maintenance
- Regular backups recommended
- Monitor database size
- Consider archiving old assignments

### Bot Health
- Monitor bot uptime
- Check for failed message deliveries
- Monitor scheduler execution

## Future Enhancements

Potential improvements for production:
1. **Admin Dashboard**: Web interface for managing lessons, viewing stats
2. **Analytics**: Track user engagement, completion rates
3. **Notifications**: Email/SMS reminders for lessons
4. **Multi-language Support**: Internationalization
5. **Course Variants**: Multiple courses, not just one
6. **Certificates**: Auto-generate completion certificates
7. **Referral Rewards**: Partner commission system
8. **Subscription Model**: Recurring payments option

