"""
Telegram-бот "Звёзды и Premium" — автоматическая продажа Telegram Stars и Telegram
Premium с наценкой.

Схема работы (без стартового капитала на закупку товара впрок):
  1. Клиент выбирает товар (звёзды или Premium) и указывает получателя (username).
  2. Бот проверяет получателя через FireLoot API (показывает имя/фото — защита от опечаток).
  3. Бот выставляет счёт в ЮKassa на розничную цену (опт + ваша наценка).
  4. После успешной оплаты (вебхук ЮKassa) бот создаёт заказ в FireLoot API.
  5. FireLoot сам доставляет товар получателю и присылает вебхук о завершении.
  6. Бот уведомляет клиента, что доставка выполнена.

Вам не нужно ничего делать руками — единственное, что требуется человеческого участия:
следить, чтобы баланс в FireLoot (USDT) не уходил в ноль, и вовремя его пополнять
(можно настроить телеграм-напоминание себе, когда баланс опускается ниже порога —
см. check_balance_and_alert()).
"""

import asyncio
import hashlib
import hmac
import logging
import os
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime

import httpx
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from fastapi import FastAPI, Request, Response
from yoomoney import Client as YooMoneyClient, Quickpay

from config import settings
from pricing import STAR_PACKAGES, PREMIUM_PACKAGES, retail_price

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("stars-bot")

bot = Bot(token=settings.BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
router = Router()
dp.include_router(router)

yoomoney_client = YooMoneyClient(settings.YOOMONEY_ACCESS_TOKEN)

DB_PATH = os.path.join(os.path.dirname(__file__), "orders.db")


# --------------------------------------------------------------------------- #
# База данных заказов (простая SQLite — этого достаточно для одного бота)
# --------------------------------------------------------------------------- #
def db_init():
    with closing(sqlite3.connect(DB_PATH)) as con:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS orders (
                external_id     TEXT PRIMARY KEY,
                chat_id         INTEGER NOT NULL,
                kind            TEXT NOT NULL,      -- 'stars' | 'premium'
                amount          INTEGER NOT NULL,   -- число звёзд или месяцев premium
                target_username TEXT NOT NULL,
                retail_price    REAL NOT NULL,
                yk_payment_id   TEXT,
                status          TEXT NOT NULL DEFAULT 'awaiting_payment',
                fireloot_order  TEXT,
                created_at      TEXT NOT NULL
            )
            """
        )
        con.commit()


def db_create_order(external_id, chat_id, kind, amount, target_username, retail_price_):
    with closing(sqlite3.connect(DB_PATH)) as con:
        con.execute(
            "INSERT INTO orders (external_id, chat_id, kind, amount, target_username, "
            "retail_price, status, created_at) VALUES (?, ?, ?, ?, ?, ?, 'awaiting_payment', ?)",
            (external_id, chat_id, kind, amount, target_username, retail_price_, datetime.utcnow().isoformat()),
        )
        con.commit()


def db_get_order(external_id):
    with closing(sqlite3.connect(DB_PATH)) as con:
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT * FROM orders WHERE external_id = ?", (external_id,)).fetchone()
        return dict(row) if row else None


def db_update_order(external_id, **fields):
    with closing(sqlite3.connect(DB_PATH)) as con:
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        con.execute(f"UPDATE orders SET {set_clause} WHERE external_id = ?", (*fields.values(), external_id))
        con.commit()


def db_find_by_fireloot_order(order_id):
    with closing(sqlite3.connect(DB_PATH)) as con:
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT * FROM orders WHERE fireloot_order = ?", (order_id,)).fetchone()
        return dict(row) if row else None


# --------------------------------------------------------------------------- #
# Клиент FireLoot API — реальные адреса методов и ключ вы получите в личном
# кабинете после подключения через @firelootshop. До этого момента здесь
# подставлены заглушки из config.py (FIRELOOT_BASE_URL / FIRELOOT_API_KEY).
# --------------------------------------------------------------------------- #
class FireLootClient:
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    async def validate_username(self, username: str) -> dict:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(f"{self.base_url}/validate", json={"username": username}, headers=self.headers)
            r.raise_for_status()
            return r.json()

    async def create_order(self, external_id: str, username: str, *, stars: int = None, premium: int = None) -> dict:
        payload = {"external_id": external_id, "username": username}
        if stars is not None:
            payload["stars"] = stars
        if premium is not None:
            payload["premium"] = premium
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(f"{self.base_url}/orders", json=payload, headers=self.headers)
            r.raise_for_status()
            return r.json()

    async def get_balance(self) -> dict:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(f"{self.base_url}/balance", headers=self.headers)
            r.raise_for_status()
            return r.json()


fireloot = FireLootClient(settings.FIRELOOT_BASE_URL, settings.FIRELOOT_API_KEY)


def verify_fireloot_signature(raw_body: bytes, signature_header: str) -> bool:
    """Проверка подписи вебхука FireLoot (HMAC-SHA256)."""
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(settings.FIRELOOT_WEBHOOK_SECRET.encode(), raw_body, hashlib.sha256).hexdigest()
    got = signature_header.split("sha256=", 1)[1]
    return hmac.compare_digest(expected, got)


# --------------------------------------------------------------------------- #
# FSM (диалог с пользователем)
# --------------------------------------------------------------------------- #
class BuyFlow(StatesGroup):
    choosing_kind = State()
    choosing_amount = State()
    entering_username = State()
    confirming = State()


def main_menu_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="⭐ Купить звёзды", callback_data="buy:stars")
    kb.button(text="💎 Купить Telegram Premium", callback_data="buy:premium")
    kb.adjust(1)
    return kb.as_markup()


def packages_kb(kind: str):
    kb = InlineKeyboardBuilder()
    packages = STAR_PACKAGES if kind == "stars" else PREMIUM_PACKAGES
    for amount, label in packages:
        price = retail_price(kind, amount)
        kb.button(text=f"{label} — {price:.0f} ₽", callback_data=f"amt:{kind}:{amount}")
    kb.button(text="⬅️ Назад", callback_data="back:menu")
    kb.adjust(1)
    return kb.as_markup()


def confirm_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Всё верно, оплатить", callback_data="confirm:yes")
    kb.button(text="✏️ Ввести другой username", callback_data="confirm:retry")
    kb.adjust(1)
    return kb.as_markup()


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Привет! Это автоматический магазин Telegram Stars и Telegram Premium.\n"
        "Оплата — картой или СБП. Доставка обычно занимает пару минут после оплаты.\n\n"
        "Что хотите купить?",
        reply_markup=main_menu_kb(),
    )


@router.callback_query(F.data == "back:menu")
async def back_to_menu(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("Что хотите купить?", reply_markup=main_menu_kb())
    await callback.answer()


@router.callback_query(F.data.startswith("buy:"))
async def choose_kind(callback: CallbackQuery, state: FSMContext):
    kind = callback.data.split(":", 1)[1]
    await state.update_data(kind=kind)
    await state.set_state(BuyFlow.choosing_amount)
    title = "Сколько звёзд?" if kind == "stars" else "На какой срок Premium?"
    await callback.message.edit_text(title, reply_markup=packages_kb(kind))
    await callback.answer()


@router.callback_query(F.data.startswith("amt:"))
async def choose_amount(callback: CallbackQuery, state: FSMContext):
    _, kind, amount = callback.data.split(":")
    await state.update_data(kind=kind, amount=int(amount))
    await state.set_state(BuyFlow.entering_username)
    await callback.message.edit_text(
        "Введите username получателя без @ (кому отправить звёзды/Premium).\n"
        "Если это вы сами — просто пришлите свой username."
    )
    await callback.answer()


@router.message(BuyFlow.entering_username)
async def enter_username(message: Message, state: FSMContext):
    username = message.text.strip().lstrip("@")
    data = await state.get_data()
    kind, amount = data["kind"], data["amount"]

    status_msg = await message.answer("Проверяю получателя…")
    try:
        info = await fireloot.validate_username(username)
    except Exception:
        log.exception("validate_username failed")
        await status_msg.edit_text(
            "Не удалось проверить username — проверьте, что он указан верно, и попробуйте ещё раз."
        )
        return

    if not info.get("valid"):
        await status_msg.edit_text("Такой пользователь не найден. Проверьте username и пришлите ещё раз.")
        return

    price = retail_price(kind, amount)
    name = info.get("name", username)
    label = f"{amount} ⭐" if kind == "stars" else f"Premium на {amount} мес."

    await state.update_data(username=username, price=price, name=name)
    await state.set_state(BuyFlow.confirming)
    await status_msg.edit_text(
        f"Получатель: {name} (@{username})\n"
        f"Товар: {label}\n"
        f"К оплате: {price:.0f} ₽\n\n"
        f"Всё верно?",
        reply_markup=confirm_kb(),
    )


@router.callback_query(BuyFlow.confirming, F.data == "confirm:retry")
async def confirm_retry(callback: CallbackQuery, state: FSMContext):
    await state.set_state(BuyFlow.entering_username)
    await callback.message.edit_text("Введите username получателя без @.")
    await callback.answer()


@router.callback_query(BuyFlow.confirming, F.data == "confirm:yes")
async def confirm_and_pay(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    kind, amount, username, price = data["kind"], data["amount"], data["username"], data["price"]
    external_id = f"ord-{uuid.uuid4().hex[:12]}"

    label = f"{amount} Telegram Stars" if kind == "stars" else f"Telegram Premium на {amount} мес."

    # label = external_id: по нему бот потом узнаёт этот платёж в истории операций ЮMoney
    quickpay = Quickpay(
        receiver=settings.YOOMONEY_WALLET,
        quickpay_form="shop",
        targets=f"{label} для @{username}",
        paymentType="AC",  # оплата картой; можно поставить "PC" для оплаты с кошелька ЮMoney
        sum=price,
        label=external_id,
    )

    db_create_order(external_id, callback.message.chat.id, kind, amount, username, price)

    kb = InlineKeyboardBuilder()
    kb.button(text="💳 Оплатить", url=quickpay.redirected_url)
    kb.adjust(1)

    await callback.message.edit_text(
        f"Счёт создан на {price:.0f} ₽.\n"
        f"После оплаты доставка начнётся автоматически в течение {settings.YOOMONEY_POLL_INTERVAL_SEC}-60 секунд "
        f"— ничего дополнительно нажимать не нужно.",
        reply_markup=kb.as_markup(),
    )
    await state.clear()
    await callback.answer()


async def _fulfill_paid_order(order: dict):
    """Общая логика: заказ оплачен → создать заказ в FireLoot → уведомить клиента."""
    external_id = order["external_id"]
    db_update_order(external_id, status="paid")
    try:
        if order["kind"] == "stars":
            result = await fireloot.create_order(external_id, order["target_username"], stars=order["amount"])
        else:
            result = await fireloot.create_order(external_id, order["target_username"], premium=order["amount"])
        db_update_order(external_id, status="processing", fireloot_order=result["order_id"])
        await bot.send_message(order["chat_id"], "Оплата получена, заказ передан на доставку ✅")
    except Exception:
        log.exception("Не удалось создать заказ в FireLoot")
        db_update_order(external_id, status="delivery_failed")
        await bot.send_message(
            order["chat_id"],
            "Оплата прошла, но при передаче заказа поставщику произошла ошибка. "
            "Мы разберёмся и доставим товар вручную либо вернём деньги.",
        )
        if settings.ADMIN_CHAT_ID:
            await bot.send_message(settings.ADMIN_CHAT_ID, f"⚠️ Сбой доставки по заказу {external_id}")


async def poll_yoomoney_payments():
    """
    ЮMoney для физлиц без статуса ИП/юрлица не даёт HTTP-уведомления на свой сервер
    так же просто, как ЮKassa, поэтому бот сам периодически спрашивает историю
    операций кошелька и ищет платежи с label = external_id наших заказов.
    """
    while True:
        try:
            with closing(sqlite3.connect(DB_PATH)) as con:
                con.row_factory = sqlite3.Row
                pending = con.execute(
                    "SELECT * FROM orders WHERE status = 'awaiting_payment'"
                ).fetchall()

            for row in pending:
                order = dict(row)
                external_id = order["external_id"]
                try:
                    history = yoomoney_client.operation_history(label=external_id, records=5)
                except Exception:
                    log.exception("Не удалось получить историю операций ЮMoney")
                    continue

                for op in history.operations:
                    if op.label == external_id and op.status == "success" and op.direction == "in":
                        await _fulfill_paid_order(order)
                        break
        except Exception:
            log.exception("Сбой в цикле опроса ЮMoney")

        await asyncio.sleep(settings.YOOMONEY_POLL_INTERVAL_SEC)


# --------------------------------------------------------------------------- #
# Веб-сервер: вебхук FireLoot (заказ доставлен). Оплата теперь проверяется
# отдельным фоновым циклом poll_yoomoney_payments(), а не вебхуком.
# --------------------------------------------------------------------------- #
app = FastAPI()


@app.post("/webhooks/fireloot")
async def fireloot_webhook(request: Request):
    raw = await request.body()
    signature = request.headers.get("X-FireLoot-Signature", "")
    if not verify_fireloot_signature(raw, signature):
        return Response(status_code=403)

    event = await request.json()
    order_id = event.get("order_id")
    status = event.get("status")
    order = db_find_by_fireloot_order(order_id)
    if not order:
        return Response(status_code=200)

    db_update_order(order["external_id"], status=status)

    if status == "completed":
        await bot.send_message(order["chat_id"], "Готово! Товар доставлен 🎉 Спасибо за покупку.")
    elif status == "failed":
        await bot.send_message(
            order["chat_id"],
            "Доставка не удалась, деньги возвращены на баланс поставщика — мы вернём вам оплату вручную либо повторим заказ.",
        )
        if settings.ADMIN_CHAT_ID:
            await bot.send_message(settings.ADMIN_CHAT_ID, f"⚠️ FireLoot: заказ {order_id} завершился ошибкой")

    return Response(status_code=200)


# --------------------------------------------------------------------------- #
# Периодическая проверка баланса FireLoot — чтобы бот не "заглох" молча
# --------------------------------------------------------------------------- #
async def check_balance_and_alert():
    while True:
        try:
            balance = await fireloot.get_balance()
            if settings.ADMIN_CHAT_ID and balance.get("usdt", 0) < settings.LOW_BALANCE_THRESHOLD_USDT:
                await bot.send_message(
                    settings.ADMIN_CHAT_ID,
                    f"⚠️ Баланс FireLoot низкий: {balance.get('usdt')} USDT. Пополните, иначе заказы начнут падать.",
                )
        except Exception:
            log.exception("Не удалось проверить баланс FireLoot")
        await asyncio.sleep(settings.BALANCE_CHECK_INTERVAL_SEC)


async def run_bot():
    db_init()
    asyncio.create_task(check_balance_and_alert())
    asyncio.create_task(poll_yoomoney_payments())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(run_bot())
