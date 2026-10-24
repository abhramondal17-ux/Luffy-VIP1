import asyncio
import logging
import os
import secrets
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

import aiohttp
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.memory import MemoryStorage

# -------------------- Configuration --------------------
BOT_TOKEN = os.getenv("8883211350:AAF_UY-76Hz80JIvQLBbBdBTyJWT1H2yl-g", "").strip()
ADMIN_IDS = {int(x.strip()) for x in os.getenv(" 8952331211", "").split(",") if x.strip().isdigit()}
SUPPORT_USERNAME = os.getenv("ENGINE_ENGINE123", "").strip().lstrip("@")
DB_PATH = os.getenv("DB_PATH", "jarvis_engine.db")

# FamGateway settings: verify parameter names and response schema against your
# current provider dashboard/API documentation before enabling live payments.
FAMGATEWAY_BASE_URL = os.getenv("FAMGATEWAY_BASE_URL", "https://famgateway.in").rstrip("/")
FAMGATEWAY_API_KEY = os.getenv("FAMGATEWAY_API_KEY", "").strip()
FAMGATEWAY_QR_PATH = os.getenv("FAMGATEWAY_QR_PATH", "/api/qr.php")
FAMGATEWAY_VERIFY_PATH = os.getenv("FAMGATEWAY_VERIFY_PATH", "/api/verify-order.php")

# Plans from the reference video. Change prices/stock in Admin tools as needed.
PLANS = {
    "1": {"label": "1 Day", "price": Decimal("120")},
    "3": {"label": "3 Days", "price": Decimal("220")},
    "7": {"label": "7 Days", "price": Decimal("320")},
    "15": {"label": "15 Days", "price": Decimal("550")},
    "30": {"label": "30 Days", "price": Decimal("900")},
}
CATEGORIES = ["Carrom Pool", "8 Ball Pool"]

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("jarvis-engine")

# -------------------- Database --------------------
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with db() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS users(
            user_id INTEGER PRIMARY KEY, username TEXT, full_name TEXT,
            balance TEXT NOT NULL DEFAULT '0', country TEXT DEFAULT 'India',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS keys_stock(
            id INTEGER PRIMARY KEY AUTOINCREMENT, category TEXT NOT NULL,
            plan_days TEXT NOT NULL, license_key TEXT NOT NULL UNIQUE,
            active INTEGER NOT NULL DEFAULT 1, added_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS purchases(
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
            category TEXT NOT NULL, plan_days TEXT NOT NULL, license_key TEXT NOT NULL,
            price TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS payments(
            order_id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, amount TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending', provider_payload TEXT,
            created_at TEXT NOT NULL, credited_at TEXT
        );
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """)
        con.execute("INSERT OR IGNORE INTO settings(key,value) VALUES('support_text','Contact support using the Support button.')")

def now():
    return datetime.now(timezone.utc).isoformat()

def money(value):
    return f"₹{Decimal(str(value)):.2f}"

def ensure_user(message: Message):
    with db() as con:
        con.execute("""INSERT INTO users(user_id,username,full_name,created_at)
        VALUES(?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET username=excluded.username,
        full_name=excluded.full_name""",
        (message.from_user.id, message.from_user.username or "",
         message.from_user.full_name or "", now()))

def get_user(uid):
    with db() as con:
        return con.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone()

def is_admin(uid):
    return uid in ADMIN_IDS

# -------------------- Menus --------------------
def main_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🛒 Buy 1 Day"), KeyboardButton(text="🛒 Buy 3 Days")],
            [KeyboardButton(text="🛒 Buy 7 Days"), KeyboardButton(text="🛒 Buy 15 Days")],
            [KeyboardButton(text="🛒 Buy 30 Days"), KeyboardButton(text="📦 Stock")],
            [KeyboardButton(text="💳 Add Funds"), KeyboardButton(text="💰 Balance")],
            [KeyboardButton(text="📁 Get Files"), KeyboardButton(text="🆘 Support")],
        ],
        resize_keyboard=True,
        input_field_placeholder="Choose an option below…"
    )

def back_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Main Menu", callback_data="main_menu")]
    ])

def category_keyboard(plan_days):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🎯 {cat}", callback_data=f"buy:{plan_days}:{i}")]
        for i, cat in enumerate(CATEGORIES)
    ] + [[InlineKeyboardButton(text="⬅️ Cancel", callback_data="main_menu")]])

class AddFunds(StatesGroup):
    amount = State()

class AdminKey(StatesGroup):
    category = State()
    days = State()
    key = State()

class AdminBalance(StatesGroup):
    user_id = State()
    amount = State()

class AdminSupport(StatesGroup):
    text = State()

# -------------------- Start / profile --------------------
async def start(message: Message):
    ensure_user(message)
    user = get_user(message.from_user.id)
    await message.answer(
        "✨ <b>WELCOME TO JARVIS ENGINE</b> ✨\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>User:</b> {message.from_user.full_name}\n"
        f"🆔 <b>ID:</b> <code>{message.from_user.id}</code>\n"
        f"🏅 <b>Membership:</b> Bronze\n"
        f"💰 <b>Wallet:</b> {money(user['balance'])}\n"
        f"🌍 <b>Country:</b> {user['country'] or 'India'}\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "👇 Select an option from the menu below.",
        reply_markup=main_menu()
    )

async def show_balance(message: Message):
    ensure_user(message)
    user = get_user(message.from_user.id)
    await message.answer(f"💰 <b>YOUR WALLET</b>\n━━━━━━━━━━━━\nAvailable balance: <b>{money(user['balance'])}</b>",
                         reply_markup=main_menu())

async def show_stock(message: Message):
    lines = ["📦 <b>LIVE STOCK</b>", "━━━━━━━━━━━━━━━━━━"]
    with db() as con:
        for cat in CATEGORIES:
            lines.append(f"\n🎮 <b>{cat}</b>")
            for days, plan in PLANS.items():
                count = con.execute("SELECT COUNT(*) n FROM keys_stock WHERE category=? AND plan_days=? AND active=1",
                                    (cat, days)).fetchone()["n"]
                lines.append(f"• {plan['label']}: <b>{count}</b> keys")
    await message.answer("\n".join(lines), reply_markup=main_menu())

async def show_support(message: Message):
    with db() as con:
        row = con.execute("SELECT value FROM settings WHERE key='support_text'").fetchone()
    txt = row["value"] if row else "Contact support for help."
    if SUPPORT_USERNAME:
        txt += f"\n\nTelegram: @{SUPPORT_USERNAME}"
    await message.answer("🆘 <b>JARVIS ENGINE SUPPORT</b>\n━━━━━━━━━━━━━━━━━━\n" + txt,
                         reply_markup=main_menu())

async def choose_plan(message: Message, days: str):
    plan = PLANS[days]
    await message.answer(
        f"🛒 <b>JARVIS ENGINE — {plan['label']}</b>\n"
        f"💵 Price: <b>{money(plan['price'])}</b>\n\n"
        "Choose your product category:",
        reply_markup=category_keyboard(days)
    )

async def buy_product(callback: CallbackQuery, days: str, category_index: int):
    uid = callback.from_user.id
    if category_index < 0 or category_index >= len(CATEGORIES):
        await callback.answer("Invalid category", show_alert=True); return
    category = CATEGORIES[category_index]
    plan = PLANS[days]
    with db() as con:
        user = con.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone()
        keyrow = con.execute("""SELECT * FROM keys_stock WHERE category=? AND plan_days=? AND active=1
                                ORDER BY id LIMIT 1""", (category, days)).fetchone()
        if not keyrow:
            await callback.answer("Out of stock. Please contact support.", show_alert=True); return
        balance = Decimal(user["balance"] if user else "0")
        if balance < plan["price"]:
            await callback.answer("Insufficient wallet balance. Add funds first.", show_alert=True); return
        new_balance = balance - plan["price"]
        con.execute("UPDATE users SET balance=? WHERE user_id=?", (str(new_balance), uid))
        con.execute("UPDATE keys_stock SET active=0 WHERE id=?", (keyrow["id"],))
        con.execute("""INSERT INTO purchases(user_id,category,plan_days,license_key,price,created_at)
                       VALUES(?,?,?,?,?,?)""", (uid, category, days, keyrow["license_key"], str(plan["price"]), now()))
    await callback.message.answer(
        "✅ <b>PURCHASE SUCCESSFUL</b>\n━━━━━━━━━━━━━━━━━━\n"
        f"🎮 Product: {category}\n⏳ Validity: {plan['label']}\n"
        f"🔑 Your key:\n<code>{keyrow['license_key']}</code>\n\n"
        f"💰 Remaining balance: {money(new_balance)}",
        reply_markup=main_menu()
    )
    await callback.answer("Purchase completed")

async def get_files(message: Message):
    with db() as con:
        rows = con.execute("SELECT * FROM purchases WHERE user_id=? ORDER BY id DESC LIMIT 10",
                           (message.from_user.id,)).fetchall()
    if not rows:
        await message.answer("📁 <b>MY FILES / PURCHASES</b>\n\nNo purchases found yet.", reply_markup=main_menu())
        return
    text = ["📁 <b>MY PURCHASES</b>", "━━━━━━━━━━━━━━━━━━"]
    for r in rows:
        text.append(f"\n🎮 {r['category']} — {PLANS.get(r['plan_days'], {}).get('label', r['plan_days'])}\n🔑 <code>{r['license_key']}</code>\n📅 {r['created_at'][:10]}")
    await message.answer("\n".join(text), reply_markup=main_menu())

# -------------------- FamGateway integration --------------------
async def fam_request(path, payload):
    if not FAMGATEWAY_API_KEY:
        raise RuntimeError("FAMGATEWAY_API_KEY is not configured")
    headers = {"Authorization": f"Bearer {FAMGATEWAY_API_KEY}", "X-API-KEY": FAMGATEWAY_API_KEY}
    timeout = aiohttp.ClientTimeout(total=25)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(FAMGATEWAY_BASE_URL + path, json=payload, headers=headers) as resp:
            raw = await resp.text()
            if resp.status >= 400:
                raise RuntimeError(f"Provider HTTP {resp.status}: {raw[:250]}")
            try:
                return await resp.json(content_type=None)
            except Exception:
                raise RuntimeError("Provider returned an unrecognized response. Check API docs.")

async def add_funds_start(message: Message, state: FSMContext):
    await state.set_state(AddFunds.amount)
    await message.answer("💳 <b>ADD FUNDS</b>\n\nSend the amount in INR (minimum ₹10). Example: <code>100</code>",
                         reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="❌ Cancel")]], resize_keyboard=True))

async def create_payment(message: Message, state: FSMContext):
    raw = (message.text or "").strip()
    if raw == "❌ Cancel":
        await state.clear(); await message.answer("Cancelled.", reply_markup=main_menu()); return
    try:
        amount = Decimal(raw).quantize(Decimal("0.01"))
    except InvalidOperation:
        await message.answer("Enter a valid amount, e.g. 100"); return
    if amount < Decimal("10") or amount > Decimal("50000"):
        await message.answer("Amount must be between ₹10 and ₹50,000."); return
    order_id = "JARVIS" + secrets.token_hex(5).upper()
    with db() as con:
        con.execute("INSERT INTO payments(order_id,user_id,amount,status,created_at) VALUES(?,?,?,?,?)",
                    (order_id, message.from_user.id, str(amount), "pending", now()))
    # Provider request fields may vary by account/API version. Confirm them in FamGateway docs.
    payload = {
        "api_key": FAMGATEWAY_API_KEY,
        "amount": str(amount),
        "order_id": order_id,
        "customer_name": message.from_user.full_name,
        "customer_email": f"{message.from_user.id}@telegram.user",
        "customer_mobile": "9999999999",
    }
    try:
        result = await fam_request(FAMGATEWAY_QR_PATH, payload)
    except Exception as exc:
        log.exception("FamGateway QR creation failed")
        with db() as con:
            con.execute("UPDATE payments SET status='error',provider_payload=? WHERE order_id=?",
                        (str(exc)[:500], order_id))
        await state.clear()
        await message.answer("⚠️ QR create नहीं हो पाया। FamGateway API settings/docs जाँचें और Support से संपर्क करें.",
                             reply_markup=main_menu())
        return
    # Parse common field names, but do not assume payment success.
    qr_url = result.get("qr_url") or result.get("qr_code_url") or result.get("qr") or result.get("image_url")
    payment_url = result.get("payment_url") or result.get("checkout_url") or result.get("url")
    provider_order = result.get("order_id") or result.get("txn_id") or order_id
    with db() as con:
        con.execute("UPDATE payments SET provider_payload=? WHERE order_id=?", (str(result)[:2000], order_id))
    buttons = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Verify Payment", callback_data=f"verify:{order_id}")],
        [InlineKeyboardButton(text="❌ Cancel Order", callback_data=f"cancelpay:{order_id}")]
    ])
    caption = (f"💳 <b>UPI PAYMENT</b>\n━━━━━━━━━━━━━━━━━━\n"
               f"🧾 Order ID: <code>{order_id}</code>\n"
               f"💵 Amount: <b>{money(amount)}</b>\n"
               f"⏱️ Please complete payment, then tap Verify Payment.\n"
               f"⚠️ Wallet is credited only after provider verification.")
    try:
        if qr_url:
            async with aiohttp.ClientSession() as session:
                async with session.get(qr_url, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                    if resp.status == 200:
                        image_bytes = await resp.read()
                        await message.answer_photo(BufferedInputFile(image_bytes, filename="payment_qr.png"),
                                                   caption=caption, reply_markup=buttons)
                    else:
                        await message.answer(caption + (f"\n\n🔗 {payment_url}" if payment_url else ""),
                                             reply_markup=buttons)
        else:
            await message.answer(caption + (f"\n\n🔗 {payment_url}" if payment_url else "") +
                                 "\n\nProvider response did not include a recognized QR URL. Check API response fields.",
                                 reply_markup=buttons)
    except Exception:
        await message.answer(caption + (f"\n\n🔗 {payment_url}" if payment_url else ""),
                             reply_markup=buttons)
    await state.clear()

async def verify_payment(callback: CallbackQuery, order_id: str):
    uid = callback.from_user.id
    with db() as con:
        payment = con.execute("SELECT * FROM payments WHERE order_id=?", (order_id,)).fetchone()
    if not payment or payment["user_id"] != uid:
        await callback.answer("Order not found", show_alert=True); return
    if payment["status"] == "paid":
        await callback.answer("Already credited", show_alert=True); return
    try:
        result = await fam_request(FAMGATEWAY_VERIFY_PATH, {"api_key": FAMGATEWAY_API_KEY, "order_id": order_id})
    except Exception:
        log.exception("Payment verification failed")
        await callback.answer("Verification service error. Try again later.", show_alert=True); return
    status = str(result.get("status", result.get("payment_status", ""))).lower()
    success = status in {"success", "successful", "paid", "completed", "captured"} or result.get("success") is True
    if not success:
        await callback.answer("Payment not confirmed yet.", show_alert=True); return
    # Atomic/idempotent credit: update only if still pending.
    with db() as con:
        row = con.execute("SELECT * FROM payments WHERE order_id=?", (order_id,)).fetchone()
        if row and row["status"] != "paid":
            con.execute("UPDATE payments SET status='paid',credited_at=?,provider_payload=? WHERE order_id=? AND status!='paid'",
                        (now(), str(result)[:2000], order_id))
            # Ensure the credit occurs only once.
            if con.execute("SELECT changes()").fetchone()[0] == 1:
                u = con.execute("SELECT balance FROM users WHERE user_id=?", (uid,)).fetchone()
                balance = Decimal(u["balance"] if u else "0") + Decimal(row["amount"])
                con.execute("UPDATE users SET balance=? WHERE user_id=?", (str(balance), uid))
            else:
                balance = Decimal(get_user(uid)["balance"])
        else:
            balance = Decimal(get_user(uid)["balance"])
    await callback.message.answer(f"✅ <b>PAYMENT VERIFIED</b>\n💰 Added: {money(payment['amount'])}\nWallet balance: <b>{money(balance)}</b>",
                                  reply_markup=main_menu())
    await callback.answer("Payment verified")

async def cancel_payment(callback: CallbackQuery, order_id: str):
    with db() as con:
        row = con.execute("SELECT * FROM payments WHERE order_id=?", (order_id,)).fetchone()
        if not row or row["user_id"] != callback.from_user.id:
            await callback.answer("Order not found", show_alert=True); return
        if row["status"] == "paid":
            await callback.answer("Already paid; cannot cancel.", show_alert=True); return
        con.execute("UPDATE payments SET status='cancelled' WHERE order_id=? AND status='pending'", (order_id,))
    await callback.message.answer("❌ Payment order cancelled.", reply_markup=main_menu())
    await callback.answer("Cancelled")

# -------------------- Admin tools --------------------
async def admin_help(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Admin access only."); return
    await message.answer(
        "🛡️ <b>JARVIS ADMIN</b>\n"
        "<code>/addkey</code> — manually add a key (guided)\n"
        "<code>/addbalance</code> — add wallet balance to a user\n"
        "<code>/set_support</code> — update support message\n"
        "<code>/stock</code> — view stock\n\n"
        "Keys are manually supplied by the admin; the bot never generates product keys."
    )

async def addkey_start(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Admin access only."); return
    await state.set_state(AdminKey.category)
    kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=c)] for c in CATEGORIES] +
                             [[KeyboardButton(text="❌ Cancel")]], resize_keyboard=True)
    await message.answer("Select category:", reply_markup=kb)

async def addkey_category(message: Message, state: FSMContext):
    if message.text == "❌ Cancel":
        await state.clear(); await message.answer("Cancelled.", reply_markup=main_menu()); return
    if message.text not in CATEGORIES:
        await message.answer("Choose a category from the keyboard."); return
    await state.update_data(category=message.text)
    await state.set_state(AdminKey.days)
    await message.answer("Send plan days: 1, 3, 7, 15, or 30")

async def addkey_days(message: Message, state: FSMContext):
    if message.text not in PLANS:
        await message.answer("Allowed values: 1, 3, 7, 15, 30"); return
    await state.update_data(days=message.text)
    await state.set_state(AdminKey.key)
    await message.answer("Now send the real license/key to add to stock.")

async def addkey_save(message: Message, state: FSMContext):
    key = (message.text or "").strip()
    if len(key) < 3 or len(key) > 500:
        await message.answer("Key length must be between 3 and 500 characters."); return
    data = await state.get_data()
    try:
        with db() as con:
            con.execute("INSERT INTO keys_stock(category,plan_days,license_key,active,added_at) VALUES(?,?,?,?,?)",
                        (data["category"], data["days"], key, 1, now()))
        await message.answer(f"✅ Key added to stock.\nCategory: {data['category']}\nPlan: {PLANS[data['days']]['label']}",
                             reply_markup=main_menu())
    except sqlite3.IntegrityError:
        await message.answer("⚠️ This key already exists in the database.", reply_markup=main_menu())
    await state.clear()

async def addbalance_start(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Admin access only."); return
    await state.set_state(AdminBalance.user_id)
    await message.answer("Send the Telegram numeric user ID.")

async def addbalance_uid(message: Message, state: FSMContext):
    if not (message.text or "").isdigit():
        await message.answer("Send a numeric Telegram user ID."); return
    await state.update_data(user_id=int(message.text))
    await state.set_state(AdminBalance.amount)
    await message.answer("Send amount to add, e.g. 100")

async def addbalance_amount(message: Message, state: FSMContext):
    try:
        amount = Decimal((message.text or "").strip())
        if amount <= 0: raise InvalidOperation
    except Exception:
        await message.answer("Enter a positive amount."); return
    data = await state.get_data()
    with db() as con:
        row = con.execute("SELECT balance FROM users WHERE user_id=?", (data["user_id"],)).fetchone()
        if not row:
            await message.answer("User not found. They must open the bot once first."); await state.clear(); return
        new = Decimal(row["balance"]) + amount
        con.execute("UPDATE users SET balance=? WHERE user_id=?", (str(new), data["user_id"]))
    await message.answer(f"✅ Added {money(amount)}. New balance: {money(new)}", reply_markup=main_menu())
    await state.clear()

async def set_support_start(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Admin access only."); return
    await state.set_state(AdminSupport.text)
    await message.answer("Send the new support text. This replaces the current support message.")

async def set_support_save(message: Message, state: FSMContext):
    with db() as con:
        con.execute("INSERT INTO settings(key,value) VALUES('support_text',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    ((message.text or "")[:3000],))
    await message.answer("✅ Support message updated.", reply_markup=main_menu())
    await state.clear()

async def main():
    if not BOT_TOKEN:
        raise SystemExit("Missing BOT_TOKEN. Copy .env.example to .env and set your token.")
    init_db()
    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp.message.register(start, CommandStart())
    dp.message.register(admin_help, Command("admin"))
    dp.message.register(addkey_start, Command("addkey"))
    dp.message.register(addbalance_start, Command("addbalance"))
    dp.message.register(set_support_start, Command("set_support"))

    dp.callback_query.register(lambda c: c.answer(), F.data == "noop")
    async def callback_router(c: CallbackQuery, state: FSMContext):
        data = c.data or ""
        if data == "main_menu":
            await c.message.answer("🏠 Main menu", reply_markup=main_menu()); await c.answer()
        elif data.startswith("buy:"):
            _, days, cat = data.split(":")
            await buy_product(c, days, int(cat))
        elif data.startswith("verify:"):
            await verify_payment(c, data.split(":", 1)[1])
        elif data.startswith("cancelpay:"):
            await cancel_payment(c, data.split(":", 1)[1])
    dp.callback_query.register(callback_router)

    dp.message.register(addkey_category, AdminKey.category)
    dp.message.register(addkey_days, AdminKey.days)
    dp.message.register(addkey_save, AdminKey.key)
    dp.message.register(addbalance_uid, AdminBalance.user_id)
    dp.message.register(addbalance_amount, AdminBalance.amount)
    dp.message.register(set_support_save, AdminSupport.text)
    dp.message.register(create_payment, AddFunds.amount)

    @dp.message(F.text == "💰 Balance")
    async def balance_handler(m: Message): await show_balance(m)
    @dp.message(F.text == "📦 Stock")
    async def stock_handler(m: Message): await show_stock(m)
    @dp.message(F.text == "🆘 Support")
    async def support_handler(m: Message): await show_support(m)
    @dp.message(F.text == "📁 Get Files")
    async def files_handler(m: Message): await get_files(m)
    @dp.message(F.text == "💳 Add Funds")
    async def funds_handler(m: Message, state: FSMContext): await add_funds_start(m, state)

    for d in PLANS:
        async def plan_handler(m: Message, days=d): await choose_plan(m, days)
        dp.message.register(plan_handler, F.text == f"🛒 Buy {PLANS[d]['label']}")

    @dp.message(F.text == "❌ Cancel")
    async def cancel_handler(m: Message, state: FSMContext):
        await state.clear(); await m.answer("Cancelled.", reply_markup=main_menu())

    log.info("Starting JARVIS ENGINE bot")
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
