import logging
import requests

log = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9",
    "Origin": "https://uzum.uz",
    "Referer": "https://uzum.uz/",
}


def fetch(min_discount: int = 30, limit: int = 15) -> list[dict]:
    """Акции с Uzum.uz через API поиска."""
    try:
        resp = requests.get(
            "https://api.uzum.uz/api/product/search",
            headers=HEADERS,
            params={"sortBy": "DISCOUNT_DESC", "size": 60, "page": 0},
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        log.error(f"[Uzum] Ошибка запроса: {e}")
        return []

    products = (
        data.get("payload", {}).get("products", [])
        or data.get("products", [])
        or []
    )

    deals = []
    for p in products:
        try:
            old = p.get("fullPrice") or p.get("originalPrice") or 0
            new = p.get("purchasePrice") or p.get("price") or 0
            if not old or not new or old <= new:
                continue

            discount = round((old - new) / old * 100)
            if discount < min_discount:
                continue

            pid   = str(p.get("productId") or p.get("id") or "")
            slug  = p.get("slug") or pid
            photos = p.get("photos") or p.get("images") or []
            img   = ""
            if photos:
                img = (
                    photos[0].get("high")
                    or photos[0].get("url")
                    or photos[0].get("large")
                    or ""
                )

            deals.append({
                "id":        f"uzum_{pid}",
                "title":     p.get("title") or p.get("name") or "Без названия",
                "old_price": old,
                "new_price": new,
                "discount":  discount,
                "url":       f"https://uzum.uz/product/{slug}",
                "image":     img,
                "shop":      "Uzum.uz 🛍",
            })
            if len(deals) >= limit:
                break
        except Exception as e:
            log.warning(f"[Uzum] Пропуск товара: {e}")

    log.info(f"[Uzum] Найдено {len(deals)} акций")
    return deals
