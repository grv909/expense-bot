# Telegram → Google Sheets expense bot

Send a message, a row is added to your sheet: **Date (auto) | Name | Amount | Type**.

    180 blinkit              -> Expense (default)
    5000 salary income       -> Income
    25000 savings transfer   -> Transfer
    +5000 salary             -> Income
Types: expense / income / transfer (short: e / i / t). Commands: /today /month /undo

## 1. Google Sheet
1. Upload `expense_tracker_starter.xlsx` to Google Drive → open with Google Sheets → File → Save as Google Sheets.
2. Keep the tab named **Transactions** (columns A–D). Copy the sheet ID from the URL: docs.google.com/spreadsheets/d/**<ID>**/edit

## 2. Google service account (free)
1. console.cloud.google.com → create a project → enable **Google Sheets API** and **Google Drive API**.
2. IAM & Admin → Service Accounts → create one → Keys → Add key → JSON. Download it.
3. Open the sheet → Share → paste the service account's email (client_email in the JSON) → Editor.

## 3. Telegram bot
1. In Telegram, message **@BotFather** → /newbot → copy the token.
2. Your numeric user id: message **@userinfobot** (or send /start to your bot once deployed; it prints your id).

## 4. Deploy (Render example)
1. Put this folder in a GitHub repo. Render → New → Web Service → connect repo → Runtime: Docker → Free plan.
2. Environment variables (see `.env.example`): TELEGRAM_BOT_TOKEN, ALLOWED_USER_ID, GOOGLE_SHEET_ID, GOOGLE_CREDENTIALS_JSON (paste the JSON), WEBHOOK_SECRET (any random text).
   Render supplies RENDER_EXTERNAL_URL itself, so webhook mode turns on automatically.
3. Deploy, then message your bot. Free instances sleep when idle; the first message after a quiet spell may be slow.

Always-on server / your own PC instead: set the env vars, leave WEBHOOK_URL unset, run
`pip install -r requirements.txt && python bot.py` (polling mode).

## Notes
- The bot only answers ALLOWED_USER_ID. Never commit the token or the JSON key.
- Starter sheet: Paytm card-bill payments and your own-account move are typed **Transfer**, so Expense totals show real spending (₹40,931 for 25 Sep–24 Oct). Zomato and ₹200–300 grocery payments are named **Blinkit** (₹3,304.58).
- Test the parser offline: `python test_parser.py`
