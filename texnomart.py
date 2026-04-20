import logging
import re
import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

BASE = "https://texnomart.uz"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ru-RU,ru;q=0.9",
    "Referer": "https://texnomart.uz/",
}


def _parse_price(text: str) -> int:
    digits = re.sub(r"[^\d]", "", text or "")
    return int(digits) if digits else 0


def fetch(min_discount: int = 20, limit: int = 15) -> list[dict]:
    """
    Акции с texnomart.uz — электроника и бытовая техника.
    Парсит страницу распродаж / акций.
    """
    urls_to_try = [
        f"{BASE}/ru/sales",
        f"{BASE}/ru/promotions",
        f"{BASE}/ru/aksiya",
        f"{BASE}/ru/catalog?sort=discount",
    ]

    soup = None
    page_url = ""
    for url in urls_to_try:
        try:
            resp = requests.get(url, headers=HEADERS, timeout=20)
            if resp.status_code == 200 and len(resp.text) > 5000:
                soup = BeautifulSoup(resp.text, "lxml")
                page_url = url
                log.info(f"[Texnomart] Открыта страница: {url}")
                break
        except Exception as e:
            log.warning(f"[Texnomart] {url} недоступен: {e}")

    if not soup:
        log.error("[Texnomart] Страница акций не открылась.")
        return []

    deals = []

    # Texnomart обычно использует классы product-card, item-card, catalog-item
    cards = (
        soup.select(".product-card")
        or soup.select(".item-card")
        or soup.select(".catalog-item")
        or soup.select(".product-item")
        or soup.select("[class*='product']")
        or soup.select("article")
    )

    log.info(f"[Texnomart] Найдено карточек на странице: {len(cards)}")

    for card in cards:
        try:
            # Название товара
            title_el = (
                card.select_one(".product-card__name")
                or card.select_one(".item-name")
                or card.select_one(".product-name")
                or card.select_one("h2, h3, h4, .name, .title")
            )
            title = title_el.get_text(strip=True) if title_el else ""
            if not title or len(title) < 3:
                continue

            # Старая цена (зачёркнутая)
            old_el = (
                card.select_one(".old-price")
                or card.select_one(".price-old")
                or card.select_one(".price__old")
                or card.select_one("del")
                or card.select_one("s")
                or card.select_one("[class*='old']")
            )

            # Новая цена (текущая)
            new_el = (
                card.select_one(".current-price")
                or card.select_one(".price-current")
                or card.select_one(".price__current")
                or card.select_one(".new-price")
                or card.select_one("[class*='current']")
                or card.select_one("[class*='new']")
            )

            old_price = _parse_price(old_el.get_text() if old_el else "")
            new_price = _parse_price(new_el.get_text() if new_el else "")

            # Иногда скидка % уже написана на карточке — используем если цен нет
            if not old_price or not new_price:
                badge = card.select_one("[class*='badge'], [class*='discount'], [class*='sale']")
                badge_text = badge.get_text(strip=True) if badge else ""
                pct = re.search(r"(\d+)\s*%", badge_text)
                if pct and new_price:
                    d = int(pct.group(1))
                    if d >= min_discount:
                        old_price = round(new_price / (1 - d / 100))

            if not old_price or not new_price or old_price <= new_price:
                continue

            discount = round((old_price - new_price) / old_price * 100)
            if discount < min_discount:
                continue

            # Ссылка на товар
            link_el = card.select_one("a[href]")
            href = link_el["href"] if link_el else ""
            url_full = href if href.startswith("http") else f"{BASE}{href}"

            # Фото
            img_el = card.select_one("img")
            img_src = ""
            if img_el:
                img_src = (
                    img_el.get("data-src")
                    or img_el.get("data-lazy")
                    or img_el.get("data-original")
                    or img_el.get("src")
                    or ""
                )
                if img_src.startswith("//"):
                    img_src = f"https:{img_src}"
                elif img_src and not img_src.startswith("http"):
                    img_src = f"{BASE}{img_src}"

            deal_id = f"texnomart_{re.sub(r'[^a-z0-9]', '', title.lower())[:30]}"

            deals.append({
                "id":        deal_id,
                "title":     title[:100],
                "old_price": old_price,
                "new_price": new_price,
                "discount":  discount,
                "url":       url_full or page_url,
                "image":     img_src,
                "shop":      "Texnomart.uz 📺",
            })

            if len(deals) >= limit:
                break

        except Exception as e:
            log.warning(f"[Texnomart] Пропуск карточки: {e}")

    log.info(f"[Texnomart] Найдено {len(deals)} акций")
    return deals
