import asyncio
import json
import logging
import os
import re
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN      = os.getenv("BOT_TOKEN")
ADMIN_ID       = int(os.getenv("ADMIN_ID"))
CHANNEL_ID     = os.getenv("CHANNEL_ID")
MIN_DISCOUNT   = int(os.getenv("MIN_DISCOUNT", "30"))
CHECK_HOURS    = int(os.getenv("CHECK_HOURS", "3"))
LIMIT_PER_SHOP = int(os.getenv("LIMIT_PER_SHOP", "10"))
SEEN_FILE      = Path("seen_deals.json")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp  = Dispatcher()

# ════════════════════════════════════════════════════════════
#  ПАРСЕРЫ
# ════════════════════════════════════════════════════════════

BROWSER = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "ru-RU,ru;q=0.9",
}

def _price(text: str) -> int:
    d = re.sub(r"[^\d]", "", text or "")
    return int(d) if d else 0


def parse_uzum(min_discount=30, limit=10) -> list[dict]:
    try:
        r = requests.get(
            "https://api.uzum.uz/api/product/search",
            headers={**BROWSER, "Origin": "https://uzum.uz", "Referer": "https://uzum.uz/", "Accept": "application/json"},
            params={"sortBy": "DISCOUNT_DESC", "size": 60, "page": 0},
            timeout=20,
        )
        r.raise_for_status()
        products = r.json().get("payload", {}).get("products", []) or r.json().get("products", []) or []
    except Exception as e:
        log.error(f"[Uzum] {e}"); return []

    deals = []
    for p in products:
        old = p.get("fullPrice") or p.get("originalPrice") or 0
        new = p.get("purchasePrice") or p.get("price") or 0
        if not old or not new or old <= new: continue
        disc = round((old - new) / old * 100)
        if disc < min_discount: continue
        pid = str(p.get("productId") or p.get("id") or "")
        photos = p.get("photos") or p.get("images") or []
        img = (photos[0].get("high") or photos[0].get("url") or "") if photos else ""
        deals.append({"id": f"uzum_{pid}", "title": (p.get("title") or "")[:100],
                      "old_price": old, "new_price": new, "discount": disc,
                      "url": f"https://uzum.uz/product/{p.get('slug') or pid}",
                      "image": img, "shop": "Uzum.uz 🛍"})
        if len(deals) >= limit: break
    log.info(f"[Uzum] {len(deals)} акций"); return deals


def parse_wildberries(min_discount=30, limit=10) -> list[dict]:
    categories = [("Одежда", 306), ("Электроника", 6119), ("Обувь", 4764)]
    deals, seen = [], set()
    for cat_name, cat_id in categories:
        if len(deals) >= limit: break
        try:
            r = requests.get(
                "https://catalog.wb.ru/catalog/sale/catalog",
                headers={**BROWSER, "Origin": "https://www.wildberries.ru", "Referer": "https://www.wildberries.ru/"},
                params={"appType": 1, "curr": "uzs", "dest": -1257786, "sort": "sale", "spp": 30, "page": 1, "cat": cat_id},
                timeout=20,
            )
            products = r.json().get("data", {}).get("products", []) or []
        except Exception as e:
            log.warning(f"[WB] {cat_name}: {e}"); continue
        for p in products:
            pid = int(p.get("id", 0))
            if not pid or pid in seen: continue
            seen.add(pid)
            disc = p.get("sale", 0)
            if disc < min_discount: continue
            old = round((p.get("priceU") or 0) / 100)
            new = round((p.get("salePriceU") or 0) / 100)
            if not old or not new or old <= new: continue
            vol = pid // 100_000
            img = f"https://basket-{str(vol).zfill(2)}.wb.ru/vol{vol}/part{pid//1000}/{pid}/images/tm/1.jpg"
            brand = p.get("brand", "")
            title = f"{brand} — {p.get('name','')}" if brand else p.get("name", "")
            deals.append({"id": f"wb_{pid}", "title": title[:100],
                          "old_price": old, "new_price": new, "discount": disc,
                          "url": f"https://www.wildberries.ru/catalog/{pid}/detail.aspx",
                          "image": img, "shop": f"Wildberries 🍇 ({cat_name})"})
            if len(deals) >= limit: break
    log.info(f"[WB] {len(deals)} акций"); return deals


def _html_deals(url, shop_label, shop_prefix, min_discount, limit) -> list[dict]:
    """Универсальный HTML-парсер для Korzinka / Texnomart / MediaPark."""
    try:
        r = requests.get(url, headers=BROWSER, timeout=20)
        if r.status_code != 200 or len(r.text) < 3000: return []
        soup = BeautifulSoup(r.text, "lxml")
    except Exception as e:
        log.warning(f"[{shop_label}] {e}"); return []

    cards = (soup.select(".product-card") or soup.select(".catalog-item")
             or soup.select(".product-item") or soup.select("article"))

    deals = []
    for card in cards:
        try:
            t = card.select_one("h2,h3,h4,.product-name,.item-name,.name,.title")
            title = t.get_text(strip=True) if t else ""
            if not title or len(title) < 3: continue

            old_el = card.select_one(".old-price,.price-old,del,s,[class*='old']")
            new_el = card.select_one(".new-price,.price-new,.current-price,[class*='current'],[class*='new'],.price")
            old = _price(old_el.get_text() if old_el else "")
            new = _price(new_el.get_text() if new_el else "")
            if not old or not new or old <= new: continue

            disc = round((old - new) / old * 100)
            if disc < min_discount: continue

            a = card.select_one("a[href]")
            href = a["href"] if a else ""
            base = url.split("/", 3)[:3]; base = "/".join(base)
            link = href if href.startswith("http") else f"{base}{href}"

            img_el = card.select_one("img")
            img = ""
            if img_el:
                img = img_el.get("data-src") or img_el.get("data-lazy") or img_el.get("src") or ""
                if img.startswith("//"): img = "https:" + img
                elif img and not img.startswith("http"): img = base + img

            deals.append({"id": f"{shop_prefix}_{re.sub(r'[^a-z0-9]','',title.lower())[:28]}",
                          "title": title[:100], "old_price": old, "new_price": new,
                          "discount": disc, "url": link or url, "image": img, "shop": shop_label})
            if len(deals) >= limit: break
        except Exception: continue
    log.info(f"[{shop_label}] {len(deals)} акций"); return deals


def parse_korzinka(min_discount=20, limit=10) -> list[dict]:
    for url in ["https://korzinka.uz/ru/promotions", "https://korzinka.uz/ru/actions"]:
        d = _html_deals(url, "Korzinka.uz 🛒", "kzk", min_discount, limit)
        if d: return d
    return []

def parse_texnomart(min_discount=20, limit=10) -> list[dict]:
    for url in ["https://texnomart.uz/ru/sales", "https://texnomart.uz/ru/promotions"]:
        d = _html_deals(url, "Texnomart.uz 📺", "txm", min_discount, limit)
        if d: return d
    return []

def parse_mediapark(min_discount=20, limit=10) -> list[dict]:
    for url in ["https://mediapark.uz/ru/sales", "https://mediapark.uz/ru/promotions"]:
        d = _html_deals(url, "MediaPark.uz 🖥", "mp", min_discount, limit)
        if d: return d
    return []

def parse_olx(min_discount=30, limit=10) -> list[dict]:
    deals = []
    for url in ["https://www.olx.uz/ru/elektronika/", "https://www.olx.uz/ru/moda-i-stil/"]:
        if len(deals) >= limit: break
        try:
            r = requests.get(url, headers=BROWSER, timeout=20)
            soup = BeautifulSoup(r.text, "lxml")
        except Exception: continue
        for card in soup.select("[data-cy='l-card'], .offer-wrapper, article"):
            try:
                t = card.select_one("h6,h3,[data-testid='ad-title'],.title-cell strong")
                title = t.get_text(strip=True) if t else ""
                if not title: continue
                old_el = card.select_one("[data-testid='old-price'],.old-price")
                new_el = card.select_one("[data-testid='ad-price'],.price strong")
                old = _price(old_el.get_text() if old_el else "")
                new = _price(new_el.get_text() if new_el else "")
                if not old or not new or old <= new: continue
                disc = round((old - new) / old * 100)
                if disc < min_discount: continue
                a = card.select_one("a[href]")
                href = a["href"] if a else ""
                link = href if href.startswith("http") else f"https://www.olx.uz{href}"
                img_el = card.select_one("img")
                img = (img_el.get("data-src") or img_el.get("src") or "") if img_el else ""
                deals.append({"id": f"olx_{re.sub(r'[^a-z0-9]','',title.lower())[:28]}",
                              "title": title[:100], "old_price": old, "new_price": new,
                              "discount": disc, "url": link, "image": img, "shop": "OLX.uz 📋"})
                if len(deals) >= limit: break
            except Exception: continue
    log.info(f"[OLX] {len(deals)} акций"); return deals


ALL_PARSERS = [parse_uzum, parse_wildberries, parse_korzinka,
               parse_texnomart, parse_mediapark, parse_olx]

def fetch_all() -> list[dict]:
    result = []
    for fn in ALL_PARSERS:
        try: result.extend(fn(min_discount=MIN_DISCOUNT, limit=LIMIT_PER_SHOP))
        except Exception as e: log.error(f"Парсер {fn.__name__}: {e}")
    result.sort(key=lambda d: d["discount"], reverse=True)
    return result


# ════════════════════════════════════════════════════════════
#  БОТ
# ════════════════════════════════════════════════════════════

def load_seen() -> set:
    return set(json.loads(SEEN_FILE.read_text())) if SEEN_FILE.exists() else set()

def save_seen(s: set): SEEN_FILE.write_text(json.dumps(list(s)))

pending: dict[str, dict] = {}

def fmt(p: int) -> str: return f"{p:,}".replace(",", " ")

def post_text(d: dict) -> str:
    return (f"🔥 *{d['title']}*\n\n"
            f"~~{fmt(d['old_price'])} сум~~ → *{fmt(d['new_price'])} сум*\n"
            f"💥 Скидка: *{d['discount']}%*\n\n"
            f"🏪 {d['shop']}\n"
            f"👉 [Смотреть товар]({d['url']})")

def preview_text(d: dict) -> str:
    return f"📦 *Новая акция:* {d['shop']}\n\n" + post_text(d) + "\n\n_Публиковать?_"

def kb(deal_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Опубликовать", callback_data=f"pub:{deal_id}"),
        InlineKeyboardButton(text="❌ Пропустить",   callback_data=f"skip:{deal_id}"),
    ]])

async def check_and_notify():
    log.info("Запускаю все парсеры...")
    seen = load_seen()
    deals = fetch_all()
    new_count = 0
    for d in deals:
        if d["id"] in seen: continue
        seen.add(d["id"]); pending[d["id"]] = d
        try:
            if d.get("image"):
                await bot.send_photo(ADMIN_ID, d["image"], caption=preview_text(d),
                                     parse_mode="Markdown", reply_markup=kb(d["id"]))
            else:
                await bot.send_message(ADMIN_ID, preview_text(d),
                                       parse_mode="Markdown", reply_markup=kb(d["id"]))
            new_count += 1
            await asyncio.sleep(0.4)
        except Exception as e: log.error(f"Отправка: {e}")
    save_seen(seen)
    if new_count:
        await bot.send_message(ADMIN_ID, f"✅ Готово. Новых акций: *{new_count}*", parse_mode="Markdown")
    else:
        log.info("Новых акций не найдено.")

@dp.message(Command("start"))
async def cmd_start(msg: Message):
    if msg.from_user.id != ADMIN_ID: return
    await msg.answer(
        f"👋 *Бот запущен!*\n\n"
        f"🏪 Uzum | Wildberries | Korzinka | Texnomart | MediaPark | OLX\n"
        f"⏱ Каждые *{CHECK_HOURS} ч.* | 💥 Мин. скидка *{MIN_DISCOUNT}%*\n\n"
        f"/check — проверить сейчас\n/stats — статистика",
        parse_mode="Markdown")

@dp.message(Command("check"))
async def cmd_check(msg: Message):
    if msg.from_user.id != ADMIN_ID: return
    await msg.answer("🔍 Парсю 6 магазинов...")
    await check_and_notify()

@dp.message(Command("stats"))
async def cmd_stats(msg: Message):
    if msg.from_user.id != ADMIN_ID: return
    await msg.answer(f"📊 Просмотрено: *{len(load_seen())}* | Ожидают: *{len(pending)}*",
                     parse_mode="Markdown")

@dp.callback_query(F.data.startswith("pub:"))
async def on_pub(cb: CallbackQuery):
    d = pending.pop(cb.data.split(":",1)[1], None)
    if not d: await cb.answer("Уже обработано.", show_alert=True); return
    try:
        if d.get("image"):
            await bot.send_photo(CHANNEL_ID, d["image"], caption=post_text(d), parse_mode="Markdown")
        else:
            await bot.send_message(CHANNEL_ID, post_text(d), parse_mode="Markdown")
        await cb.message.edit_reply_markup(reply_markup=None)
        await cb.answer("✅ Опубликовано!")
    except Exception as e: await cb.answer(str(e), show_alert=True)

@dp.callback_query(F.data.startswith("skip:"))
async def on_skip(cb: CallbackQuery):
    pending.pop(cb.data.split(":",1)[1], None)
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.answer("Пропущено.")

async def main():
    scheduler = AsyncIOScheduler()
    scheduler.add_job(check_and_notify, "interval", hours=CHECK_HOURS)
    scheduler.start()
    await check_and_notify()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
