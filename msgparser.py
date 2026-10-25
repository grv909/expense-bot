"""Parse '<amount> <name> <type>' messages. No dependencies.

    250 pizza food               -> (250.0, 'Pizza', 'Food')
    80 bus travel                -> (80.0,  'Bus',   'Travel')
    120 "cafe coffee day" food   -> (120.0, 'Cafe Coffee Day', 'Food')   (quotes keep a multi-word name)
    5000 salary income           -> (5000.0, 'Salary', 'Income')
    +5000 salary                 -> (5000.0, 'Salary', 'Income')         (no type + leading '+')
    180 blinkit                  -> (180.0, 'Blinkit', 'Other')          (no type -> 'Other')
Type is free text: first word after the name onward ('250 pizza eating out' -> type 'Eating Out').
"""
import re
import shlex

AMOUNT_RE = re.compile(r"^[+\-]?[₹]?\d[\d,]*(\.\d+)?$")
TYPE_ALIASES = {"i": "Income", "inc": "Income", "income": "Income",
                "t": "Transfer", "trf": "Transfer", "transfer": "Transfer"}
DEFAULT_TYPE = "Other"


class ParseError(ValueError):
    pass


def parse_message(text: str):
    try:
        words = shlex.split(text.strip().replace("’", "'"))
    except ValueError:               # unbalanced quote -> plain split
        words = text.strip().split()
    if len(words) < 2:
        raise ParseError("Send: <amount> <name> <type>   e.g.  250 pizza food")
    if not AMOUNT_RE.match(words[0]):
        raise ParseError("Start with the amount. Example: 250 pizza food")

    plus = words[0].startswith("+")
    amount = abs(float(words[0].replace("₹", "").replace(",", "").replace("+", "")))
    if amount == 0:
        raise ParseError("Amount must be more than 0.")

    name = words[1].strip().title()
    type_words = words[2:]
    if type_words:
        raw = " ".join(type_words).strip()
        txn_type = TYPE_ALIASES.get(raw.lower(), raw.title())
    else:
        txn_type = "Income" if plus else DEFAULT_TYPE
    return round(amount, 2), name, txn_type