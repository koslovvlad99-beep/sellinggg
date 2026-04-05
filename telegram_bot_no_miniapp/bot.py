import sqlite3
from pathlib import Path

import httpx
from telegram import KeyboardButton, ReplyKeyboardMarkup, Update, WebAppInfo
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

BOT_TOKEN = "8792976128:AAGn6y5aJ7xjVW3KLAgm682jSCAnQ_Em8VE"
NOWPAY_API_KEY = "94M55Q2-PJGMTPR-MDBZ7B4-PYF8F6H"
ADMIN_ID = 7888451284

DB_PATH = Path(__file__).with_name("bot.db")
NOWPAY_URL = "https://api.nowpayments.io/v1/invoice"

conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=20)
conn.execute("PRAGMA journal_mode=WAL;")
conn.execute("PRAGMA busy_timeout=30000;")
c = conn.cursor()

c.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, balance REAL DEFAULT 0)")
c.execute("CREATE TABLE IF NOT EXISTS payments (payment_id TEXT PRIMARY KEY)")
c.execute("""CREATE TABLE IF NOT EXISTS invoices (
    invoice_id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    amount REAL NOT NULL,
    pay_url TEXT,
    status TEXT NOT NULL DEFAULT 'pending'
)""")
c.execute("""CREATE TABLE IF NOT EXISTS withdrawals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    amount REAL NOT NULL,
    address TEXT,
    status TEXT NOT NULL DEFAULT 'pending'
)""")
conn.commit()

try:
    c.execute("ALTER TABLE withdrawals ADD COLUMN address TEXT")
    conn.commit()
except sqlite3.OperationalError:
    pass

def build_menu():
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("💰 Balance"), KeyboardButton("💸 Deposit")],
            [KeyboardButton("🏧 Withdraw"), KeyboardButton("🧾 Pending Withdrawals")],
            [KeyboardButton("🌐 Open App", web_app=WebAppInfo(url=MINI_APP_URL))],
            [KeyboardButton("ℹ️ Help")],
        ],
        resize_keyboard=True
    )

user_states = {}
withdraw_cache = {}

def ensure_user(uid):
    c.execute("INSERT OR IGNORE INTO users(user_id) VALUES (?)", (uid,))
    conn.commit()

def get_balance(uid):
    ensure_user(uid)
    c.execute("SELECT balance FROM users WHERE user_id=?", (uid,))
    row = c.fetchone()
    return float(row[0]) if row else 0.0

async def create_invoice(uid, amount):
    headers = {"x-api-key": NOWPAY_API_KEY, "Content-Type": "application/json"}
    payload = {
        "price_amount": amount,
        "price_currency": "usd",
        "order_id": str(uid),
        "order_description": f"Deposit for Telegram user {uid}"
    }

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(NOWPAY_URL, json=payload, headers=headers)

    data = response.json()
    if response.status_code >= 400 or "invoice_url" not in data:
        raise RuntimeError(str(data))

    invoice_id = str(data.get("id"))
    pay_url = data["invoice_url"]

    c.execute(
        "INSERT OR REPLACE INTO invoices(invoice_id, user_id, amount, pay_url, status) VALUES (?, ?, ?, ?, 'pending')",
        (invoice_id, uid, amount, pay_url)
    )
    conn.commit()
    return pay_url

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    ensure_user(uid)
    await update.message.reply_text(
        f"Welcome.\nBalance: ${get_balance(uid):.2f}",
        reply_markup=build_menu()
    )

async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    c.execute("SELECT COUNT(*), COALESCE(SUM(balance), 0) FROM users")
    users, total = c.fetchone()
    c.execute("SELECT id, user_id, amount, COALESCE(address,''), status FROM withdrawals WHERE status IN ('pending','approved') ORDER BY id DESC LIMIT 20")
    rows = c.fetchall()

    text = f"Users: {users}\nTotal balance: ${float(total):.2f}\n\nOpen withdrawals:\n"
    if rows:
        text += "\n".join([f"#{wid} | user {uid} | ${amt:.2f} | {addr or 'no address'} | {status}" for wid, uid, amt, addr, status in rows])
    else:
        text += "None"
    text += "\n\nApprove: /approve ID\nReject: /reject ID\nAfter manual payout: /send ID"
    await update.message.reply_text(text)

async def approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    if not context.args:
        return await update.message.reply_text("Usage: /approve ID")
    wid = int(context.args[0])
    c.execute("SELECT user_id, amount, address, status FROM withdrawals WHERE id=?", (wid,))
    row = c.fetchone()
    if not row:
        return await update.message.reply_text("Withdrawal not found")

    uid, amount, address, status = row
    if status != "pending":
        return await update.message.reply_text("Withdrawal already processed")
    if get_balance(uid) < amount:
        return await update.message.reply_text("User balance too low")

    c.execute("UPDATE withdrawals SET status='approved' WHERE id=?", (wid,))
    conn.commit()
    await update.message.reply_text(f"Approved withdrawal #{wid}. After payout use /send {wid}")
    try:
        await context.bot.send_message(uid, f"✅ Withdrawal #{wid} approved.\nAmount: ${amount:.2f}\nAddress: {address}")
    except Exception:
        pass

async def send_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    if not context.args:
        return await update.message.reply_text("Usage: /send ID")
    wid = int(context.args[0])
    c.execute("SELECT user_id, amount, address, status FROM withdrawals WHERE id=?", (wid,))
    row = c.fetchone()
    if not row:
        return await update.message.reply_text("Withdrawal not found")

    uid, amount, address, status = row
    if status != "approved":
        return await update.message.reply_text("Withdrawal must be approved first")
    if get_balance(uid) < amount:
        return await update.message.reply_text("User balance too low to finalize")

    c.execute("UPDATE users SET balance = balance - ? WHERE user_id=?", (amount, uid))
    c.execute("UPDATE withdrawals SET status='sent' WHERE id=?", (wid,))
    conn.commit()
    await update.message.reply_text(f"Marked withdrawal #{wid} as sent.")
    try:
        await context.bot.send_message(uid, f"💸 Withdrawal #{wid} marked as sent.\nAmount: ${amount:.2f}\nAddress: {address}")
    except Exception:
        pass

async def reject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    if not context.args:
        return await update.message.reply_text("Usage: /reject ID")
    wid = int(context.args[0])
    c.execute("SELECT user_id, amount, status FROM withdrawals WHERE id=?", (wid,))
    row = c.fetchone()
    if not row:
        return await update.message.reply_text("Withdrawal not found")
    uid, amount, status = row
    if status not in ("pending", "approved"):
        return await update.message.reply_text("Withdrawal already processed")

    c.execute("UPDATE withdrawals SET status='rejected' WHERE id=?", (wid,))
    conn.commit()
    await update.message.reply_text(f"Rejected withdrawal #{wid}")
    try:
        await context.bot.send_message(uid, f"❌ Withdrawal #{wid} for ${amount:.2f} was rejected.")
    except Exception:
        pass

async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    uid = update.effective_user.id
    ensure_user(uid)
    text = (update.message.text or "").strip()

    if text == "💰 Balance":
        return await update.message.reply_text(f"Balance: ${get_balance(uid):.2f}")

    if text == "💸 Deposit":
        user_states[uid] = "deposit"
        return await update.message.reply_text("Enter amount:")

    if text == "🏧 Withdraw":
        user_states[uid] = "withdraw_amount"
        return await update.message.reply_text("Enter withdrawal amount:")

    if text == "🧾 Pending Withdrawals":
        if uid != ADMIN_ID:
            return await update.message.reply_text("Admin only.")
        c.execute("SELECT id, user_id, amount, COALESCE(address,''), status FROM withdrawals WHERE status IN ('pending','approved') ORDER BY id DESC LIMIT 20")
        rows = c.fetchall()
        if not rows:
            return await update.message.reply_text("No open withdrawals.")
        msg = "Open withdrawals:\n" + "\n".join([f"#{wid} | user {u} | ${a:.2f} | {addr or 'no address'} | {st}" for wid, u, a, addr, st in rows])
        msg += "\n\nApprove: /approve ID\nReject: /reject ID\nAfter manual payout: /send ID"
        return await update.message.reply_text(msg)

    if text == "🌐 Open App":
        return await update.message.reply_text("Use the Open App button to launch the Mini App.")

    if text == "ℹ️ Help":
        return await update.message.reply_text("Deposit creates invoice. Withdraw asks amount then address. Admin approves. After manual payout admin uses /send ID.")

    if user_states.get(uid) == "deposit":
        try:
            amount = float(text)
            if amount <= 0:
                raise ValueError
        except ValueError:
            return await update.message.reply_text("Invalid amount")
        try:
            pay_url = await create_invoice(uid, amount)
        except Exception as err:
            user_states.pop(uid, None)
            return await update.message.reply_text(f"Could not create invoice: {err}")
        user_states.pop(uid, None)
        return await update.message.reply_text(f"Pay here:\n{pay_url}")

    if user_states.get(uid) == "withdraw_amount":
        try:
            amount = float(text)
            if amount <= 0:
                raise ValueError
        except ValueError:
            return await update.message.reply_text("Invalid amount")
        if get_balance(uid) < amount:
            user_states.pop(uid, None)
            return await update.message.reply_text("Not enough balance.")
        withdraw_cache[uid] = amount
        user_states[uid] = "withdraw_address"
        return await update.message.reply_text("Send payout address:")

    if user_states.get(uid) == "withdraw_address":
        address = text
        amount = withdraw_cache.get(uid)
        if amount is None:
            user_states.pop(uid, None)
            return await update.message.reply_text("Start again.")
        c.execute("INSERT INTO withdrawals(user_id, amount, address, status) VALUES (?, ?, ?, 'pending')", (uid, amount, address))
        conn.commit()
        wid = c.lastrowid
        user_states.pop(uid, None)
        withdraw_cache.pop(uid, None)
        await update.message.reply_text(f"Withdrawal request #{wid} submitted.")
        try:
            await context.bot.send_message(ADMIN_ID, f"New withdrawal request\nID: {wid}\nUser: {uid}\nAmount: ${amount:.2f}\nAddress: {address}")
        except Exception:
            pass
        return

    await update.message.reply_text("Use the menu buttons.")

app = ApplicationBuilder().token(BOT_TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("admin", admin))
app.add_handler(CommandHandler("approve", approve))
app.add_handler(CommandHandler("reject", reject))
app.add_handler(CommandHandler("send", send_done))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, menu))

print("Bot starting...")
app.run_polling(drop_pending_updates=True)
