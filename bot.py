"""Telegram -> Google Sheets expense bot.

One-liner:   <amount> <name> <type>     e.g.  250 pizza food
Buttons:     /add  (asks amount -> name -> tap a type)
Sheet columns: Date | Name | Amount | Type
"""
import json
import logging
import os
import threading
import time
import urllib.request
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import gspread
from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler, ContextTypes,
    ConversationHandler, MessageHandler, filters,
)

from msgparser import AMOUNT_RE, ParseError, parse_message

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("expense-bot")

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
ALLOWED_ID = int(os.environ["ALLOWED_USER_ID"])           # only this Telegram user may use the bot
SHEET_ID = os.environ["GOOGLE_SHEET_ID"]
TAB = os.environ.get("SHEET_TAB", "Transactions")
TZ = ZoneInfo(os.environ.get("TIMEZONE", "Asia/Kolkata"))
CREDS_FILE = os.environ.get("GOOGLE_CREDENTIALS_FILE")    # path to the service-account .json
CREDS_JSON = os.environ.get("GOOGLE_CREDENTIALS_JSON")    # or the JSON text itself
AWAKE_START = int(os.environ.get("AWAKE_START", "8"))     # self-ping window (hours, local time)
AWAKE_END = int(os.environ.get("AWAKE_END", "23"))

NOT_SPENDING = ("Income", "Transfer")                      # every other type counts as spending
DEFAULT_TYPES = ["Food", "Groceries", "Travel", "Bills", "Shopping", "Other", "Income", "Transfer"]
ASK_AMOUNT, ASK_NAME, ASK_TYPE = range(3)

_ws = None


# ---------------------------------------------------------------- sheet helpers
def sheet():
    """Lazy-connect to the worksheet."""
    global _ws
    if _ws is None:
        if CREDS_FILE:
            gc = gspread.service_account(filename=CREDS_FILE)
        elif CREDS_JSON:
            gc = gspread.service_account_from_dict(json.loads(CREDS_JSON))
        else:
            raise RuntimeError("Set GOOGLE_CREDENTIALS_FILE or GOOGLE_CREDENTIALS_JSON")
        _ws = gc.open_by_key(SHEET_ID).worksheet(TAB)
    return _ws


def reset_sheet():
    global _ws
    _ws = None


def norm_date(v):
    """Sheet date (serial number or text) -> 'YYYY-MM-DD'."""
    if isinstance(v, (int, float)):
        return (date(1899, 12, 30) + timedelta(days=int(v))).strftime("%Y-%m-%d")
    s = str(v).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d %b %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return s


def read_rows():
    """All data rows as (date 'YYYY-MM-DD', name, amount, type)."""
    values = sheet().get_all_values(value_render_option="UNFORMATTED_VALUE")[1:]
    out = []
    for r in values:
        if len(r) >= 4 and r[0] != "":
            try:
                amt = r[2] if isinstance(r[2], (int, float)) else float(str(r[2]).replace("₹", "").replace(",", ""))
                out.append((norm_date(r[0]), str(r[1]), float(amt), str(r[3])))
            except ValueError:
                continue
    return out


def write_row(name, amount, ttype):
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    sheet().append_row([today, name, amount, ttype], value_input_option="USER_ENTERED")
    return today


def rupees(x: float) -> str:
    return f"₹{x:,.2f}".replace(".00", "")


def is_spending(t: str) -> bool:
    return t not in NOT_SPENDING


def authorised(update: Update) -> bool:
    return update.effective_user is not None and update.effective_user.id == ALLOWED_ID


def frequent_types(limit=8):
    """Types you use most (from the sheet), padded with sensible defaults."""
    try:
        counts = Counter(t for _, _, _, t in read_rows() if t)
        types = [t for t, _ in counts.most_common(limit)]
    except Exception:
        log.exception("couldn't read types")
        types = []
    for d in DEFAULT_TYPES:
        if d not in types and len(types) < limit:
            types.append(d)
    return types[:limit]


# ---------------------------------------------------------------- help
HELP = (
    "💸 Expense bot\n\n"
    "Add an entry\n"
    "• One line:  <amount> <name> <type>\n"
    "    250 pizza food\n"
    "    80 bus travel\n"
    "    120 \"cafe coffee day\" food   (quotes for multi-word names)\n"
    "    5000 salary income   or   +5000 salary\n"
    "    180 blinkit          (no type → Other)\n"
    "• /add – guided entry: amount → name → tap a type\n"
    "   (shortcut: /add 250  or  /add 250 pizza)\n\n"
    "View\n"
    "• /today – today's entries and total\n"
    "• /week – this week (Mon–today) by type\n"
    "• /month – this month: spent, income, by type, top names\n"
    "• /last 5 – your last 5 entries (default 5)\n"
    "• /types – types you've used, with counts\n\n"
    "Fix\n"
    "• /undo – delete the last entry\n"
    "• /cancel – stop a guided /add\n\n"
    "Types 'income' and 'transfer' (or i / t) aren't counted as spending."
)


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not authorised(update):
        await update.message.reply_text(f"Not authorised. Your Telegram ID is {update.effective_user.id}")
        return
    await update.message.reply_text(HELP)


# ---------------------------------------------------------------- one-line entry
async def quick_add(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        amount, name, ttype = parse_message(update.message.text)
    except ParseError as e:
        await update.message.reply_text(f"⚠️ {e}\nTip: /add lets you enter it with buttons, /help lists everything.")
        return
    await save_and_reply(update.message, amount, name, ttype)


async def save_and_reply(message, amount, name, ttype):
    try:
        d = write_row(name, amount, ttype)
    except Exception:
        log.exception("sheet write failed")
        reset_sheet()
        await message.reply_text("❌ Couldn't write to the sheet. Try again in a moment.")
        return False
    await message.reply_text(f"✅ {ttype}: {rupees(amount)} – {name} ({d})")
    return True


# ---------------------------------------------------------------- guided /add
def type_keyboard():
    types = frequent_types()
    rows = [types[i:i + 3] for i in range(0, len(types), 3)]
    kb = [[InlineKeyboardButton(t, callback_data=f"t:{t}") for t in row] for row in rows]
    kb.append([InlineKeyboardButton("✖ Cancel", callback_data="cancel")])
    return InlineKeyboardMarkup(kb)


async def add_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    args = ctx.args or []
    if args and AMOUNT_RE.match(args[0]):
        try:
            ctx.user_data["amount"] = parse_message(f"{args[0]} x")[0]
        except ParseError as e:
            await update.message.reply_text(f"⚠️ {e}")
            return ConversationHandler.END
        if len(args) >= 2:                       # /add 250 pizza  -> straight to the type buttons
            ctx.user_data["name"] = args[1].title()
            await update.message.reply_text(
                f"{rupees(ctx.user_data['amount'])} – {ctx.user_data['name']}\nPick a type (or type your own):",
                reply_markup=type_keyboard())
            return ASK_TYPE
        await update.message.reply_text(f"{rupees(ctx.user_data['amount'])}. What's it for? (a name, e.g. pizza)")
        return ASK_NAME
    await update.message.reply_text("How much? (e.g. 250)   /cancel to stop")
    return ASK_AMOUNT


async def got_amount(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    try:                                          # whole one-liner typed here? just save it
        amount, name, ttype = parse_message(text)
        if len(text.split()) >= 3:
            await save_and_reply(update.message, amount, name, ttype)
            ctx.user_data.clear()
            return ConversationHandler.END
    except ParseError:
        pass
    if not AMOUNT_RE.match(text):
        await update.message.reply_text("Please send just the amount, like 250   (/cancel to stop)")
        return ASK_AMOUNT
    amount = float(text.replace("₹", "").replace(",", "").replace("+", "").replace("-", ""))
    if amount == 0:
        await update.message.reply_text("Amount must be more than 0.")
        return ASK_AMOUNT
    ctx.user_data["amount"] = round(amount, 2)
    await update.message.reply_text(f"{rupees(ctx.user_data['amount'])}. What's it for? (a name, e.g. pizza)")
    return ASK_NAME


async def got_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["name"] = update.message.text.strip().title()
    await update.message.reply_text(
        f"{rupees(ctx.user_data['amount'])} – {ctx.user_data['name']}\nPick a type (or type your own):",
        reply_markup=type_keyboard())
    return ASK_TYPE


async def got_type_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q.from_user.id != ALLOWED_ID:
        await q.answer()
        return ConversationHandler.END
    await q.answer()
    if q.data == "cancel":
        ctx.user_data.clear()
        await q.edit_message_text("Cancelled.")
        return ConversationHandler.END
    ttype = q.data[2:]
    amount, name = ctx.user_data.get("amount"), ctx.user_data.get("name")
    if amount is None or not name:
        await q.edit_message_text("That entry expired. Send /add to start again.")
        return ConversationHandler.END
    await q.edit_message_reply_markup(reply_markup=None)
    await save_and_reply(q.message, amount, name, ttype)
    ctx.user_data.clear()
    return ConversationHandler.END


async def got_type_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.strip()
    ttype = {"i": "Income", "inc": "Income", "t": "Transfer", "trf": "Transfer"}.get(raw.lower(), raw.title())
    amount, name = ctx.user_data.get("amount"), ctx.user_data.get("name")
    if amount is None or not name:
        await update.message.reply_text("That entry expired. Send /add to start again.")
        return ConversationHandler.END
    await save_and_reply(update.message, amount, name, ttype)
    ctx.user_data.clear()
    return ConversationHandler.END


async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END


# ---------------------------------------------------------------- views
async def today(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    d = datetime.now(TZ).strftime("%Y-%m-%d")
    rows = [r for r in read_rows() if r[0] == d]
    if not rows:
        await update.message.reply_text("Nothing logged today.")
        return
    lines = [f"{n} – {rupees(a)} ({t})" for _, n, a, t in rows]
    spent = sum(a for _, _, a, t in rows if is_spending(t))
    await update.message.reply_text("Today:\n" + "\n".join(lines) + f"\n\nSpent: {rupees(spent)}")


def summarise(rows, title):
    cats, names = defaultdict(float), defaultdict(float)
    income = transfer = 0.0
    for _, n, a, t in rows:
        if t == "Income":
            income += a
        elif t == "Transfer":
            transfer += a
        else:
            cats[t] += a
            names[n] += a
    spent = sum(cats.values())
    msg = [title, f"Spent: {rupees(spent)}", f"Income: {rupees(income)}",
           f"Transfers: {rupees(transfer)}", f"Net (income − spent): {rupees(income - spent)}"]
    if cats:
        msg.append("\nBy type:")
        msg += [f"• {c}: {rupees(a)}" for c, a in sorted(cats.items(), key=lambda x: -x[1])]
    return msg, names


async def month(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    prefix = datetime.now(TZ).strftime("%Y-%m")
    rows = [r for r in read_rows() if r[0].startswith(prefix)]
    if not rows:
        await update.message.reply_text("Nothing logged this month.")
        return
    msg, names = summarise(rows, f"{datetime.now(TZ):%B %Y}")
    if names:
        msg.append("\nTop spends:")
        msg += [f"• {n}: {rupees(a)}" for n, a in sorted(names.items(), key=lambda x: -x[1])[:5]]
    await update.message.reply_text("\n".join(msg))


async def week(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(TZ)
    monday = (now - timedelta(days=now.weekday())).strftime("%Y-%m-%d")
    rows = [r for r in read_rows() if monday <= r[0] <= now.strftime("%Y-%m-%d")]
    if not rows:
        await update.message.reply_text("Nothing logged this week.")
        return
    msg, _ = summarise(rows, f"This week (from {monday})")
    await update.message.reply_text("\n".join(msg))


async def last(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        n = max(1, min(int(ctx.args[0]), 30)) if ctx.args else 5
    except ValueError:
        n = 5
    rows = read_rows()[-n:]
    if not rows:
        await update.message.reply_text("No entries yet.")
        return
    await update.message.reply_text(
        f"Last {len(rows)}:\n" + "\n".join(f"{d}  {nm} – {rupees(a)} ({t})" for d, nm, a, t in reversed(rows)))


async def types(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    counts = Counter(t for _, _, _, t in read_rows() if t)
    if not counts:
        await update.message.reply_text("No types yet.")
        return
    await update.message.reply_text("Types used:\n" + "\n".join(f"• {t}: {c}" for t, c in counts.most_common(30)))


async def undo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ws = sheet()
    values = ws.get_all_values(value_render_option="UNFORMATTED_VALUE")
    if len(values) <= 1:
        await update.message.reply_text("Nothing to undo.")
        return
    lastrow = values[-1] + [""] * 4
    ws.delete_rows(len(values))
    await update.message.reply_text(f"↩️ Removed: {lastrow[1]} – {lastrow[2]} ({lastrow[3]}, {norm_date(lastrow[0])})")


async def on_error(update, ctx: ContextTypes.DEFAULT_TYPE):
    log.error("handler error", exc_info=ctx.error)
    reset_sheet()
    if isinstance(update, Update) and update.effective_message and update.effective_user \
            and update.effective_user.id == ALLOWED_ID:
        await update.effective_message.reply_text("❌ Something went wrong (sheet unreachable?). Try again.")


# ---------------------------------------------------------------- keep-awake (hosts that sleep)
def keep_awake(url, every=600):
    def loop():
        while True:
            time.sleep(every)
            if AWAKE_START <= datetime.now(TZ).hour < AWAKE_END:
                try:
                    urllib.request.urlopen(url, timeout=15)
                except Exception:
                    pass          # even a 404 counts as traffic
    threading.Thread(target=loop, daemon=True).start()


# ---------------------------------------------------------------- wiring
def build_app() -> Application:
    app = Application.builder().token(TOKEN).build()
    me = filters.User(user_id=ALLOWED_ID)

    app.add_handler(CommandHandler(["start", "help"], start))      # replies to everyone (shows their id)

    conv = ConversationHandler(
        entry_points=[CommandHandler("add", add_start, filters=me)],
        states={
            ASK_AMOUNT: [MessageHandler(me & filters.TEXT & ~filters.COMMAND, got_amount)],
            ASK_NAME: [MessageHandler(me & filters.TEXT & ~filters.COMMAND, got_name)],
            ASK_TYPE: [CallbackQueryHandler(got_type_button),
                       MessageHandler(me & filters.TEXT & ~filters.COMMAND, got_type_text)],
        },
        fallbacks=[CommandHandler("cancel", cancel, filters=me)],
        per_message=False,
    )
    app.add_handler(conv)
    for name, fn in (("today", today), ("week", week), ("month", month),
                     ("last", last), ("types", types), ("undo", undo)):
        app.add_handler(CommandHandler(name, fn, filters=me))
    app.add_handler(CommandHandler("cancel", cancel, filters=me))
    app.add_handler(MessageHandler(me & filters.TEXT & ~filters.COMMAND, quick_add))
    app.add_error_handler(on_error)
    return app


def main():
    app = build_app()
    base_url = os.environ.get("WEBHOOK_URL") or os.environ.get("RENDER_EXTERNAL_URL")
    if base_url:  # webhook mode: for hosts that sleep when idle (Render, ...)
        port = int(os.environ.get("PORT", "8080"))
        secret = os.environ.get("WEBHOOK_SECRET", "")
        keep_awake(base_url)
        app.run_webhook(
            listen="0.0.0.0", port=port, url_path=TOKEN,
            webhook_url=f"{base_url.rstrip('/')}/{TOKEN}",
            secret_token=secret or None,
        )
    else:         # polling mode: your PC or an always-on server
        app.run_polling()


if __name__ == "__main__":
    main()