"""
ВАЖНО: числа ниже — placeholder-ы. Реальные оптовые цены на звёзды и Premium
вы увидите в личном кабинете FireLoot после подключения (или получите одним
запросом к их API — см. документацию в кабинете). Замените WHOLESALE_RUB_PER_STAR
и WHOLESALE_PREMIUM_RUB на актуальные цифры, иначе бот будет считать неправильно.
"""

from config import settings

# Опт за 1 звезду, в рублях (уточнить в кабинете FireLoot)
WHOLESALE_RUB_PER_STAR = 1.6

# Опт за Premium по срокам, в рублях (уточнить в кабинете FireLoot)
WHOLESALE_PREMIUM_RUB = {
    3: 1200,
    6: 2100,
    12: 3600,
}

# Пакеты звёзд, которые видит клиент в меню: (количество, подпись)
STAR_PACKAGES = [
    (50, "50 ⭐"),
    (100, "100 ⭐"),
    (250, "250 ⭐"),
    (500, "500 ⭐"),
    (1000, "1000 ⭐"),
]

# Пакеты Premium: (месяцев, подпись)
PREMIUM_PACKAGES = [
    (3, "Premium 3 месяца"),
    (6, "Premium 6 месяцев"),
    (12, "Premium 12 месяцев"),
]


def wholesale_price(kind: str, amount: int) -> float:
    if kind == "stars":
        return amount * WHOLESALE_RUB_PER_STAR
    if kind == "premium":
        return WHOLESALE_PREMIUM_RUB[amount]
    raise ValueError(f"unknown kind: {kind}")


def retail_price(kind: str, amount: int) -> float:
    base = wholesale_price(kind, amount)
    return round(base * (1 + settings.MARKUP_PERCENT / 100), 0)
