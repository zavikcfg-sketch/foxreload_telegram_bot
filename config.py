"""
Все настройки читаются из переменных окружения (.env файл рядом с проектом).
Никаких ключей и токенов в коде быть не должно.
"""

import os

from dotenv import load_dotenv

load_dotenv()


class Settings:
    # --- Telegram ---
    BOT_TOKEN: str = os.environ["BOT_TOKEN"]
    BOT_USERNAME: str = os.environ.get("BOT_USERNAME", "")  # без @, для return_url после оплаты
    ADMIN_CHAT_ID: int | None = int(os.environ["ADMIN_CHAT_ID"]) if os.environ.get("ADMIN_CHAT_ID") else None

    # --- ЮKassa ---
    YOOKASSA_SHOP_ID: str = os.environ["YOOKASSA_SHOP_ID"]
    YOOKASSA_SECRET_KEY: str = os.environ["YOOKASSA_SECRET_KEY"]

    # --- FireLoot API ---
    # Реальные значения выдаются в личном кабинете после подключения через @firelootshop.
    FIRELOOT_BASE_URL: str = os.environ.get("FIRELOOT_BASE_URL", "https://api.firelootshop.com")
    FIRELOOT_API_KEY: str = os.environ.get("FIRELOOT_API_KEY", "")
    FIRELOOT_WEBHOOK_SECRET: str = os.environ.get("FIRELOOT_WEBHOOK_SECRET", "")

    # --- Наценка и контроль баланса ---
    MARKUP_PERCENT: float = float(os.environ.get("MARKUP_PERCENT", "25"))  # ваша наценка сверх опта, в %
    LOW_BALANCE_THRESHOLD_USDT: float = float(os.environ.get("LOW_BALANCE_THRESHOLD_USDT", "10"))
    BALANCE_CHECK_INTERVAL_SEC: int = int(os.environ.get("BALANCE_CHECK_INTERVAL_SEC", "3600"))


settings = Settings()
