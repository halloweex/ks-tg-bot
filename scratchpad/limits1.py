"""count_blocks vs what actually goes on the wire, on real screen shapes."""
import json, sys
sys.path.insert(0, "/Users/vladislav/course-dbt/ks-tg-bot")
sys.path.insert(0, "/Users/vladislav/course-dbt/ks-tg-bot/scratchpad")
from wire import wire_payload, walk_json, real_block_objects
from bot import rich
from bot.handlers.orders import rich_orders_blocks
from core.i18n import customer_texts
from aiogram.enums import InputRichBlockType

T = customer_texts("uk")
ENUM = {m.value for m in InputRichBlockType}

NAMES = ["LALARECIPE Hempseed Serum", "COSRX Advanced Snail 96 Mucin Power Essence",
         "Beauty of Joseon Relief Sun Rice + Probiotics SPF50+",
         "Anua Heartleaf 77% Soothing Toner 250 мл"]

def order(idx, items=1, cancelled=False):
    return {
        "id": idx, "order_name": f"#2001{idx}", "source": "keycrm",
        "status_name": "Прибув у відділення",
        "status_group_id": 5 if cancelled else 4,
        "grand_total": 2800, "currency": "грн",
        "ordered_at": f"2026-{1+idx%12:02d}-{1+idx%28:02d}T10:00:00",
        "tracking_code": f"2045149687{idx:04d}",
        "delivery_city": "Запоріжжя", "receive_point": "Відділення №20",
        "products_json": json.dumps(
            [{"name": NAMES[i % len(NAMES)], "qty": 1 + i % 3} for i in range(items)],
            ensure_ascii=False),
    }

def wire_counts(blocks):
    p = wire_payload(blocks)
    typed = sum(1 for n, _d in walk_json(p) if n.get("type") in ENUM)
    listitems = sum(1 for n, _d in walk_json(p)
                    if "blocks" in n and "type" not in n)  # bare list items
    return p, typed, listitems

print(f"{'history':>26} | count_blocks | wire typed | +list items | real objs | fits()")
for n, items in [(5,1),(5,4),(25,1),(25,4),(60,4),(200,4),(5,30),(25,30),(60,30),(200,30)]:
    rows = [order(i, items) for i in range(1, n+1)]
    b = rich_orders_blocks(rows, T)
    p, typed, li = wire_counts(b)
    objs = len(real_block_objects(b))
    print(f"{n:>4} orders x {items:>2} items | {rich.count_blocks(b):>12} | {typed:>10} | "
          f"{typed+li:>11} | {objs:>9} | {rich.fits(b)}")
