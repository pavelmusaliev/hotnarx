"""
Агрегатор парсеров.
Добавьте новый магазин:
  1. Создайте parsers/newshop.py с функцией fetch()
  2. Импортируйте и добавьте в ALL_PARSERS ниже
"""
from parsers import uzum, korzinka, texnomart

# Список активных парсеров. Закомментируйте чтобы отключить.
ALL_PARSERS = [
    uzum.fetch,
    korzinka.fetch,
    texnomart.fetch,
]


def fetch_all(min_discount: int = 30, limit_per_shop: int = 10) -> list[dict]:
    """Запускает все парсеры и возвращает объединённый список акций."""
    all_deals = []
    for parser in ALL_PARSERS:
        try:
            deals = parser(min_discount=min_discount, limit=limit_per_shop)
            all_deals.extend(deals)
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"Парсер {parser.__module__} упал: {e}")
    return all_deals
