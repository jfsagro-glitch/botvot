# Экраны нового линейного флоу продаж (Sales Bot)

Включение: переменная окружения `SALES_NEW_FLOW=1` или `true`. По умолчанию `0` — работает старый флоу.

## Схема навигации

- **СТАРТ** (при отсутствии доступа и `SALES_NEW_FLOW=1`) → выбор формата.
- **Онлайн:** Формат → Онлайн-программы → 1-я/2-я ступень или О программе → Выбор тарифа → (Подробнее о тарифах) → Оплата.
- **Офлайн:** Формат → Главный герой → Подробнее / Форматы участия → (деталь по формату) → Оплата.

Все переходы «Назад» ведут на предыдущий экран без потери контекста. Оплата вызывается через те же обработчики, что и старый флоу (`pay:*`), маппинг `sale:pay:*` → `pay:*` выполняется внутри `handle_payment_initiate`.

---

## Экраны и callback_data

| Screen ID | Текст (кратко) | Кнопки → callback_data |
|-----------|----------------|-------------------------|
| **start** | Выберите формат обучения 👇 | 🔵 Онлайн → `sale:format:online`, 🟣 Офлайн (Москва) → `sale:format:offline` |
| **online_programs** | Онлайн-программы, «Вопросы, которые меняют всё» | ▶️ 1-я ступень → `sale:online:course:q:step:1`, ▶️ 2-я ступень → `sale:online:course:q:step:2`, 📄 О программе → `sale:online:course:q:about`, 🔙 Назад → `sale:back:start` |
| **online_step_1** | Онлайн, 1-я ступень, 30 дней практики | 💳 Выбрать тариф → `sale:online:q:step:1:plans`, 🔙 Назад → `sale:back:online_programs` |
| **online_step_2** | Онлайн, 2-я ступень | 💳 Выбрать тариф → `sale:online:q:step:2:plans`, 🔙 Назад → `sale:back:online_programs` |
| **online_about** | О программе (короткое описание) | 🔙 Назад → `sale:back:online_programs` |
| **online_plans_step_1** | Форматы участия: BASIC / FEEDBACK / PRACTIC + цены | 💎 BASIC → `sale:online:q:step:1:plan:basic`, ⭐ FEEDBACK → `sale:online:q:step:1:plan:feedback`, 👑 PRACTIC → `sale:online:q:step:1:plan:practic`, 🔍 Подробнее о тарифах → `sale:online:q:step:1:plans:details`, 🔙 Назад → `sale:back:online_step_1` |
| **online_plans_step_2** | То же для 2-й ступени | Аналогично, step:2, back → `sale:back:online_step_2` |
| **online_plan_details** | Детали одного тарифа (BASIC/FEEDBACK/PRACTIC) | 💳 Оплатить X ₽ → `sale:pay:online:q:step:{1|2}:{basic|feedback|practic}`, 🔙 Назад к тарифам → `sale:back:online_plans_step_{1|2}` |
| **online_plans_details** | Подробнее о тарифах (один экран с тремя описаниями) | 🔙 Назад к тарифам → `sale:back:online_plans_step_{1|2}` |
| **offline_main** | «Главный герой», Москва, 2 дня | 📄 Подробнее → `sale:offline:hero:about`, 💳 Выбрать формат участия → `sale:offline:hero:plans`, 🔙 Назад → `sale:back:start` |
| **offline_about** | Полное описание практикума (текущий текст из проекта) | 💳 Выбрать формат участия → `sale:offline:hero:plans`, 🔙 Назад → `sale:back:offline_main` |
| **offline_plans** | Форматы: СЛУШАТЕЛЬ / АКТИВИСТ / МЕДИА-ПЕРСОНА / ГЛАВНЫЙ ГЕРОЙ + цены | По одной кнопке на формат → `sale:offline:hero:plan:listener|aktivist|media|hero`, 🔙 Назад → `sale:back:offline_main` |
| **offline_plan_details** | Деталь формата офлайн + цена | 💳 Оплатить X ₽ → `sale:pay:offline:hero:{listener|aktivist|media|hero}`, 🔙 Назад к тарифам → `sale:offline:hero:plans` |

---

## Маппинг sale:pay:* → pay:* (оплата)

Обработчик оплаты принимает и `pay:*`, и `sale:pay:*`. Для `sale:pay:*` внутри вызывается маппинг:

| sale:pay:* | pay:* |
|------------|--------|
| `sale:pay:online:q:step:1:basic` | `pay:online:basic` |
| `sale:pay:online:q:step:1:feedback` | `pay:online:feedback` |
| `sale:pay:online:q:step:1:practic` | `pay:online:practic` |
| `sale:pay:online:q:step:2:basic` | `pay:online:second:basic` |
| `sale:pay:online:q:step:2:feedback` | `pay:online:second:feedback` |
| `sale:pay:online:q:step:2:practic` | `pay:online:second:practic` |
| `sale:pay:offline:hero:listener` | `pay:offline:slushatel` |
| `sale:pay:offline:hero:aktivist` | `pay:offline:aktivist` |
| `sale:pay:offline:hero:media` | `pay:offline:media_persona` |
| `sale:pay:offline:hero:hero` | `pay:offline:glavnyi_geroi` |

Цены и product_id не меняются — используются существующие настройки и платёжный слой.

---

## Регистрация обработчиков

- Все `sale:*`, кроме `sale:pay:*`, обрабатываются в `handle_sale_screen` (один обработчик, диспетчеризация по `callback.data`).
- `sale:pay:*` обрабатываются в `handle_payment_initiate` после маппинга на `pay:*`.
- Регистрация в `_register_handlers`:  
  - `F.data.startswith("sale:") & ~F.data.startswith("sale:pay:")` → `handle_sale_screen`;  
  - `F.data.startswith("pay:") | F.data.startswith("sale:pay:")` → `handle_payment_initiate`.

---

## Команда для админа

- `/sales_new_flow_status` — выводит текущее значение флага (включён/выключен) и подсказку, как включить.  
  Если в конфиге задан `ADMIN_CHAT_ID`, только пользователь с этим chat_id может вызвать команду.

---

## Ограничение Telegram

Длина `callback_data` не более 64 байт. Все используемые значения укладываются в лимит.
