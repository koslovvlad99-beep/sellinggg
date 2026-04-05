This build keeps the bot deposit, balance, withdrawal, and admin approval features.

Files:
- bot.py -> Telegram bot with deposit + withdrawal + admin commands
- webhook.py -> NOWPayments webhook that updates balance
- bot.db -> SQLite database

Mini App features are disabled for now.
You do not need index.html, app.js, styles.css, or miniapp_server.py to run the bot.

Edit first:
- bot.py: BOT_TOKEN, NOWPAY_API_KEY, ADMIN_ID
- webhook.py: IPN_SECRET, BOT_TOKEN

Run:
pip install -r requirements.txt

Terminal 1:
python3 webhook.py

Terminal 2:
python3 bot.py

Set NOWPayments IPN/webhook URL to:
https://your-domain.com/webhook

Notes:
- Deposit still uses NOWPayments invoice flow.
- Withdraw stays manual: admin approves with /approve ID and confirms payout with /send ID.
- Pending Withdrawals button is admin-only.
