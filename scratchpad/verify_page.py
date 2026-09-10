"""What page=0 in the in-block button does on a paged rich screen."""
import asyncio, json, sys
sys.path.insert(0, "/Users/vladislav/course-dbt/ks-tg-bot")
exec(open("/Users/vladislav/course-dbt/ks-tg-bot/scratchpad/verify_track.py").read().split("# --- scenario 1")[0])

def summaries(blocks):
    out = []
    for b in blocks:
        d = b.model_dump(exclude_none=True, mode="json")
        if d["type"] == "details":
            out.append(json.dumps(d["summary"], ensure_ascii=False)[:34])
    return out

for n in (25, 100):
    rows = [order(i) for i in range(n, 0, -1)]
    install(rows)
    page1 = mod.rich_orders_blocks(rows, T, page=1)
    print(f"--- {n} active orders, screen at page=1 ---")
    print("  sections on page 1:", len(page1and := summaries(page1)), page1and[:2], "…")
    btn = inblock_buttons(page1)[0]
    print("  in-block button of the top order there:", btn[0][:30], btn[2])
    asyncio.run(mod.track_parcel(FakeCallback(btn[2]), OrderAction.unpack(btn[2]), None, T))
    after = summaries(CAPTURED["blocks"])
    print("  after the tap: %d sections, first=%s" % (len(after), after[0]))
    tapped_id = OrderAction.unpack(btn[2]).order_id
    # does the tapped order still appear, and does it carry the parcel?
    found = None
    for b in CAPTURED["blocks"]:
        d = b.model_dump(exclude_none=True, mode="json")
        if d["type"] == "details" and f"#2001{tapped_id} " in json.dumps(d["summary"], ensure_ascii=False):
            found = json.dumps(d["blocks"], ensure_ascii=False)
    print("  tapped order still on screen:", found is not None,
          "| parcel lines on it:", (found is not None and "Де посилка?" not in found))
    print("  page line:", [b.text for b in CAPTURED["blocks"]
                           if b.model_dump(exclude_none=True, mode='json')["type"] == "paragraph"][:1])
