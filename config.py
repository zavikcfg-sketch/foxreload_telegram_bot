"""
Все настройки читаются из переменных окружения (.env файл рядом с проектом,
либо переменные окружения, заданные в панели хостинга).

Если бот падает с "Не задана переменная окружения ..." — значит .env не
подхватился хостингом. На большинстве платформ (Render, Railway и т.п.)
переменные из .env нужно ещё и продублировать в разделе Environment /
Variables в панели управления проектом — сам файл .env туда не читается
автоматически, если вы не указали это явно в настройках деплоя.
"""

import os

from dotenv import load_dotenv

load_dotenv()


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"Не задана переменная окружения {name}. "
            f"Проверьте файл .env и/или переменные окружения в панели хостинга (Render/Railway/VPS)."
        )
    return value


class Settings:
    # --- Telegram ---
    BOT_TOKEN: str = _require("BOT_TOKEN")
    BOT_USERNAME: str = os.environ.get("BOT_USERNAME", "")  # без @, для ссылки в сообщениях
    ADMIN_CHAT_ID: int | None = int(os.environ["ADMIN_CHAT_ID"]) if os.environ.get("ADMIN_CHAT_ID") else None

    # --- ЮMoney (yoomoney.ru) ---
    # access_token выпускается в разделе "Мои приложения" на yoomoney.ru/myservices/online.
    # wallet — номер вашего кошелька ЮMoney (получатель платежей).
    YOOMONEY_ACCESS_TOKEN: str = _require("YOOMONEY_ACCESS_TOKEN")
    YOOMONEY_WALLET: str = _require("YOOMONEY_WALLET")
    YOOMONEY_POLL_INTERVAL_SEC: int = int(os.environ.get("YOOMONEY_POLL_INTERVAL_SEC", "20"))

    # --- FireLoot API ---
    # Реальные значения выдаются в личном кабинете после подключения через @firelootshop.
    FIRELOOT_BASE_URL: str = os.environ.get("FIRELOOT_BASE_URL", "https://api.firelootshop.com")
    FIRELOOT_API_KEY: str = os.environ.get("FIRELOOT_API_KEY", "")
    FIRELOOT_WEBHOOK_SECRET: str = os.environ.get("FIRELOOT_WEBHOOK_SECRET", "")

    # --- Наценка и контроль баланса ---
    MARKUP_PERCENT: float = float(os.environ.get("MARKUP_PERCENT", "25"))
    LOW_BALANCE_THRESHOLD_USDT: float = float(os.environ.get("LOW_BALANCE_THRESHOLD_USDT", "10"))
    BALANCE_CHECK_INTERVAL_SEC: int = int(os.environ.get("BALANCE_CHECK_INTERVAL_SEC", "3600"))


settings = Settings()
