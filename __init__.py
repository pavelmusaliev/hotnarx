"""
Агрегатор парсеров — 5 магазинов.
Добавить новый: создайте parsers/newshop.py с fetch(min_discount, limit),
импортируйте и добавьте в ALL_PARSERS.
"""
from parsers import uzum, korzinka, texnomart, olx, wildberries, mediapark

ALL_PARSERS = [
    ("Uzum",        uzum.fetch),
    ("Korzinka",    korzinka.fetch),
    ("Texnomart",   texnomart.fetch),
    ("OLX",         olx.fetch),
    ("Wildberries", wildberries.fetch),
    ("MediaPark",   mediapark.fetch),
]

def fetch_all(min_discount: int = 30, limit_per_shop: int = 10) -> list[dict]:
    """Запускает все парсеры, возвращает акции отсортированные по % скидки."""
    import logging
    log = logging.getLogger(__name__)
    all_deals: list[dict] = []
    for name, fn in ALL_PARSERS:
        try:
            deals = fn(min_discount=min_discount, limit=limit_per_shop)
            all_deals.extend(deals)
            log.info(f"[{name}] +{len(deals)} акций")
        except Exception as e:
            log.error(f"[{name}] Парсер упал: {e}")
    all_deals.sort(key=lambda d: d["discount"], reverse=True)
    return all_deals
