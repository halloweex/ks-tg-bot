import asyncio, json, sys
sys.path.insert(0, "/Users/vladislav/course-dbt/ks-tg-bot")
exec(open("/Users/vladislav/course-dbt/ks-tg-bot/scratchpad/verify_track.py").read().split("# --- scenario 1")[0])

rows = [order(i) for i in range(100, 0, -1)]
install(rows)
page7 = mod.rich_orders_blocks(rows, T, page=7)   # orders 30..21
btn = inblock_buttons(page7)[0]
print("screen at page=7, top section:", btn[0][:34], "| button:", btn[2])
asyncio.run(mod.track_parcel(FakeCallback(btn[2]), OrderAction.unpack(btn[2]), None, T))
tid = OrderAction.unpack(btn[2]).order_id
found = any(f"#2001{tid} " in json.dumps(b.model_dump(exclude_none=True, mode="json")["summary"], ensure_ascii=False)
            for b in CAPTURED["blocks"]
            if b.model_dump(exclude_none=True, mode="json")["type"] == "details")
print("after the tap: tapped order #2001%d still on screen: %s" % (tid, found))
print("plain fallback card is:", CAPTURED["text"].splitlines()[3])
print("plain fallback tail:", CAPTURED["text"].splitlines()[10:12])
