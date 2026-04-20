import asyncio, json, logging, os, re
from pathlib import Path

from playwright.sync_api import sync_playwright
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

LAUNCH_ARGS = ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]

def _price(v) -> int:
    d = re.sub(r"[^\d]", "", str(v or ""))
    return int(d) if d else 0

def _open_page(pw, url: str, wait_ms=4000):
    br = pw.chromium.launch(headless=True, args=LAUNCH_ARGS)
    ctx = br.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
        locale="ru-RU",
    )
    page = ctx.new_page()
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(wait_ms)
    return br, page

# ════════════════════════════════════════════════════════════
#  UZUM — вызываем их API изнутри браузера (с их куками)
# ════════════════════════════════════════════════════════════
def parse_uzum(min_discount=30, limit=10) -> list[dict]:
    deals = []
    try:
        with sync_playwright() as pw:
            br, page = _open_page(pw, "https://uzum.uz/ru/promotions", wait_ms=5000)

            # Делаем API-запрос изнутри браузера — он уже авторизован
            endpoints = [
                "https://api.uzum.uz/api/product/search?sortBy=DISCOUNT_DESC&size=60&page=0",
                "https://api.uzum.uz/api/v1/product/search?sortBy=DISCOUNT_DESC&size=60&page=0",
                "https://api.uzum.uz/api/main/root-categories",
            ]
            raw = None
            for ep in endpoints:
                try:
                    raw = page.evaluate(f"""
                        async () => {{
                            const r = await fetch("{ep}", {{
                                headers: {{
                                    "Accept": "application/json",
                                    "x-iid": "uzum-web"
                                }}
                            }});
                            if (!r.ok) return null;
                            return await r.json();
                        }}
                    """)
                    if raw:
                        log.info(f"[Uzum] Сработал endpoint: {ep[:60]}")
                        break
                except Exception as e:
                    log.warning(f"[Uzum] {ep[:50]}: {e}")

            br.close()

            if not raw:
                log.info("[Uzum] 0 акций — все endpoints вернули null")
                return []

            products = (raw.get("payload", {}).get("products", [])
                        or raw.get("products", [])
                        or raw.get("data", {}).get("products", [])
                        or [])

            log.info(f"[Uzum] Получено товаров из API: {len(products)}")

            for p in products:
                old = p.get("fullPrice") or p.get("originalPrice") or 0
                new = p.get("purchasePrice") or p.get("price") or 0
                if not old or not new or old <= new: continue
                disc = round((old - new) / old * 100)
                if disc < min_discount: continue
                pid = str(p.get("productId") or p.get("id") or "")
                photos = p.get("photos") or p.get("images") or []
                img = (photos[0].get("high") or photos[0].get("url") or "") if photos else ""
                deals.append({
                    "id": f"uzum_{pid}",
                    "title": (p.get("title") or p.get("name") or "Uzum товар")[:100],
                    "old_price": old, "new_price": new, "discount": disc,
                    "url": f"https://uzum.uz/ru/product/{p.get('slug') or pid}",
                    "image": img, "shop": "Uzum.uz 🛍",
                })
                if len(deals) >= limit: break

    except Exception as e:
        log.error(f"[Uzum] {e}")

    log.info(f"[Uzum] {len(deals)} акций")
    return deals

# ════════════════════════════════════════════════════════════
#  OLCHA — вызываем API изнутри браузера
# ════════════════════════════════════════════════════════════
def parse_olcha(min_discount=20, limit=10) -> list[dict]:
    deals = []
    try:
        with sync_playwright() as pw:
            br, page = _open_page(pw, "https://olcha.uz/ru/category/all?sort=discount", wait_ms=5000)

            endpoints = [
                "https://api.olcha.uz/v2/products?sort=discount&order=desc&limit=50&page=1",
                "https://api.olcha.uz/v1/products?sort=discount&limit=50",
                "https://olcha.uz/api/v2/products?sort=discount&limit=50",
            ]
            raw = None
            for ep in endpoints:
                try:
                    raw = page.evaluate(f"""
                        async () => {{
                            const r = await fetch("{ep}", {{"headers": {{"Accept": "application/json"}}}});
                            if (!r.ok) return null;
                            return await r.json();
                        }}
                    """)
                    if raw:
                        log.info(f"[Olcha] Сработал: {ep[:60]}")
                        break
                except Exception: continue

            br.close()

            if not raw:
                log.info("[Olcha] 0 акций")
                return []

            products = (raw.get("data", {}).get("products", [])
                        or raw.get("products", [])
                        or raw.get("items", [])
                        or [])

            log.info(f"[Olcha] Товаров из API: {len(products)}")

            for p in products:
                old = p.get("old_price") or p.get("base_price") or p.get("price_old") or 0
                new = p.get("price") or p.get("sell_price") or p.get("price_new") or 0
                if not old or not new or old <= new: continue
                disc = round((old - new) / old * 100)
                if disc < min_discount: continue
                pid = str(p.get("id") or p.get("slug") or "")
                imgs = p.get("images") or p.get("photos") or []
                img = (imgs[0].get("url") or "") if imgs else p.get("image", "")
                deals.append({
                    "id": f"olcha_{pid}",
                    "title": (p.get("name") or p.get("title") or "Olcha товар")[:100],
                    "old_price": old, "new_price": new, "discount": disc,
                    "url": f"https://olcha.uz/product/{p.get('slug') or pid}",
                    "image": img, "shop": "Olcha.uz 🍑",
                })
                if len(deals) >= limit: break

    except Exception as e:
        log.error(f"[Olcha] {e}")

    log.info(f"[Olcha] {len(deals)} акций")
    return deals

# ════════════════════════════════════════════════════════════
#  TEXNOMART — вызываем API изнутри браузера
# ════════════════════════════════════════════════════════════
def parse_texnomart(min_discount=20, limit=10) -> list[dict]:
    deals = []
    try:
        with sync_playwright() as pw:
            br, page = _open_page(pw, "https://texnomart.uz/ru/sales", wait_ms=5000)

            endpoints = [
                "https://texnomart.uz/api/v1/products?sale=true&sort=discount&limit=50",
                "https://texnomart.uz/api/products?special=sale&sort=discount&limit=50",
                "https://api.texnomart.uz/v1/products?sale=1&sort=discount",
            ]
            raw = None
            for ep in endpoints:
                try:
                    raw = page.evaluate(f"""
                        async () => {{
                            const r = await fetch("{ep}", {{"headers": {{"Accept": "application/json"}}}});
                            if (!r.ok) return null;
                            return await r.json();
                        }}
                    """)
                    if raw:
                        log.info(f"[Texnomart] Сработал: {ep[:60]}")
                        break
                except Exception: continue

            br.close()

            if not raw:
                log.info("[Texnomart] 0 акций")
                return []

            products = (raw.get("data", {}).get("products", [])
                        or raw.get("products", [])
                        or raw.get("items", [])
                        or [])

            log.info(f"[Texnomart] Товаров: {len(products)}")

            for p in products:
                old = p.get("old_price") or p.get("price_old") or p.get("original_price") or 0
                new = p.get("price") or p.get("sell_price") or 0
                if not old or not new or old <= new: continue
                disc = round((old - new) / old * 100)
                if disc < min_discount: continue
                pid = str(p.get("id") or p.get("slug") or "")
                imgs = p.get("images") or p.get("photos") or []
                img = (imgs[0].get("url") or "") if imgs else p.get("image", "")
                deals.append({
                    "id": f"txm_{pid}",
                    "title": (p.get("name") or p.get("title") or "Texnomart товар")[:100],
                    "old_price": old, "new_price": new, "discount": disc,
                    "url": f"https://texnomart.uz/ru/product/{p.get('slug') or pid}",
                    "image": img, "shop": "Texnomart.uz 📺",
                })
                if len(deals) >= limit: break

    except Exception as e:
        log.error(f"[Texnomart] {e}")

    log.info(f"[Texnomart] {len(deals)} акций")
    return deals

# ════════════════════════════════════════════════════════════
#  АГРЕГАТОР
# ════════════════════════════════════════════════════════════
def fetch_all_sync() -> list[dict]:
    result = []
    for fn in [parse_uzum, parse_olcha, parse_texnomart]:
        try:
            result.extend(fn(min_discount=MIN_DISCOUNT, limit=LIMIT_PER_SHOP))
        except Exception as e:
            log.error(f"{fn.__name__}: {e}")
    result.sort(key=lambda d: d["discount"], reverse=True)
    return result

# ════════════════════════════════════════════════════════════
#  БОТ
# ════════════════════════════════════════════════════════════
def load_seen() -> set:
    return set(json.loads(SEEN_FILE.read_text())) if SEEN_FILE.exists() else set()

def save_seen(s: set):
    SEEN_FILE.write_text(json.dumps(list(s)))

pending: dict[str, dict] = {}

def fmt(p: int) -> str:
    return f"{p:,}".replace(",", " ")

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
    log.info("Старт проверки...")
    seen = load_seen()
    deals = await asyncio.to_thread(fetch_all_sync)
    log.info(f"Итого: {len(deals)} акций")

    new_count = 0
    for d in deals:
        if d["id"] in seen: continue
        seen.add(d["id"])
        pending[d["id"]] = d
        try:
            if d.get("image"):
                await bot.send_photo(ADMIN_ID, d["image"], caption=preview_text(d),
                                     parse_mode="Markdown", reply_markup=kb(d["id"]))
            else:
                await bot.send_message(ADMIN_ID, preview_text(d),
                                       parse_mode="Markdown", reply_markup=kb(d["id"]))
            new_count += 1
            await asyncio.sleep(0.4)
        except Exception as e:
            log.error(f"Отправка: {e}")

    save_seen(seen)
    if new_count:
        await bot.send_message(ADMIN_ID, f"✅ Новых акций: *{new_count}*", parse_mode="Markdown")
    else:
        await bot.send_message(ADMIN_ID,
            "🤷 0 акций. Смотрите логи в Railway — "
            "там написано какие endpoints сработали. Скиньте мне логи.",
            parse_mode="Markdown")

@dp.message(Command("start"))
async def cmd_start(msg: Message):
    if msg.from_user.id != ADMIN_ID: return
    await msg.answer(
        f"👋 *Бот запущен!*\n\n"
        f"🌐 Режим: fetch изнутри браузера\n"
        f"🏪 Uzum | Olcha | Texnomart\n"
        f"⏱ Каждые *{CHECK_HOURS} ч.* | 💥 Мин. скидка *{MIN_DISCOUNT}%*\n\n"
        f"/check — проверить сейчас\n/stats — статистика",
        parse_mode="Markdown")

@dp.message(Command("check"))
async def cmd_check(msg: Message):
    if msg.from_user.id != ADMIN_ID: return
    await msg.answer("🔍 Запускаю браузеры... (~2 мин)")
    await check_and_notify()

@dp.message(Command("stats"))
async def cmd_stats(msg: Message):
    if msg.from_user.id != ADMIN_ID: return
    await msg.answer(
        f"📊 Просмотрено: *{len(load_seen())}*\nОжидают: *{len(pending)}*",
        parse_mode="Markdown")

@dp.callback_query(F.data.startswith("pub:"))
async def on_pub(cb: CallbackQuery):
    d = pending.pop(cb.data.split(":", 1)[1], None)
    if not d: await cb.answer("Уже обработано.", show_alert=True); return
    try:
        if d.get("image"):
            await bot.send_photo(CHANNEL_ID, d["image"], caption=post_text(d), parse_mode="Markdown")
        else:
            await bot.send_message(CHANNEL_ID, post_text(d), parse_mode="Markdown")
        await cb.message.edit_reply_markup(reply_markup=None)
        await cb.answer("✅ Опубликовано!")
    except Exception as e:
        await cb.answer(str(e), show_alert=True)

@dp.callback_query(F.data.startswith("skip:"))
async def on_skip(cb: CallbackQuery):
    pending.pop(cb.data.split(":", 1)[1], None)
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
