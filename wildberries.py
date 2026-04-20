import logging
import re
import requests

log = logging.getLogger(__name__)

# Wildberries имеет JSON API — парсим через него, не через HTML
API_URL = "https://catalog.wb.ru/catalog/sale/catalog"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "ru-RU,ru;q=0.9",
    "Origin": "https://www.wildberries.ru",
    "Referer": "https://www.wildberries.ru/",
}

# Узбекистан — dest параметр для Ташкента
PARAMS_BASE = {
    "appType": 1,
    "curr": "uzs",          # цены в узбекских сумах
    "dest": -1257786,       # Ташкент
    "sort": "sale",         # сортировка по скидке
    "spp": 30,
    "page": 1,
}

CATEGORIES = [
    ("Одежда",    {"cat": 306},   ),
    ("Электроника", {"cat": 6119}, ),
    ("Обувь",     {"cat": 4764},  ),
]


def _img_url(product_id: int) -> str:
    """Формирует URL превью фото по ID товара (стандартная схема WB)."""
    vol  = product_id // 100_000
    part = product_id // 1_000
    return (
        f"https://basket-{str(vol).zfill(2)}.wb.ru"
        f"/vol{vol}/part{part}/{product_id}/images/tm/1.jpg"
    )


def fetch(min_discount: int = 30, limit: int = 15) -> list[dict]:
    """
    Wildberries — раздел SALE через официальный JSON API каталога.
    Возвращает товары с наибольшей скидкой.
    """
    deals = []
    seen_ids: set[int] = set()

    for cat_name, extra_params in CATEGORIES:
        if len(deals) >= limit:
            break
        params = {**PARAMS_BASE, **extra_params}

        try:
            resp = requests.get(API_URL, headers=HEADERS, params=params, timeout=20)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            log.warning(f"[WB] Категория {cat_name}: {e}")
            continue

        products = (
            data.get("data", {}).get("products", [])
            or data.get("products", [])
            or []
        )

        for p in products:
            try:
                pid = int(p.get("id", 0))
                if not pid or pid in seen_ids:
                    continue
                seen_ids.add(pid)

                # Цены в WB API в копейках × 100 (т.е. /100 = сумы)
                sale_price = p.get("salePriceU", 0) or p.get("priceU", 0)
                orig_price = p.get("priceU", 0)
                sale_pct   = p.get("sale", 0)  # скидка % прямо в ответе

                if sale_pct < min_discount:
                    continue

                new_price = round(sale_price / 100)
                old_price = round(orig_price / 100)

                if not new_price or not old_price or old_price <= new_price:
                    continue

                name  = p.get("name", "Без названия")
                brand = p.get("brand", "")
                title = f"{brand} — {name}" if brand else name

                deals.append({
                    "id":        f"wb_{pid}",
                    "title":     title[:100],
                    "old_price": old_price,
                    "new_price": new_price,
                    "discount":  sale_pct,
                    "url":       f"https://www.wildberries.ru/catalog/{pid}/detail.aspx",
                    "image":     _img_url(pid),
                    "shop":      f"Wildberries 🍇 ({cat_name})",
                })

                if len(deals) >= limit:
                    break

            except Exception as e:
                log.warning(f"[WB] Пропуск товара: {e}")

    log.info(f"[WB] Найдено {len(deals)} акций")
    return deals
