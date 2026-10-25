"""Parse a chat message like '180 blinkit' into (amount, name, type). No dependencies."""
import re

TYPES = ("Expense", "Income", "Transfer")

ALIASES = {
    "expense": "Expense", "exp": "Expense", "e": "Expense", "spent": "Expense",
    "income": "Income", "inc": "Income", "i": "Income", "received": "Income", "credit": "Income",
    "transfer": "Transfer", "trf": "Transfer", "t": "Transfer", "self": "Transfer",
}

AMOUNT_RE = re.compile(r"^[+\-]?[₹]?\s*\d[\d,]*(\.\d+)?$")


class ParseError(ValueError):
    pass


def parse_message(text: str):
    """Return (amount: float, name: str, type: str).

    Accepted:  '180 blinkit'            -> Expense
               '5000 salary income'     -> Income   (type = last word)
               '+5000 salary'           -> Income   (leading + means income)
               '25000 own account t'    -> Transfer
               '₹1,500 irctc ticket'    -> Expense
    The amount may also come last: 'blinkit 180'.
    """
    words = text.strip().split()
    if len(words) < 2:
        raise ParseError("Send: <amount> <name> [type]  e.g.  180 blinkit")

    # amount = first or last word
    if AMOUNT_RE.match(words[0]):
        amount_word, rest = words[0], words[1:]
    elif AMOUNT_RE.match(words[-1]):
        amount_word, rest = words[-1], words[:-1]
    else:
        raise ParseError("I couldn't find an amount. Example: 180 blinkit")

    plus = amount_word.startswith("+")
    try:
        amount = abs(float(amount_word.replace("₹", "").replace(",", "").replace("+", "").strip()))
    except ValueError:
        raise ParseError("That amount doesn't look right.")
    if amount == 0:
        raise ParseError("Amount must be more than 0.")

    # type = last remaining word if it's a known alias (and something is left for the name)
    txn_type = "Income" if plus else "Expense"
    if len(rest) >= 2 and rest[-1].lower() in ALIASES:
        txn_type = ALIASES[rest[-1].lower()]
        rest = rest[:-1]

    name = " ".join(rest).strip().title()
    if not name:
        raise ParseError("Please include a name. Example: 180 blinkit")
    return round(amount, 2), name, txn_type
