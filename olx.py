import logging
import re
import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

BASE = "https://www.olx.uz"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ru-RU,ru;q=0.9",
    "Referer": "https://www.olx.uz/",
}


def _parse_price(text: str) -> int:
    digits = re.sub(r"[^\d]", "", text or "")
    return int(digits) if digits else 0


def fetch(min_discount: int = 20, limit: int = 15) -> list[dict]:
    """
    OLX.uz — объявления со снижением цены.
    Парсит категорию с фильтром «Снизили цену» или свежие объявления.
    OLX не имеет явного раздела акций, поэтому ищем объявления
    со значком снижения цены или с пометкой «скидка» в названии.
    """
    urls_to_try = [
        f"{BASE}/ru/elektronika/?search[order]=filter_float_price%3Aasc",
        f"{BASE}/ru/moda-i-stil/",
        f"{BASE}/ru/dom-i-sad/",
    ]

    deals = []

    for page_url in urls_to_try:
        if len(deals) >= limit:
            break
        try:
            resp = requests.get(page_url, headers=HEADERS, timeout=20)
            if resp.status_code != 200:
                continue
            soup = BeautifulSoup(resp.text, "lxml")
        except Exception as e:
            log.warning(f"[OLX] {page_url}: {e}")
            continue

        # OLX карточки объявлений
        cards = (
            soup.select("[data-cy='l-card']")
            or soup.select(".offer-wrapper")
            or soup.select("article")
        )

        for card in cards:
            try:
                title_el = (
                    card.select_one("h6")
                    or card.select_one("h3")
                    or card.select_one("[data-testid='ad-title']")
                    or card.select_one(".title-cell strong")
                )
                title = title_el.get_text(strip=True) if title_el else ""
                if not title:
                    continue

                # Ищем только объявления с явным снижением цены
                has_discount = (
                    card.select_one("[data-testid='price-reduction']")
                    or card.select_one(".price-reduction")
                    or card.select_one("[class*='reduction']")
                    or card.select_one("[class*='discount']")
                )

                # Или скидка упомянута в названии
                discount_in_title = re.search(
                    r"скид|акци|sale|распрода|дёшево|дешево",
                    title.lower()
                )

                if not has_discount and not discount_in_title:
                    continue

                # Цена
                price_el = (
                    card.select_one("[data-testid='ad-price']")
                    or card.select_one(".price strong")
                    or card.select_one(".price-label")
                )
                new_price = _parse_price(price_el.get_text() if price_el else "")
                if not new_price:
                    continue

                # Старая цена (если показана)
                old_el = card.select_one("[data-testid='old-price']") or card.select_one(".old-price")
                old_price = _parse_price(old_el.get_text() if old_el else "")

                # Если нет старой цены — пропускаем (нет данных для расчёта скидки)
                if not old_price or old_price <= new_price:
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
                        or img_el.get("src")
                        or ""
                    )

                deal_id = f"olx_{re.sub(r'[^a-z0-9]', '', title.lower())[:30]}"

                deals.append({
                    "id":        deal_id,
                    "title":     title[:100],
                    "old_price": old_price,
                    "new_price": new_price,
                    "discount":  discount,
                    "url":       url_full,
                    "image":     img_src,
                    "shop":      "OLX.uz 📋",
                })

                if len(deals) >= limit:
                    break

            except Exception as e:
                log.warning(f"[OLX] Пропуск карточки: {e}")

    log.info(f"[OLX] Найдено {len(deals)} акций")
    return deals
