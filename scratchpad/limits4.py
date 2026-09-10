"""The boundary: what the screen does with the orders it cannot fit."""
import json, sys
sys.path.insert(0, "/Users/vladislav/course-dbt/ks-tg-bot/scratchpad")
from limits1 import order, T, rich, rich_orders_blocks, wire_counts
from wire import wire_payload

def readout(blocks):
    p = wire_payload(blocks)
    sections, paras = 0, []
    for b in p["blocks"]:
        if b["type"] == "details":
            sections += 1
        elif b["type"] == "paragraph" and isinstance(b.get("text"), str):
            paras.append(b["text"])
    _p, typed, li = wire_counts(blocks)
    return sections, paras, rich.count_blocks(blocks), typed + li - 1

print("--- where the cap starts dropping orders (4 items each, the shop's average) ---")
for n in (50, 55, 57, 58, 60, 80, 200):
    rows = [order(i, 4) for i in range(1, n+1)]
    b = rich_orders_blocks(rows, T)
    s, paras, cb, real = readout(b)
    line = [x for x in paras if x.startswith("Показано")]
    print(f"  {n:>3} orders -> {s:>3} sections on screen, count_blocks={cb}, "
          f"real blocks on wire={real}, page line={line}")

print()
print("--- one order too big to fit by itself ---")
for items in (200, 400, 600):
    rows = [order(1, items)]
    b = rich_orders_blocks(rows, T)
    s, paras, cb, real = readout(b)
    print(f"  1 order of {items:>3} items -> sections={s}, count_blocks={cb}, "
          f"real={real}, paragraphs={paras[:2]}")

print()
print("--- the tail (cancelled orders) is weighed but never capped on its own ---")
for n_c in (100, 300, 400, 600):
    rows = ([order(i, 4) for i in range(1, 11)]
            + [order(1000+i, 1, cancelled=True) for i in range(n_c)])
    b = rich_orders_blocks(rows, T, cancelled=True)
    s, paras, cb, real = readout(b)
    print(f"  10 active + {n_c:>3} cancelled -> active sections={s}, "
          f"count_blocks={cb} (budget {rich.BLOCK_BUDGET}), real on wire={real}, "
          f"fits={rich.fits(b)}, page line={[x for x in paras if x.startswith('Показано')]}")
