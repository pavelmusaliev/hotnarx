import asyncio, json, logging, os, re
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

BROWSER = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "ru-RU,ru;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}
JSON_HDR = {**BROWSER, "Accept": "application/json, text/plain, */*"}

def _price(text: str) -> int:
    d = re.sub(r"[^\d]", "", text or "")
    return int(d) if d else 0

# ════════════════════════════════════════════════════════════
#  UZUM — парсим HTML страницу акций
# ════════════════════════════════════════════════════════════
def parse_uzum(min_discount=30, limit=10) -> list[dict]:
    deals = []
    urls = [
        "https://uzum.uz/ru/promotions",
        "https://uzum.uz/ru/category/vse-tovary?sortBy=DISCOUNT_DESC",
    ]
    for url in urls:
        if deals: break
        try:
            r = requests.get(url, headers=BROWSER, timeout=25)
            if r.status_code != 200: continue
            soup = BeautifulSoup(r.text, "lxml")

            # Ищем JSON с данными (Next.js паттерн)
            for script in soup.find_all("script", id="__NEXT_DATA__"):
                try:
                    data = json.loads(script.string)
                    # Ходим вглубь в поисках массива products
                    text = json.dumps(data)
                    # Ищем пары fullPrice/purchasePrice
                    items = re.findall(
                        r'"title"\s*:\s*"([^"]+)".*?"fullPrice"\s*:\s*(\d+).*?"purchasePrice"\s*:\s*(\d+)',
                        text
                    )
                    for title, old_s, new_s in items[:limit*2]:
                        old, new = int(old_s), int(new_s)
                        if old <= new: continue
                        disc = round((old-new)/old*100)
                        if disc < min_discount: continue
                        deals.append({
                            "id": f"uzum_{re.sub(r'[^a-z0-9]','',title.lower())[:28]}",
                            "title": title[:100], "old_price": old, "new_price": new,
                            "discount": disc, "url": "https://uzum.uz/ru/promotions",
                            "image": "", "shop": "Uzum.uz 🛍"
                        })
                        if len(deals) >= limit: break
                except Exception: continue

            # Fallback: карточки в HTML
            if not deals:
                for card in soup.select("[class*='product'], [class*='ProductCard'], article")[:50]:
                    try:
                        t = card.select_one("[class*='title'],[class*='name'],h2,h3,h4")
                        title = t.get_text(strip=True) if t else ""
                        if not title or len(title) < 3: continue
                        old_el = card.select_one("[class*='old'],[class*='cross'],del,s")
                        new_el = card.select_one("[class*='current'],[class*='new'],[class*='sale'],[class*='price']")
                        old = _price(old_el.get_text() if old_el else "")
                        new = _price(new_el.get_text() if new_el else "")
                        if not old or not new or old <= new: continue
                        disc = round((old-new)/old*100)
                        if disc < min_discount: continue
                        a = card.select_one("a[href]")
                        href = a["href"] if a else ""
                        link = href if href.startswith("http") else f"https://uzum.uz{href}"
                        img = (card.select_one("img") or {})
                        img_src = img.get("data-src") or img.get("src") or "" if img else ""
                        deals.append({
                            "id": f"uzum_{re.sub(r'[^a-z0-9]','',title.lower())[:28]}",
                            "title": title[:100], "old_price": old, "new_price": new,
                            "discount": disc, "url": link, "image": img_src, "shop": "Uzum.uz 🛍"
                        })
                        if len(deals) >= limit: break
                    except Exception: continue
        except Exception as e:
            log.warning(f"[Uzum] {e}")
    log.info(f"[Uzum] {len(deals)} акций")
    return deals

# ════════════════════════════════════════════════════════════
#  KORZINKA
# ════════════════════════════════════════════════════════════
def parse_korzinka(min_discount=20, limit=10) -> list[dict]:
    deals = []
    for url in ["https://korzinka.uz/ru/promotions", "https://korzinka.uz/ru/catalog"]:
        if deals: break
        try:
            r = requests.get(url, headers=BROWSER, timeout=20)
            if r.status_code != 200 or len(r.text) < 2000: continue
            soup = BeautifulSoup(r.text, "lxml")
            for card in soup.select(".product-card, [class*='product'], article")[:60]:
                try:
                    t = card.select_one("h2,h3,h4,[class*='name'],[class*='title']")
                    title = t.get_text(strip=True) if t else ""
                    if not title or len(title) < 3: continue
                    old_el = card.select_one("[class*='old'],del,s")
                    new_el = card.select_one("[class*='new'],[class*='current'],[class*='price']")
                    old = _price(old_el.get_text() if old_el else "")
                    new = _price(new_el.get_text() if new_el else "")
                    if not old or not new or old <= new: continue
                    disc = round((old-new)/old*100)
                    if disc < min_discount: continue
                    a = card.select_one("a[href]")
                    href = a["href"] if a else ""
                    link = href if href.startswith("http") else f"https://korzinka.uz{href}"
                    img_el = card.select_one("img")
                    img = (img_el.get("data-src") or img_el.get("src") or "") if img_el else ""
                    deals.append({"id": f"kzk_{re.sub(r'[^a-z0-9]','',title.lower())[:28]}",
                                  "title": title[:100], "old_price": old, "new_price": new,
                                  "discount": disc, "url": link, "image": img, "shop": "Korzinka.uz 🛒"})
                    if len(deals) >= limit: break
                except Exception: continue
        except Exception as e:
            log.warning(f"[Korzinka] {e}")
    log.info(f"[Korzinka] {len(deals)} акций")
    return deals

# ════════════════════════════════════════════════════════════
#  TEXNOMART
# ════════════════════════════════════════════════════════════
def parse_texnomart(min_discount=20, limit=10) -> list[dict]:
    deals = []
    for url in ["https://texnomart.uz/ru/sales", "https://texnomart.uz/ru/catalog?special=sale",
                "https://texnomart.uz/ru/promotions"]:
        if deals: break
        try:
            r = requests.get(url, headers=BROWSER, timeout=20)
            if r.status_code != 200 or len(r.text) < 2000: continue
            soup = BeautifulSoup(r.text, "lxml")
            for card in soup.select(".product-card,.item-card,[class*='product'],article")[:60]:
                try:
                    t = card.select_one("h2,h3,h4,[class*='name'],[class*='title']")
                    title = t.get_text(strip=True) if t else ""
                    if not title or len(title) < 3: continue
                    old_el = card.select_one("[class*='old'],del,s")
                    new_el = card.select_one("[class*='current'],[class*='new'],[class*='price']")
                    old = _price(old_el.get_text() if old_el else "")
                    new = _price(new_el.get_text() if new_el else "")
                    if not old or not new or old <= new: continue
                    disc = round((old-new)/old*100)
                    if disc < min_discount: continue
                    a = card.select_one("a[href]")
                    href = a["href"] if a else ""
                    link = href if href.startswith("http") else f"https://texnomart.uz{href}"
                    img_el = card.select_one("img")
                    img = (img_el.get("data-src") or img_el.get("src") or "") if img_el else ""
                    deals.append({"id": f"txm_{re.sub(r'[^a-z0-9]','',title.lower())[:28]}",
                                  "title": title[:100], "old_price": old, "new_price": new,
                                  "discount": disc, "url": link, "image": img, "shop": "Texnomart.uz 📺"})
                    if len(deals) >= limit: break
                except Exception: continue
        except Exception as e:
            log.warning(f"[Texnomart] {e}")
    log.info(f"[Texnomart] {len(deals)} акций")
    return deals

# ════════════════════════════════════════════════════════════
#  MEDIAPARK
# ════════════════════════════════════════════════════════════
def parse_mediapark(min_discount=20, limit=10) -> list[dict]:
    deals = []
    for url in ["https://mediapark.uz/ru/sales", "https://mediapark.uz/ru/promotions",
                "https://mediapark.uz/ru/catalog?sale=1"]:
        if deals: break
        try:
            r = requests.get(url, headers=BROWSER, timeout=20)
            if r.status_code != 200 or len(r.text) < 2000: continue
            soup = BeautifulSoup(r.text, "lxml")
            for card in soup.select(".product-card,[class*='product'],article,.item")[:60]:
                try:
                    t = card.select_one("h2,h3,h4,[class*='name'],[class*='title']")
                    title = t.get_text(strip=True) if t else ""
                    if not title or len(title) < 3: continue
                    old_el = card.select_one("[class*='old'],del,s")
                    new_el = card.select_one("[class*='current'],[class*='new'],[class*='price']")
                    old = _price(old_el.get_text() if old_el else "")
                    new = _price(new_el.get_text() if new_el else "")
                    if not old or not new or old <= new: continue
                    disc = round((old-new)/old*100)
                    if disc < min_discount: continue
                    a = card.select_one("a[href]")
                    href = a["href"] if a else ""
                    link = href if href.startswith("http") else f"https://mediapark.uz{href}"
                    img_el = card.select_one("img")
                    img = (img_el.get("data-src") or img_el.get("src") or "") if img_el else ""
                    deals.append({"id": f"mp_{re.sub(r'[^a-z0-9]','',title.lower())[:28]}",
                                  "title": title[:100], "old_price": old, "new_price": new,
                                  "discount": disc, "url": link, "image": img, "shop": "MediaPark.uz 🖥"})
                    if len(deals) >= limit: break
                except Exception: continue
        except Exception as e:
            log.warning(f"[MediaPark] {e}")
    log.info(f"[MediaPark] {len(deals)} акций")
    return deals

# ════════════════════════════════════════════════════════════
#  OLCHA.UZ — работающий альтернатива Uzum с открытым API
# ════════════════════════════════════════════════════════════
def parse_olcha(min_discount=20, limit=10) -> list[dict]:
    """Olcha.uz — крупный узбекский маркетплейс с доступным API."""
    deals = []
    try:
        r = requests.get(
            "https://api.olcha.uz/v2/products",
            headers={**JSON_HDR, "Origin": "https://olcha.uz", "Referer": "https://olcha.uz/"},
            params={"sort": "discount", "order": "desc", "limit": 50, "page": 1},
            timeout=20,
        )
        if r.status_code != 200: raise Exception(f"HTTP {r.status_code}")
        data = r.json()
        products = data.get("data", {}).get("products", []) or data.get("products", []) or []
        for p in products:
            old = p.get("old_price") or p.get("base_price") or 0
            new = p.get("price") or p.get("sell_price") or 0
            if not old or not new or old <= new: continue
            disc = round((old-new)/old*100)
            if disc < min_discount: continue
            pid = str(p.get("id") or p.get("slug") or "")
            slug = p.get("slug") or pid
            imgs = p.get("images") or p.get("photos") or []
            img = (imgs[0].get("url") or imgs[0].get("original") or "") if imgs else ""
            deals.append({
                "id": f"olcha_{pid}",
                "title": (p.get("name") or p.get("title") or "")[:100],
                "old_price": old, "new_price": new, "discount": disc,
                "url": f"https://olcha.uz/product/{slug}",
                "image": img, "shop": "Olcha.uz 🍑"
            })
            if len(deals) >= limit: break
    except Exception as e:
        log.warning(f"[Olcha] {e}")
        # Fallback через HTML
        try:
            r = requests.get("https://olcha.uz/ru/sales", headers=BROWSER, timeout=20)
            soup = BeautifulSoup(r.text, "lxml")
            for card in soup.select("[class*='product'],[class*='Product'],article")[:40]:
                try:
                    t = card.select_one("h2,h3,h4,[class*='name'],[class*='title']")
                    title = t.get_text(strip=True) if t else ""
                    if not title or len(title) < 3: continue
                    old_el = card.select_one("[class*='old'],del,s")
                    new_el = card.select_one("[class*='new'],[class*='current'],[class*='price']")
                    old = _price(old_el.get_text() if old_el else "")
                    new = _price(new_el.get_text() if new_el else "")
                    if not old or not new or old <= new: continue
                    disc = round((old-new)/old*100)
                    if disc < min_discount: continue
                    a = card.select_one("a[href]")
                    href = (a["href"] if a else "")
                    link = href if href.startswith("http") else f"https://olcha.uz{href}"
                    deals.append({"id": f"olcha_{re.sub(r'[^a-z0-9]','',title.lower())[:28]}",
                                  "title": title[:100], "old_price": old, "new_price": new,
                                  "discount": disc, "url": link, "image": "", "shop": "Olcha.uz 🍑"})
                    if len(deals) >= limit: break
                except Exception: continue
        except Exception: pass
    log.info(f"[Olcha] {len(deals)} акций")
    return deals

# ════════════════════════════════════════════════════════════
#  АГРЕГАТОР
# ════════════════════════════════════════════════════════════
ALL_PARSERS = [parse_uzum, parse_olcha, parse_korzinka, parse_texnomart, parse_mediapark]

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
    log.info(f"Итого найдено: {len(deals)} акций")
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
        await bot.send_message(ADMIN_ID, "🤷 Новых акций не найдено. Магазины либо закрыли доступ, либо акций нет.")

@dp.message(Command("start"))
async def cmd_start(msg: Message):
    if msg.from_user.id != ADMIN_ID: return
    await msg.answer(
        f"👋 *Бот запущен!*\n\n"
        f"🏪 Uzum | Olcha | Korzinka | Texnomart | MediaPark\n"
        f"⏱ Каждые *{CHECK_HOURS} ч.* | 💥 Мин. скидка *{MIN_DISCOUNT}%*\n\n"
        f"/check — проверить сейчас\n/stats — статистика",
        parse_mode="Markdown")

@dp.message(Command("check"))
async def cmd_check(msg: Message):
    if msg.from_user.id != ADMIN_ID: return
    await msg.answer("🔍 Парсю магазины...")
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
