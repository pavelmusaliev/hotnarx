import asyncio
import json
import logging
import os
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

from parsers import fetch_all

load_dotenv()

BOT_TOKEN        = os.getenv("BOT_TOKEN")
ADMIN_ID         = int(os.getenv("ADMIN_ID"))
CHANNEL_ID       = os.getenv("CHANNEL_ID")
MIN_DISCOUNT     = int(os.getenv("MIN_DISCOUNT", "30"))
CHECK_HOURS      = int(os.getenv("CHECK_HOURS", "3"))
LIMIT_PER_SHOP   = int(os.getenv("LIMIT_PER_SHOP", "10"))

SEEN_FILE = Path("seen_deals.json")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp  = Dispatcher()

def load_seen() -> set:
    if SEEN_FILE.exists():
        return set(json.loads(SEEN_FILE.read_text()))
    return set()

def save_seen(seen: set):
    SEEN_FILE.write_text(json.dumps(list(seen)))

pending: dict[str, dict] = {}

def fmt_price(p: int) -> str:
    return f"{p:,}".replace(",", " ")

def format_post(deal: dict) -> str:
    return (
        f"🔥 *{deal['title']}*\n\n"
        f"~~{fmt_price(deal['old_price'])} сум~~ → *{fmt_price(deal['new_price'])} сум*\n"
        f"💥 Скидка: *{deal['discount']}%*\n\n"
        f"🏪 {deal['shop']}\n"
        f"👉 [Смотреть товар]({deal['url']})"
    )

def format_preview(deal: dict) -> str:
    return (
        f"📦 *Новая акция:* {deal['shop']}\n\n"
        + format_post(deal)
        + "\n\n_Публиковать в канал?_"
    )

def approval_kb(deal_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Опубликовать", callback_data=f"pub:{deal_id}"),
        InlineKeyboardButton(text="❌ Пропустить",   callback_data=f"skip:{deal_id}"),
    ]])

async def check_and_notify():
    log.info("Запускаю парсеры: Uzum + Korzinka + Texnomart...")
    seen = load_seen()
    deals = fetch_all(min_discount=MIN_DISCOUNT, limit_per_shop=LIMIT_PER_SHOP)

    by_shop: dict[str, int] = {}
    for d in deals:
        by_shop[d["shop"]] = by_shop.get(d["shop"], 0) + 1
    log.info(f"Итого: {len(deals)} | " + " | ".join(f"{k}: {v}" for k, v in by_shop.items()))

    new_count = 0
    for deal in deals:
        if deal["id"] in seen:
            continue
        seen.add(deal["id"])
        pending[deal["id"]] = deal
        try:
            if deal.get("image"):
                await bot.send_photo(
                    chat_id=ADMIN_ID, photo=deal["image"],
                    caption=format_preview(deal), parse_mode="Markdown",
                    reply_markup=approval_kb(deal["id"]),
                )
            else:
                await bot.send_message(
                    chat_id=ADMIN_ID, text=format_preview(deal),
                    parse_mode="Markdown", reply_markup=approval_kb(deal["id"]),
                )
            new_count += 1
            await asyncio.sleep(0.4)
        except Exception as e:
            log.error(f"Ошибка отправки [{deal['shop']}]: {e}")

    save_seen(seen)
    if new_count:
        await bot.send_message(ADMIN_ID, f"✅ Готово. Новых акций: *{new_count}*", parse_mode="Markdown")
    else:
        log.info("Новых акций не найдено.")

@dp.message(Command("start"))
async def cmd_start(msg: Message):
    if msg.from_user.id != ADMIN_ID:
        return
    await msg.answer(
        f"👋 *Бот запущен!*\n\n"
        f"🏪 Uzum.uz 🛍 | Korzinka.uz 🛒 | Texnomart.uz 📺\n"
        f"⏱ Проверка каждые *{CHECK_HOURS} ч.* | 💥 Мин. скидка: *{MIN_DISCOUNT}%*\n\n"
        f"/check — проверить сейчас\n/stats — статистика",
        parse_mode="Markdown",
    )

@dp.message(Command("check"))
async def cmd_check(msg: Message):
    if msg.from_user.id != ADMIN_ID:
        return
    await msg.answer("🔍 Парсю Uzum + Korzinka + Texnomart...")
    await check_and_notify()

@dp.message(Command("stats"))
async def cmd_stats(msg: Message):
    if msg.from_user.id != ADMIN_ID:
        return
    await msg.answer(
        f"📊 *Статистика*\n\nПросмотрено акций: *{len(load_seen())}*\nОжидают одобрения: *{len(pending)}*",
        parse_mode="Markdown",
    )

@dp.callback_query(F.data.startswith("pub:"))
async def on_publish(cb: CallbackQuery):
    deal_id = cb.data.split(":", 1)[1]
    deal = pending.pop(deal_id, None)
    if not deal:
        await cb.answer("Уже обработано.", show_alert=True)
        return
    try:
        if deal.get("image"):
            await bot.send_photo(chat_id=CHANNEL_ID, photo=deal["image"],
                                 caption=format_post(deal), parse_mode="Markdown")
        else:
            await bot.send_message(chat_id=CHANNEL_ID, text=format_post(deal), parse_mode="Markdown")
        await cb.message.edit_reply_markup(reply_markup=None)
        await cb.answer("✅ Опубликовано!")
    except Exception as e:
        await cb.answer(f"Ошибка: {e}", show_alert=True)

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
