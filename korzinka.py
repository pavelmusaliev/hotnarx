import logging
import re
import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

BASE = "https://korzinka.uz"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ru-RU,ru;q=0.9",
    "Referer": "https://korzinka.uz/",
}


def _parse_price(text: str) -> int:
    """Извлекает число из строки вида '45 990 сум' → 45990."""
    digits = re.sub(r"[^\d]", "", text or "")
    return int(digits) if digits else 0


def fetch(min_discount: int = 20, limit: int = 15) -> list[dict]:
    """
    Акции с korzinka.uz — парсит страницу /ru/promotions.
    Korzinka показывает недельные акции на продукты.
    """
    urls_to_try = [
        f"{BASE}/ru/promotions",
        f"{BASE}/ru/actions",
        f"{BASE}/ru/sale",
    ]

    soup = None
    page_url = ""
    for url in urls_to_try:
        try:
            resp = requests.get(url, headers=HEADERS, timeout=20)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "lxml")
                page_url = url
                break
        except Exception as e:
            log.warning(f"[Korzinka] {url} недоступен: {e}")

    if not soup:
        log.error("[Korzinka] Ни одна страница акций не открылась.")
        return []

    deals = []

    # --- Попытка 1: карточки товаров с классами product-card / catalog-item
    cards = (
        soup.select(".product-card")
        or soup.select(".catalog-item")
        or soup.select("[class*='product']")
        or soup.select("article")
    )

    for card in cards:
        try:
            # Название
            title_el = (
                card.select_one(".product-card__title")
                or card.select_one(".product-name")
                or card.select_one("h2, h3, h4")
            )
            title = title_el.get_text(strip=True) if title_el else ""
            if not title:
                continue

            # Цены
            old_el = (
                card.select_one(".old-price")
                or card.select_one(".price-old")
                or card.select_one("[class*='old']")
                or card.select_one("s")
                or card.select_one("del")
            )
            new_el = (
                card.select_one(".new-price")
                or card.select_one(".price-new")
                or card.select_one(".price-current")
                or card.select_one("[class*='current']")
                or card.select_one(".price")
            )

            old_price = _parse_price(old_el.get_text() if old_el else "")
            new_price = _parse_price(new_el.get_text() if new_el else "")

            if not old_price or not new_price or old_price <= new_price:
                continue

            discount = round((old_price - new_price) / old_price * 100)
            if discount < min_discount:
                continue

            # Ссылка
            link_el = card.select_one("a[href]")
            href = link_el["href"] if link_el else ""
            url_full = href if href.startswith("http") else f"{BASE}{href}"

            # Фото
            img_el = card.select_one("img")
            img_src = ""
            if img_el:
                img_src = (
                    img_el.get("data-src")
                    or img_el.get("data-lazy-src")
                    or img_el.get("src")
                    or ""
                )
                if img_src and not img_src.startswith("http"):
                    img_src = f"{BASE}{img_src}"

            deal_id = f"korzinka_{re.sub(r'[^a-z0-9]', '', title.lower())[:30]}"

            deals.append({
                "id":        deal_id,
                "title":     title,
                "old_price": old_price,
                "new_price": new_price,
                "discount":  discount,
                "url":       url_full or page_url,
                "image":     img_src,
                "shop":      "Korzinka.uz 🛒",
            })

            if len(deals) >= limit:
                break

        except Exception as e:
            log.warning(f"[Korzinka] Пропуск карточки: {e}")

    # --- Попытка 2: если карточек не нашли — ищем любые пары цен на странице
    if not deals:
        log.info("[Korzinka] Карточки не найдены, ищу акционные блоки...")
        promo_blocks = soup.select("[class*='promo'], [class*='action'], [class*='discount']")
        for block in promo_blocks[:limit]:
            texts = block.get_text(" ", strip=True)
            prices = re.findall(r"\d[\d\s]{2,8}\d", texts)
            if len(prices) >= 2:
                old_p = _parse_price(prices[0])
                new_p = _parse_price(prices[1])
                if old_p > new_p > 0:
                    discount = round((old_p - new_p) / old_p * 100)
                    if discount >= min_discount:
                        title_el = block.select_one("h2,h3,h4,p,span")
                        title = title_el.get_text(strip=True)[:80] if title_el else "Акция Korzinka"
                        deals.append({
                            "id":        f"korzinka_block_{len(deals)}",
                            "title":     title,
                            "old_price": old_p,
                            "new_price": new_p,
                            "discount":  discount,
                            "url":       page_url,
                            "image":     "",
                            "shop":      "Korzinka.uz 🛒",
                        })

    log.info(f"[Korzinka] Найдено {len(deals)} акций")
    return deals
