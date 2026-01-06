# Настройка реальной оплаты

## Подключение YooKassa

YooKassa - популярная платежная система в России и странах СНГ.

### Шаг 1: Регистрация в YooKassa

1. Зарегистрируйтесь на https://yookassa.ru/
2. Создайте магазин (Shop)
3. Получите Shop ID и Secret Key в настройках магазина

### Шаг 2: Настройка переменных окружения

Добавьте в файл `.env`:

```env
# Выбор платежной системы: "mock" или "yookassa"
PAYMENT_PROVIDER=yookassa

# YooKassa настройки
YOOKASSA_SHOP_ID=ваш_shop_id
YOOKASSA_SECRET_KEY=ваш_secret_key
YOOKASSA_RETURN_URL=https://t.me/StartNowQ_bot

# Валюта платежей (RUB, USD, EUR и т.д.)
PAYMENT_CURRENCY=RUB
```

### Шаг 3: Установка библиотеки

```bash
pip install yookassa
```

### Шаг 4: Настройка webhook (опционально)

Для автоматической обработки платежей настройте webhook в YooKassa:

1. В настройках магазина YooKassa найдите раздел "Webhook"
2. Укажите URL вашего сервера: `https://your-domain.com/payment/webhook`
3. Создайте endpoint для обработки webhook (см. пример ниже)

### Шаг 5: Перезапуск бота

После настройки перезапустите бота:

```bash
python -m bots.sales_bot
```

## Пример обработки webhook

Если вы хотите обрабатывать webhook автоматически, создайте endpoint:

```python
from aiohttp import web
from payment.yookassa_payment import YooKassaPaymentProcessor
from services.payment_service import PaymentService

async def webhook_handler(request):
    """Обработчик webhook от YooKassa"""
    webhook_data = await request.json()
    
    # Обработать webhook через payment processor
    payment_data = await payment_processor.process_webhook(webhook_data)
    
    if payment_data:
        # Предоставить доступ пользователю
        await payment_service.process_payment_completion(
            payment_id=payment_data["payment_id"],
            webhook_data=webhook_data
        )
    
    return web.Response(text="OK")

app = web.Application()
app.router.add_post('/payment/webhook', webhook_handler)
```

## Тестирование

Для тестирования используйте тестовые данные YooKassa:
- Shop ID: тестовый ID из личного кабинета
- Secret Key: тестовый ключ из личного кабинета
- Тестовые карты: https://yookassa.ru/developers/payment-acceptance/testing-and-going-live/testing

## Переключение обратно на mock оплату

Если нужно вернуться к тестовой оплате:

```env
PAYMENT_PROVIDER=mock
```

## Другие платежные системы

Система поддерживает подключение других платежных систем:
- Stripe
- PayPal
- Другие через интерфейс `PaymentProcessor`

Создайте новый файл в `payment/` по аналогии с `yookassa_payment.py` и реализуйте методы интерфейса.

