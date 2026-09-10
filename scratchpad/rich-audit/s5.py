import sys, json
sys.path.insert(0,'/Users/vladislav/course-dbt/ks-tg-bot')
exec(open('/Users/vladislav/course-dbt/ks-tg-bot/scratchpad/rich-audit/s1.py').read().split('stats([order()]')[0])

def all_string_bytes(node):
    """Every string anywhere in the wire payload, keys excluded."""
    tot = 0
    if isinstance(node, dict):
        for v in node.values(): tot += all_string_bytes(v)
    elif isinstance(node, (list, tuple)):
        for v in node: tot += all_string_bytes(v)
    elif isinstance(node, str):
        tot += len(node.encode())
    return tot

for n, items in ((60,4),(25,30),(8,30),(3,1)):
    b = rich_orders_blocks([order(i, items) for i in range(n)], T)
    p = wire(b)
    real = all_string_bytes(p)
    seen = rich.text_bytes(b)
    print(f"{n} orders x {items} items: text_bytes={seen}  real string bytes on wire={real}  missed={real-seen} ({100*(real-seen)/real:.0f}%)")

# minimal reproducible unit: one order's summary
row = order(1,1)
from bot.handlers.orders import _order_summary
summ = _order_summary(row, T)
print()
print("summary pieces:", [s if isinstance(s,str) else type(s).__name__ for s in summ])
d = [rich.details(summ, [])]
print("text_bytes(one folded order summary) =", rich.text_bytes(d),
      " real =", all_string_bytes(wire(d)))
