import asyncio
import html
import logging
import os
import sqlite3
from decimal import Decimal, ROUND_UP

import aiohttp
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
    Message, LabeledPrice, PreCheckoutQuery
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
FOX_API_KEY = os.getenv("FOX_API_KEY", "")
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()}
FOX_BASE_URL = os.getenv("FOX_BASE_URL", "https://public-api.foxreload.com")
FOX_CURRENCY = os.getenv("FOX_CURRENCY", "usd")
LANGUAGE = os.getenv("FOX_LANGUAGE", "ru")
DB_PATH = os.getenv("DB_PATH", "shop.db")

# Для Telegram Stars:
# 1 USD ~= STAR_RATE Stars. Это только ваша витринная конверсия.
STAR_RATE = Decimal(os.getenv("STAR_RATE", "100"))
MARKUP_PERCENT = Decimal(os.getenv("MARKUP_PERCENT", "10"))

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("foxreload-bot")

if not BOT_TOKEN or not FOX_API_KEY:
    raise RuntimeError("Заполните BOT_TOKEN и FOX_API_KEY в .env")

bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db() as c:
        c.execute("""
        CREATE TABLE IF NOT EXISTS users(
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )""")
        c.execute("""
        CREATE TABLE IF NOT EXISTS orders(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            fox_order_id TEXT,
            product_id TEXT NOT NULL,
            product_name TEXT,
            quantity INTEGER NOT NULL,
            supplier_price REAL NOT NULL,
            sale_price REAL NOT NULL,
            status TEXT DEFAULT 'pending',
            external_data TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )""")


def save_user(u):
    with db() as c:
        c.execute("""
        INSERT INTO users(user_id, username, first_name)
        VALUES(?,?,?)
        ON CONFLICT(user_id) DO UPDATE SET username=excluded.username,
        first_name=excluded.first_name
        """, (u.id, u.username, u.first_name))


def money(v):
    return Decimal(str(v)).quantize(Decimal("0.01"))


def sale_price(price):
    p = Decimal(str(price))
    return money(p * (Decimal("1") + MARKUP_PERCENT / Decimal("100")))


def stars_for(usd):
    return max(1, int((Decimal(str(usd)) * STAR_RATE).to_integral_value(rounding=ROUND_UP)))


class FoxReload:
    def __init__(self):
        self.base = FOX_BASE_URL.rstrip("/")

    async def request(self, method, path, **kwargs):
        headers = {
            "X-API-Key": FOX_API_KEY,
            "X-Language": LANGUAGE,
            "X-Currency": FOX_CURRENCY,
            "Accept": "application/json",
        }
        if kwargs.get("json") is not None:
            headers["Content-Type"] = "application/json"

        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.request(method, self.base + path, headers=headers, **kwargs) as r:
                text = await r.text()
                if r.status >= 400:
                    raise RuntimeError(f"FoxReload HTTP {r.status}: {text[:500]}")
                try:
                    return await r.json()
                except Exception:
                    return {"raw": text}

    async def categories(self):
        return await self.request("GET", "/api/categories/?limit=20")

    async def search(self, query, limit=10):
        return await self.request(
            "GET", "/api/products/search",
            params={"query": query, "limit": limit}
        )

    async def product(self, product_id):
        return await self.request("GET", f"/api/products/{product_id}/")

    async def create_order(self, product_id, quantity=1):
        return await self.request(
            "POST", "/api/orders/",
            json={"items": [{"itemId": product_id, "quantity": quantity}]}
        )

    async def pay_order(self, order_id):
        return await self.request(
            "POST", f"/api/orders/{order_id}/pay",
            json={"paymentProvider": None}
        )

    async def get_order(self, order_id):
        return await self.request("GET", f"/api/orders/{order_id}")

    async def balance(self):
        return await self.request("GET", "/api/access/me/balances/")


fox = FoxReload()


def main_kb():
    b = InlineKeyboardBuilder()
    b.button(text="🛍 Каталог", callback_data="catalog")
    b.button(text="🔎 Поиск", callback_data="search")
    b.button(text="📦 Мои покупки", callback_data="purchases")
    b.button(text="💰 Баланс магазина", callback_data="balance")
    b.button(text="ℹ️ Помощь", callback_data="help")
    b.adjust(2, 2, 1)
    return b.as_markup()


def product_kb(product_id):
    b = InlineKeyboardBuilder()
    b.button(text="🛒 Купить", callback_data=f"buy:{product_id}")
    b.button(text="⬅️ Назад", callback_data="catalog")
    b.adjust(1)
    return b.as_markup()


def home_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="home")]
    ])


async def extract_list(data):
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    for key in ("results", "items", "products", "data", "categories"):
        value = data.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            for k in ("results", "items", "products"):
                if isinstance(value.get(k), list):
                    return value[k]
    return []


async def product_text(p):
    pid = p.get("id") or p.get("product_id") or p.get("productId") or p.get("slug")
    name = p.get("name") or p.get("title") or "Товар"
    price = p.get("price") or p.get("sellingPrice") or p.get("unitPrice") or 0
    stock = p.get("stock") or p.get("quantity") or p.get("available") or "—"
    desc = p.get("description") or ""
    final = sale_price(price)

    return (
        f"🎮 <b>{html.escape(str(name))}</b>\n\n"
        f"💵 Цена: <b>${final}</b>\n"
        f"📦 Остаток: <b>{html.escape(str(stock))}</b>\n\n"
        f"{html.escape(str(desc))[:700]}\n\n"
        f"🆔 <code>{html.escape(str(pid))}</code>"
    ), pid, name, Decimal(str(price))


@dp.message(CommandStart())
async def start(message: Message):
    save_user(message.from_user)
    await message.answer(
        "🔥 <b>FOX RELOAD SHOP</b>\n\n"
        "Автоматическая выдача цифровых товаров.\n"
        "Выберите раздел ниже 👇",
        reply_markup=main_kb()
    )


@dp.callback_query(F.data == "home")
async def home(call: CallbackQuery):
    await call.message.edit_text(
        "🔥 <b>FOX RELOAD SHOP</b>\n\nВыберите действие:",
        reply_markup=main_kb()
    )
    await call.answer()


@dp.callback_query(F.data == "catalog")
async def catalog(call: CallbackQuery):
    try:
        data = await fox.categories()
        cats = await extract_list(data)
        b = InlineKeyboardBuilder()
        for c in cats[:20]:
            cid = c.get("id") or c.get("slug")
            name = c.get("name") or c.get("title") or str(cid)
            b.button(text=f"🎮 {str(name)[:40]}", callback_data=f"catsearch:{cid}")
        b.button(text="🔎 Найти товар", callback_data="search")
        b.button(text="🏠 Главная", callback_data="home")
        b.adjust(1)
        await call.message.edit_text("🛍 <b>Каталог</b>\n\nВыберите категорию:", reply_markup=b.as_markup())
    except Exception as e:
        log.exception(e)
        await call.message.edit_text("❌ Не удалось загрузить каталог.", reply_markup=home_kb())
    await call.answer()


@dp.callback_query(F.data == "search")
async def search_start(call: CallbackQuery):
    await call.message.edit_text(
        "🔎 <b>Поиск товара</b>\n\n"
        "Отправьте следующим сообщением название товара.\n"
        "Например: <code>roblox</code> или <code>pubg</code>.",
        reply_markup=home_kb()
    )
    await call.answer()


@dp.message(F.text)
async def search_message(message: Message):
    text = message.text.strip()
    if text.startswith("/"):
        return
    if len(text) < 2:
        return

    try:
        data = await fox.search(text, 10)
        products = await extract_list(data)
        if not products:
            await message.answer("😔 Ничего не найдено.", reply_markup=main_kb())
            return

        b = InlineKeyboardBuilder()
        for p in products[:10]:
            pid = p.get("id") or p.get("product_id") or p.get("productId") or p.get("slug")
            name = p.get("name") or p.get("title") or str(pid)
            price = p.get("price") or p.get("sellingPrice") or p.get("unitPrice") or 0
            b.button(text=f"🎮 {str(name)[:32]} — ${sale_price(price)}", callback_data=f"product:{pid}")
        b.button(text="🏠 Главная", callback_data="home")
        b.adjust(1)
        await message.answer(
            f"🔎 <b>Результаты поиска:</b> {html.escape(text)}",
            reply_markup=b.as_markup()
        )
    except Exception:
        log.exception("search")
        await message.answer("❌ Ошибка связи с API.", reply_markup=main_kb())


@dp.callback_query(F.data.startswith("catsearch:"))
async def category_search(call: CallbackQuery):
    category = call.data.split(":", 1)[1]
    try:
        data = await fox.search(category, 10)
        products = await extract_list(data)
        b = InlineKeyboardBuilder()
        for p in products[:10]:
            pid = p.get("id") or p.get("product_id") or p.get("productId") or p.get("slug")
            name = p.get("name") or p.get("title") or str(pid)
            price = p.get("price") or p.get("sellingPrice") or p.get("unitPrice") or 0
            b.button(text=f"🎮 {str(name)[:32]} — ${sale_price(price)}", callback_data=f"product:{pid}")
        b.button(text="⬅️ Каталог", callback_data="catalog")
        b.adjust(1)
        await call.message.edit_text("🛍 <b>Товары категории</b>", reply_markup=b.as_markup())
    except Exception:
        await call.message.edit_text("❌ Не удалось получить товары.", reply_markup=home_kb())
    await call.answer()


@dp.callback_query(F.data.startswith("product:"))
async def product_view(call: CallbackQuery):
    pid = call.data.split(":", 1)[1]
    try:
        p = await fox.product(pid)
        text, pid, _, _ = await product_text(p)
        await call.message.edit_text(text, reply_markup=product_kb(pid))
    except Exception:
        log.exception("product")
        await call.message.edit_text("❌ Товар не найден.", reply_markup=home_kb())
    await call.answer()


@dp.callback_query(F.data.startswith("buy:"))
async def buy(call: CallbackQuery):
    pid = call.data.split(":", 1)[1]
    try:
        p = await fox.product(pid)
        text, pid, name, supplier = await product_text(p)
        final = sale_price(supplier)
        stars = stars_for(final)

        payload = f"fox:{call.from_user.id}:{pid}"
        prices = [LabeledPrice(label=str(name)[:50], amount=stars)]

        await bot.send_invoice(
            chat_id=call.from_user.id,
            title=str(name)[:32],
            description="Цифровой товар FOX RELOAD",
            payload=payload,
            currency="XTR",
            prices=prices,
        )
        await call.answer("💳 Счёт отправлен ниже")
    except Exception as e:
        log.exception("buy")
        await call.answer("❌ Не удалось создать оплату", show_alert=True)


@dp.pre_checkout_query()
async def pre_checkout(q: PreCheckoutQuery):
    await q.answer(ok=True)


@dp.message(F.successful_payment)
async def successful_payment(message: Message):
    sp = message.successful_payment
    payload = sp.invoice_payload
    if not payload.startswith("fox:"):
        return

    _, user_id, pid = payload.split(":", 2)
    if int(user_id) != message.from_user.id:
        await message.answer("❌ Ошибка владельца заказа.")
        return

    try:
        # Создаём заказ у поставщика только после получения оплаты от покупателя.
        created = await fox.create_order(pid, 1)
        order_id = created.get("id") or created.get("orderId") or created.get("order_id")
        if not order_id:
            raise RuntimeError(f"API не вернул order_id: {created}")

        await fox.pay_order(order_id)

        result = None
        for _ in range(15):
            await asyncio.sleep(2)
            result = await fox.get_order(order_id)
            status = str(result.get("status", "")).lower()
            if status == "completed":
                break

        # externalData может быть в items[].externalData.
        codes = []
        items = result.get("items", []) if isinstance(result, dict) else []
        for item in items:
            ed = item.get("externalData")
            if ed is not None:
                codes.append(str(ed))

        if not codes:
            await message.answer(
                "✅ <b>Оплата получена!</b>\n\n"
                f"Заказ поставщика: <code>{html.escape(str(order_id))}</code>\n"
                "⏳ Товар ещё обрабатывается. Попробуйте открыть «Мои покупки» через несколько секунд.",
                reply_markup=main_kb()
            )
        else:
            text = "\n\n".join(f"🎁 <code>{html.escape(x)}</code>" for x in codes)
            await message.answer(
                "🎉 <b>Покупка успешно выдана!</b>\n\n"
                f"{text}\n\n"
                f"🧾 Заказ: <code>{html.escape(str(order_id))}</code>",
                reply_markup=main_kb()
            )

        price = Decimal(str(sp.total_amount)) / STAR_RATE
        with db() as c:
            c.execute("""
            INSERT INTO orders(user_id, fox_order_id, product_id, product_name,
                               quantity, supplier_price, sale_price, status, external_data)
            VALUES(?,?,?,?,?,?,?,?,?)
            """, (message.from_user.id, str(order_id), pid, "Digital product", 1,
                  float(price), float(price), "completed", "\n".join(codes)))
    except Exception as e:
        log.exception("fulfillment")
        await message.answer(
            "⚠️ <b>Оплата получена, но автоматическая выдача не завершилась.</b>\n\n"
            "Заказ будет проверен администратором.\n"
            f"Технический ID: <code>{html.escape(str(payload))}</code>",
            reply_markup=main_kb()
        )


@dp.callback_query(F.data == "purchases")
async def purchases(call: CallbackQuery):
    with db() as c:
        rows = c.execute(
            "SELECT * FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 15",
            (call.from_user.id,)
        ).fetchall()

    if not rows:
        text = "📦 <b>Мои покупки</b>\n\nПокупок пока нет."
    else:
        lines = ["📦 <b>Мои покупки</b>\n"]
        for r in rows:
            lines.append(
                f"🧾 #{r['id']} • {html.escape(str(r['product_name']))}\n"
                f"Статус: <b>{html.escape(str(r['status']))}</b>"
            )
        text = "\n\n".join(lines)

    await call.message.edit_text(text, reply_markup=home_kb())
    await call.answer()


@dp.callback_query(F.data == "balance")
async def balance(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("🔒 Раздел администратора", show_alert=True)
        return
    try:
        data = await fox.balance()
        await call.message.edit_text(
            "💰 <b>Баланс FOX RELOAD</b>\n\n"
            f"<pre>{html.escape(str(data))}</pre>",
            reply_markup=home_kb()
        )
    except Exception:
        await call.message.edit_text("❌ Не удалось получить баланс API.", reply_markup=home_kb())
    await call.answer()


@dp.callback_query(F.data == "help")
async def help_cb(call: CallbackQuery):
    await call.message.edit_text(
        "ℹ️ <b>Помощь</b>\n\n"
        "🔎 Найдите товар → откройте карточку → нажмите «Купить».\n"
        "💳 После оплаты бот автоматически создаёт заказ FOX RELOAD.\n"
        "⚡ После статуса completed бот выдаёт externalData.\n\n"
        "Если выдача задержалась — сохраните ID заказа и обратитесь к администратору.",
        reply_markup=home_kb()
    )
    await call.answer()


async def main():
    init_db()
    await bot.delete_webhook(drop_pending_updates=True)
    log.info("FOX RELOAD bot started")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
