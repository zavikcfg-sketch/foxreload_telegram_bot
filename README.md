# FOX RELOAD Telegram Shop

Telegram-магазин на Python + aiogram 3, подключённый к FOX RELOAD Public API.

## Что умеет

- Каталог через `/api/categories/`
- Поиск товаров через `/api/products/search`
- Карточка товара через `/api/products/<id>/`
- Наценка магазина
- Оплата через Telegram Stars
- После оплаты создание заказа `/api/orders/`
- Оплата поставщику `/api/orders/<order_id>/pay`
- Опрос заказа до `completed`
- Выдача `items[].externalData`
- История покупок
- Баланс FOX RELOAD для администратора
- SQLite
- Inline-меню с эмодзи и аккуратной сеткой кнопок

## Важно про «цветные кнопки»

Telegram Bot API не позволяет разработчику задавать цвет inline-кнопки. Поэтому визуальный стиль сделан через эмодзи, названия, группировку и меню. Реальные цвета кнопок зависят от клиента Telegram.

## Запуск

1. Создайте бота через BotFather.
2. Скопируйте `.env.example` в `.env`.
3. Заполните `BOT_TOKEN`, `FOX_API_KEY`, `ADMIN_IDS`.
4. Установите Python 3.11–3.13.
5. Выполните:

```bash
pip install -r requirements.txt
python bot.py
```

## Оплата

В примере используется Telegram Stars (`XTR`), поэтому отдельный payment-provider token не нужен.

`STAR_RATE` и `MARKUP_PERCENT` — настройки витрины. Это не курс FOX RELOAD и не комиссия Telegram.

## Безопасность

- Не отправляйте FOX API Key в чат.
- Не вставляйте ключ в исходный код.
- Храните его только в переменных окружения.
- Если ключ уже был опубликован, сразу отзовите его и создайте новый.
