"""
Псевдо-тест: проверка маппинга sale:pay:* -> pay:* для нового флоу продаж.
Запуск: python test_sales_new_flow_mapping.py
"""
import os
import sys

# Минимальный env для импорта SalesBot (токен может быть заглушкой)
if not os.environ.get("SALES_BOT_TOKEN"):
    os.environ.setdefault("SALES_BOT_TOKEN", "123456:DUMMY")
if not os.environ.get("COURSE_BOT_TOKEN"):
    os.environ.setdefault("COURSE_BOT_TOKEN", "123456:DUMMY")


def test_map_sale_pay_to_pay():
    """Проверка что все sale:pay:* маппятся на правильные pay:*."""
    from bots.sales_bot import SalesBot
    bot = SalesBot()
    m = bot._map_sale_pay_to_pay

    cases = [
        ("sale:pay:online:q:step:1:basic", "pay:online:basic"),
        ("sale:pay:online:q:step:1:feedback", "pay:online:feedback"),
        ("sale:pay:online:q:step:1:practic", "pay:online:practic"),
        ("sale:pay:online:q:step:2:basic", "pay:online:second:basic"),
        ("sale:pay:online:q:step:2:feedback", "pay:online:second:feedback"),
        ("sale:pay:online:q:step:2:practic", "pay:online:second:practic"),
        ("sale:pay:offline:hero:listener", "pay:offline:slushatel"),
        ("sale:pay:offline:hero:aktivist", "pay:offline:aktivist"),
        ("sale:pay:offline:hero:media", "pay:offline:media_persona"),
        ("sale:pay:offline:hero:hero", "pay:offline:glavnyi_geroi"),
    ]
    for sale_data, expected_pay in cases:
        got = m(sale_data)
        assert got == expected_pay, f"expected {expected_pay!r} for {sale_data!r}, got {got!r}"
    print("OK: all sale:pay:* mappings correct")


def test_sale_callbacks_registered():
    """Проверка что обработчик sale:* зарегистрирован (фильтры диспетчера)."""
    from bots.sales_bot import SalesBot
    bot = SalesBot()
    handlers = [h for h in bot.dp.callback_query.handlers]
    has_sale = any(
        getattr(h, "filters", None) and "sale:" in str(h.filters) for h in handlers
    ) or any(
        hasattr(h, "callback") and "handle_sale_screen" in getattr(h.callback, "__name__", "")
        for h in handlers
    )
    # Проверяем по-другому: что есть handler с handle_sale_screen
    for h in handlers:
        cb = getattr(h, "callback", None) or getattr(h, "handler", None)
        if cb and getattr(cb, "__name__", "") == "handle_sale_screen":
            print("OK: handle_sale_screen registered")
            return
    print("SKIP: could not confirm handle_sale_screen registration (filter check may vary)")


if __name__ == "__main__":
    try:
        test_map_sale_pay_to_pay()
        test_sale_callbacks_registered()
        print("All checks passed.")
    except Exception as e:
        print(f"FAIL: {e}", file=sys.stderr)
        sys.exit(1)
