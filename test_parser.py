from msgparser import parse_message, ParseError
cases = {
 "180 blinkit": (180.0,"Blinkit","Expense"),
 "₹1,500 irctc ticket": (1500.0,"Irctc Ticket","Expense"),
 "5000 salary income": (5000.0,"Salary","Income"),
 "+5000 salary": (5000.0,"Salary","Income"),
 "25000 own account t": (25000.0,"Own Account","Transfer"),
 "blinkit 180.50": (180.5,"Blinkit","Expense"),
 "66707 sbi card transfer": (66707.0,"Sbi Card","Transfer"),
 "50 income": (50.0,"Income","Expense"),
}
for t,exp in cases.items():
    got=parse_message(t); assert got==exp,(t,got,exp)
for bad in ["blinkit","hello world","0 x","180"]:
    try: parse_message(bad); raise SystemExit("should fail: "+bad)
    except ParseError: pass
print("all parser tests pass")
