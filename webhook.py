from flask import Flask, request, jsonify
import sqlite3
import json
import hmac
import hashlib
from pathlib import Path

import requests

IPN_SECRET = "jKZc6xKYCMK9ieS+d8t+CpwoKnvdda44"
BOT_TOKEN = "8792976128:AAGn6y5aJ7xjVW3KLAgm682jSCAnQ_Em8VE"

DB_PATH = Path(__file__).with_name("bot.db")

app = Flask(__name__)

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
conn.commit()

def verify_nowpayments_signature(raw_body, received_sig):
    try:
        data = json.loads(raw_body.decode("utf-8"))
        sorted_body = json.dumps(data, separators=(",", ":"), sort_keys=True)
        expected_sig = hmac.new(IPN_SECRET.encode("utf-8"), sorted_body.encode("utf-8"), hashlib.sha512).hexdigest()
        return hmac.compare_digest(expected_sig, received_sig or "")
    except Exception:
        return False

@app.route("/webhook", methods=["POST"])
def webhook():
    raw_body = request.get_data()
    received_sig = request.headers.get("x-nowpayments-sig", "")
    if not verify_nowpayments_signature(raw_body, received_sig):
        return jsonify({"error": "invalid_signature"}), 403

    data = request.get_json(silent=True) or {}
    if data.get("payment_status") != "finished":
        return jsonify({"status": "ignored"})

    uid = int(data["order_id"])
    amt = float(data["price_amount"])
    pid = str(data["payment_id"])
    invoice_id = str(data.get("invoice_id") or "")

    c.execute("SELECT 1 FROM payments WHERE payment_id=?", (pid,))
    if c.fetchone():
        return jsonify({"status": "duplicate"})

    c.execute("INSERT INTO payments(payment_id) VALUES (?)", (pid,))
    c.execute("INSERT OR IGNORE INTO users(user_id) VALUES (?)", (uid,))
    c.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (amt, uid))
    if invoice_id:
        c.execute("UPDATE invoices SET status='paid' WHERE invoice_id=?", (invoice_id,))
    conn.commit()

    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": uid, "text": f"✅ Deposit received: ${amt:.2f}\nBalance updated."},
            timeout=15
        )
    except Exception:
        pass

    return jsonify({"status": "ok"})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
