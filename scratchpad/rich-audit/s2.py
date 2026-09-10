import json, sys
sys.path.insert(0, '/Users/vladislav/course-dbt/ks-tg-bot')
exec(open('/Users/vladislav/course-dbt/ks-tg-bot/scratchpad/rich-audit/s1.py').read().split('stats([order()]')[0])

# The module's own example: eight orders of thirty items each.
rows = [order(i, 30) for i in range(8)]
b = rich_orders_blocks(rows, T)
p = wire(b)
nodes = [n for n,_ in walk(p)]
typed = [n for n in nodes if n.get("type") in BT]
items = [n for n in nodes if "type" not in n and set(n.keys()) == {"blocks"}]
print("8 orders x 30 items, via rich_orders_blocks:")
print("  count_blocks      =", rich.count_blocks(b))
print("  typed blocks      =", len(typed))
print("  list items        =", len(items))
print("  typed + items     =", len(typed)+len(items))
print("  all dict nodes    =", len(nodes))
print("  json bytes        =", len(json.dumps(p, ensure_ascii=False).encode()))
print("  fits()            =", rich.fits(b))
print("  sections shown    =", sum(1 for x in b if getattr(x,'type',None)=='details'))

# Same story built raw, without the fits() cap, to see the uncapped screen.
raw = [rich.heading(T.MSG_ORDERS_TITLE, size=2)]
for r in rows:
    raw.append(rich.details(_order_summary(r,T), _order_details(r,T)))
p2 = wire(raw); nodes2=[n for n,_ in walk(p2)]
typed2=[n for n in nodes2 if n.get("type") in BT]
items2=[n for n in nodes2 if "type" not in n and set(n.keys())=={"blocks"}]
print("raw 8x30 (no tail, no cap): count_blocks=%d typed=%d items=%d sum=%d json=%d" % (
    rich.count_blocks(raw), len(typed2), len(items2), len(typed2)+len(items2),
    len(json.dumps(p2, ensure_ascii=False).encode())))
