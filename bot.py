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

def _price(text: str) -> int:
    d = re.sub(r"[^\d]", "", str(text or ""))
    return int(d) if d else 0

def _browser_args():
    return ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
            "--disable-blink-features=AutomationControlled"]

# ════════════════════════════════════════════════════════════
#  UZUM — перехватываем API-ответы которые сайт сам запрашивает
# ════════════════════════════════════════════════════════════
def parse_uzum(min_discount=30, limit=10) -> list[dict]:
    captured = []

    def on_response(resp):
        try:
            if "api.uzum.uz" in resp.url and resp.status == 200:
                if any(k in resp.url for k in ["search", "product", "promotion", "campaign"]):
                    data = resp.json()
                    captured.append(data)
        except Exception:
            pass

    try:
        with sync_playwright() as pw:
            br = pw.chromium.launch(headless=True, args=_browser_args())
            ctx = br.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
                locale="ru-RU",
            )
            page = ctx.new_page()
            page.on("response", on_response)
            page.goto("https://uzum.uz/ru/promotions", wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(3000)
            br.close()
    except Exception as e:
        log.warning(f"[Uzum] Browser error: {e}")

    # Ищем товары во всех перехваченных ответах
    deals = []
    seen_ids = set()
    for data in captured:
        raw_text = json.dumps(data)
        # Рекурсивно ищем массивы с товарами
        products = (data.get("payload", {}).get("products", [])
                    or data.get("products", [])
                    or data.get("data", {}).get("products", [])
                    or [])
        for p in products:
            try:
                pid = str(p.get("productId") or p.get("id") or "")
                if not pid or pid in seen_ids: continue
                seen_ids.add(pid)
                old = p.get("fullPrice") or p.get("originalPrice") or 0
                new = p.get("purchasePrice") or p.get("price") or 0
                if not old or not new or old <= new: continue
                disc = round((old - new) / old * 100)
                if disc < min_discount: continue
                photos = p.get("photos") or p.get("images") or []
                img = (photos[0].get("high") or photos[0].get("url") or "") if photos else ""
                slug = p.get("slug") or pid
                deals.append({
                    "id": f"uzum_{pid}",
                    "title": (p.get("title") or p.get("name") or "Товар Uzum")[:100],
                    "old_price": old, "new_price": new, "discount": disc,
                    "url": f"https://uzum.uz/ru/product/{slug}",
                    "image": img, "shop": "Uzum.uz 🛍",
                })
                if len(deals) >= limit: break
            except Exception: continue
        if len(deals) >= limit: break

    log.info(f"[Uzum] {len(deals)} акций (перехвачено {len(captured)} API-ответов)")
    return deals

# ════════════════════════════════════════════════════════════
#  OLCHA — перехватываем API
# ════════════════════════════════════════════════════════════
def parse_olcha(min_discount=20, limit=10) -> list[dict]:
    captured = []

    def on_response(resp):
        try:
            if ("olcha.uz" in resp.url and resp.status == 200
                    and "product" in resp.url):
                data = resp.json()
                captured.append(data)
        except Exception:
            pass

    try:
        with sync_playwright() as pw:
            br = pw.chromium.launch(headless=True, args=_browser_args())
            ctx = br.new_context(locale="ru-RU")
            page = ctx.new_page()
            page.on("response", on_response)
            page.goto("https://olcha.uz/ru/category/all?sort=discount&order=desc",
                      wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(3000)
            br.close()
    except Exception as e:
        log.warning(f"[Olcha] Browser error: {e}")

    deals = []
    seen_ids = set()
    for data in captured:
        products = (data.get("data", {}).get("products", [])
                    or data.get("products", [])
                    or data.get("items", [])
                    or [])
        for p in products:
            try:
                pid = str(p.get("id") or p.get("slug") or "")
                if not pid or pid in seen_ids: continue
                seen_ids.add(pid)
                old = p.get("old_price") or p.get("base_price") or p.get("price_old") or 0
                new = p.get("price") or p.get("sell_price") or p.get("price_new") or 0
                if not old or not new or old <= new: continue
                disc = round((old - new) / old * 100)
                if disc < min_discount: continue
                imgs = p.get("images") or p.get("photos") or []
                img = (imgs[0].get("url") or imgs[0].get("original") or "") if imgs else p.get("image", "")
                slug = p.get("slug") or pid
                deals.append({
                    "id": f"olcha_{pid}",
                    "title": (p.get("name") or p.get("title") or "Товар Olcha")[:100],
                    "old_price": old, "new_price": new, "discount": disc,
                    "url": f"https://olcha.uz/product/{slug}",
                    "image": img, "shop": "Olcha.uz 🍑",
                })
                if len(deals) >= limit: break
            except Exception: continue
        if len(deals) >= limit: break

    log.info(f"[Olcha] {len(deals)} акций (перехвачено {len(captured)} API-ответов)")
    return deals

# ════════════════════════════════════════════════════════════
#  TEXNOMART — перехватываем API
# ════════════════════════════════════════════════════════════
def parse_texnomart(min_discount=20, limit=10) -> list[dict]:
    captured = []

    def on_response(resp):
        try:
            if "texnomart.uz" in resp.url and resp.status == 200:
                ct = resp.headers.get("content-type", "")
                if "json" in ct:
                    data = resp.json()
                    captured.append((resp.url, data))
        except Exception:
            pass

    try:
        with sync_playwright() as pw:
            br = pw.chromium.launch(headless=True, args=_browser_args())
            ctx = br.new_context(locale="ru-RU")
            page = ctx.new_page()
            page.on("response", on_response)
            page.goto("https://texnomart.uz/ru/sales", wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(3000)
            br.close()
    except Exception as e:
        log.warning(f"[Texnomart] Browser error: {e}")

    deals = []
    seen_ids = set()
    for url, data in captured:
        log.info(f"[Texnomart] API: {url[:80]}")
        products = (data.get("data", {}).get("products", [])
                    or data.get("products", [])
                    or data.get("items", [])
                    or [])
        for p in products:
            try:
                pid = str(p.get("id") or p.get("slug") or "")
                if not pid or pid in seen_ids: continue
                seen_ids.add(pid)
                old = (p.get("old_price") or p.get("price_old")
                       or p.get("base_price") or p.get("original_price") or 0)
                new = p.get("price") or p.get("sell_price") or p.get("price_new") or 0
                if not old or not new or old <= new: continue
                disc = round((old - new) / old * 100)
                if disc < min_discount: continue
                imgs = p.get("images") or p.get("photos") or []
                img = (imgs[0].get("url") or imgs[0].get("src") or "") if imgs else p.get("image", "")
                slug = p.get("slug") or pid
                deals.append({
                    "id": f"txm_{pid}",
                    "title": (p.get("name") or p.get("title") or "Товар Texnomart")[:100],
                    "old_price": old, "new_price": new, "discount": disc,
                    "url": f"https://texnomart.uz/ru/product/{slug}",
                    "image": img, "shop": "Texnomart.uz 📺",
                })
                if len(deals) >= limit: break
            except Exception: continue
        if len(deals) >= limit: break

    log.info(f"[Texnomart] {len(deals)} акций")
    return deals

# ════════════════════════════════════════════════════════════
#  АГРЕГАТОР
# ════════════════════════════════════════════════════════════
ALL_PARSERS = [parse_uzum, parse_olcha, parse_texnomart]

def fetch_all_sync() -> list[dict]:
    result = []
    for fn in ALL_PARSERS:
        try:
            result.extend(fn(min_discount=MIN_DISCOUNT, limit=LIMIT_PER_SHOP))
        except Exception as e:
            log.error(f"Парсер {fn.__name__}: {e}")
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
    log.info("Запускаю парсеры (перехват API)...")
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
        await bot.send_message(ADMIN_ID,
            f"✅ Готово. Новых акций: *{new_count}*", parse_mode="Markdown")
    else:
        await bot.send_message(ADMIN_ID,
            "🤷 Акций не найдено.\n\nСмотрите логи в Railway — "
            "там будут строки `[Uzum] API:` с реальными URL которые перехватили. "
            "Скиньте их мне.", parse_mode="Markdown")

@dp.message(Command("start"))
async def cmd_start(msg: Message):
    if msg.from_user.id != ADMIN_ID: return
    await msg.answer(
        f"👋 *Бот запущен!*\n\n"
        f"🌐 Режим: перехват API через Playwright\n"
        f"🏪 Uzum | Olcha | Texnomart\n"
        f"⏱ Каждые *{CHECK_HOURS} ч.* | 💥 Мин. скидка *{MIN_DISCOUNT}%*\n\n"
        f"/check — проверить сейчас\n/stats — статистика",
        parse_mode="Markdown")

@dp.message(Command("check"))
async def cmd_check(msg: Message):
    if msg.from_user.id != ADMIN_ID: return
    await msg.answer("🔍 Открываю сайты, перехватываю API... (~1 мин)")
    await check_and_notify()

@dp.message(Command("stats"))
async def cmd_stats(msg: Message):
    if msg.from_user.id != ADMIN_ID: return
    await msg.answer(
        f"📊 Просмотрено: *{len(load_seen())}*\nОжидают одобрения: *{len(pending)}*",
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
