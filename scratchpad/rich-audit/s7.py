import sys, time
sys.path.insert(0,'/Users/vladislav/course-dbt/ks-tg-bot')
exec(open('/Users/vladislav/course-dbt/ks-tg-bot/scratchpad/rich-audit/s1.py').read().split('stats([order()]')[0])
import json as _j

def wirecount(b):
    p = wire(b); nodes=[n for n,_ in walk(p)]
    typed=[n for n in nodes if n.get('type') in BT]
    items=[n for n in nodes if 'type' not in n and set(n.keys())=={'blocks'}]
    return len(typed), len(items)

print(f"{'shape':>14} | {'count_blocks':>12} | {'+listitems':>10} | {'over 500?':>9} | {'build ms':>8}")
for n,items in ((22,3),(22,11),(30,4),(40,4),(60,4),(25,30),(8,30),(120,2)):
    rows=[order(i,items) for i in range(n)]
    t0=time.perf_counter(); b=rich_orders_blocks(rows,T); ms=1000*(time.perf_counter()-t0)
    tb, li = wirecount(b)
    print(f"{n}x{items:>3} | {rich.count_blocks(b):12d} | {tb+li:10d} | {str(tb+li>500):>9} | {ms:8.1f}")

for n,items in ((22,11),(8,30),(40,4)):
    b=rich_orders_blocks([order(i,items) for i in range(n)],T)
    tb,li=wirecount(b)
    print(f"{n}x{items}: fits()={rich.fits(b)}  count_blocks={rich.count_blocks(b)}  wire block objects={tb+li}  ceiling=500")
