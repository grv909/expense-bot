"""Telegram -> Google Sheets expense bot.

Send:  <amount> <name> [type]      e.g.  180 blinkit   |   5000 salary income   |   25000 savings transfer
Date is added automatically (India time by default).
"""
import json
import logging
import os
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
load_dotenv()

import gspread
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from msgparser import ParseError, parse_message

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("expense-bot")

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
ALLOWED_ID = int(os.environ["ALLOWED_USER_ID"])          # only this Telegram user may use the bot
SHEET_ID = os.environ["GOOGLE_SHEET_ID"]                  # the long id in the sheet URL
TAB = os.environ.get("SHEET_TAB", "Transactions")
TZ = ZoneInfo(os.environ.get("TIMEZONE", "Asia/Kolkata"))
CREDS_JSON = os.environ["GOOGLE_CREDENTIALS_JSON"]        # full service-account JSON as one env var

_ws = None


def sheet():
    """Lazy-connect to the worksheet (reconnects if needed)."""
    global _ws
    if _ws is None:
        gc = gspread.service_account_from_dict(json.loads(CREDS_JSON))
        _ws = gc.open_by_key(SHEET_ID).worksheet(TAB)
    return _ws


def authorised(update: Update) -> bool:
    return update.effective_user is not None and update.effective_user.id == ALLOWED_ID


def rupees(x: float) -> str:
    return f"₹{x:,.2f}".replace(".00", "")


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not authorised(update):
        await update.message.reply_text(f"Not authorised. Your Telegram ID is {update.effective_user.id}")
        return
    await update.message.reply_text(
        "Send an expense as:  <amount> <name> [type]\n\n"
        "180 blinkit            → Expense\n"
        "5000 salary income     → Income\n"
        "25000 savings transfer → Transfer\n"
        "+5000 salary           → Income\n\n"
        "Types: expense (default), income, transfer. Short forms: e / i / t.\n\n"
        "/today – today's entries\n/month – this month's totals\n/undo – delete the last entry"
    )


async def add(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not authorised(update):
        return
    try:
        amount, name, ttype = parse_message(update.message.text)
    except ParseError as e:
        await update.message.reply_text(f"⚠️ {e}")
        return
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    try:
        sheet().append_row([today, name, amount, ttype], value_input_option="USER_ENTERED")
    except Exception:
        log.exception("sheet write failed")
        global _ws
        _ws = None  # force reconnect next time
        await update.message.reply_text("❌ Couldn't write to the sheet. Try again in a moment.")
        return
    await update.message.reply_text(f"✅ {ttype}: {rupees(amount)} – {name} ({today})")


def read_rows():
    rows = sheet().get_all_values()[1:]  # skip header
    out = []
    for r in rows:
        if len(r) >= 4 and r[0]:
            try:
                out.append((r[0], r[1], float(str(r[2]).replace(",", "")), r[3]))
            except ValueError:
                continue
    return out


async def today(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not authorised(update):
        return
    d = datetime.now(TZ).strftime("%Y-%m-%d")
    rows = [r for r in read_rows() if r[0] == d]
    if not rows:
        await update.message.reply_text("Nothing logged today.")
        return
    lines = [f"{n} – {rupees(a)} ({t})" for _, n, a, t in rows]
    spent = sum(a for _, _, a, t in rows if t == "Expense")
    await update.message.reply_text("Today:\n" + "\n".join(lines) + f"\n\nExpenses: {rupees(spent)}")


async def month(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not authorised(update):
        return
    prefix = datetime.now(TZ).strftime("%Y-%m")
    rows = [r for r in read_rows() if r[0].startswith(prefix)]
    if not rows:
        await update.message.reply_text("Nothing logged this month.")
        return
    totals = defaultdict(float)
    names = defaultdict(float)
    for _, n, a, t in rows:
        totals[t] += a
        if t == "Expense":
            names[n] += a
    msg = [f"{datetime.now(TZ):%B %Y}"]
    for t in ("Expense", "Income", "Transfer"):
        msg.append(f"{t}: {rupees(totals[t])}")
    msg.append(f"Net (income − expense): {rupees(totals['Income'] - totals['Expense'])}")
    if names:
        msg.append("\nTop expenses:")
        for n, a in sorted(names.items(), key=lambda x: -x[1])[:5]:
            msg.append(f"• {n}: {rupees(a)}")
    await update.message.reply_text("\n".join(msg))


async def undo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not authorised(update):
        return
    ws = sheet()
    values = ws.get_all_values()
    if len(values) <= 1:
        await update.message.reply_text("Nothing to undo.")
        return
    last = values[-1]
    ws.delete_rows(len(values))
    await update.message.reply_text(f"↩️ Removed: {last[1]} – {rupees(float(last[2]))} ({last[3]}, {last[0]})")


def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler(["start", "help"], start))
    app.add_handler(CommandHandler("today", today))
    app.add_handler(CommandHandler("month", month))
    app.add_handler(CommandHandler("undo", undo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, add))

    base_url = os.environ.get("WEBHOOK_URL") or os.environ.get("RENDER_EXTERNAL_URL")
    if base_url:  # webhook mode: works on hosts that sleep when idle (Render, Cloud Run, ...)
        port = int(os.environ.get("PORT", "8080"))
        secret = os.environ.get("WEBHOOK_SECRET", "")
        app.run_webhook(
            listen="0.0.0.0", port=port, url_path=TOKEN,
            webhook_url=f"{base_url.rstrip('/')}/{TOKEN}",
            secret_token=secret or None,
        )
    else:  # polling mode: for an always-on server or your own PC
        app.run_polling()


if __name__ == "__main__":
    main()
