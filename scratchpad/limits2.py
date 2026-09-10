"""What text_bytes counts and what it misses, measured against the payload."""
import json, sys
sys.path.insert(0, "/Users/vladislav/course-dbt/ks-tg-bot/scratchpad")
from limits1 import *          # order(), T, wire_counts, rich, rich_orders_blocks
from wire import wire_payload, walk_json

VISIBLE_KEYS_SKIP = {"callback_data", "url", "style", "align", "valign", "type"}

def true_text_bytes(payload):
    """Every string a reader would actually see, wherever it sits.

    Walks strings inside rich-text LISTS too (text=["a ", {bold}, " b"]), which
    is where most of this screen's Ukrainian lives.
    """
    total = 0
    def walk(node, key=None):
        nonlocal total
        if isinstance(node, str):
            if key not in VISIBLE_KEYS_SKIP:
                total += len(node.encode("utf-8"))
        elif isinstance(node, dict):
            for k, v in node.items():
                walk(v, k)
        elif isinstance(node, list):
            for v in node:
                walk(v, key)
    walk(payload["blocks"], "blocks")
    return total

def machine_bytes(payload):
    """What text_bytes counts that no reader ever sees."""
    total = 0
    for node, _d in walk_json(payload):
        for k, v in node.items():
            if isinstance(v, str) and k in VISIBLE_KEYS_SKIP:
                total += len(v.encode("utf-8"))
    return total

print(f"{'history':>22} | text_bytes | real visible | machine junk counted | ratio")
for n, items in [(5,4),(25,4),(60,4),(25,30),(60,30)]:
    rows = [order(i, items) for i in range(1, n+1)]
    b = rich_orders_blocks(rows, T)
    p = wire_payload(b)
    tb, real, junk = rich.text_bytes(b), true_text_bytes(p), machine_bytes(p)
    print(f"{n:>4} orders x {items:>2} items | {tb:>10} | {real:>12} | {junk:>20} | {real/tb:.2f}x")

# where exactly the loss is
print()
one = rich_orders_blocks([order(1, 2)], T)
p = wire_payload(one)
print("one order, payload:")
print(json.dumps(p, ensure_ascii=False, indent=1)[:1400])
print("text_bytes =", rich.text_bytes(one), " visible =", true_text_bytes(p))
