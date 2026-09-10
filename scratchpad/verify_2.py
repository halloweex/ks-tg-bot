"""End-to-end: the slab toggle that expands «Скасовані», then the in-block tap.
Plus the plain-fallback body track_parcel hands to render()."""
import asyncio, json, sys
sys.path.insert(0, "/Users/vladislav/course-dbt/ks-tg-bot")
exec(open("/Users/vladislav/course-dbt/ks-tg-bot/scratchpad/verify_track.py").read().split("# --- scenario 1")[0])

rows = [order(i) for i in range(20, 8, -1)]
rows.append(order(1, status_group_id=mod.CANCELLED_STATUS_GROUP,
                  tracking_code="", status_name="Скасовано"))
install(rows)

print("=== 1. the customer taps the slab's «Скасовані (1)» ===")
slab = mod._orders_kb(rows, T)
toggle = [b for r in slab.inline_keyboard for b in r if "Скасовані" in b.text][0]
print("  slab button:", toggle.text, toggle.callback_data)
cd = OrderAction.unpack(toggle.callback_data)
asyncio.run(mod.show_order(FakeCallback(toggle.callback_data), cd, None, T))
expanded_blocks = CAPTURED["blocks"]
print("  cancelled digest now on screen:", cancelled_digest(expanded_blocks))

print("=== 2. she taps «Де посилка?» inside the newest order's section ===")
data = inblock_buttons(expanded_blocks)[0][2]
print("  in-block callback:", data)
asyncio.run(mod.track_parcel(FakeCallback(data), OrderAction.unpack(data), None, T))
print("  cancelled digest after the tap:", cancelled_digest(CAPTURED["blocks"]))

print()
print("=== 3. the plain body track_parcel hands render() for order id 10 (11th) ===")
btns = inblock_buttons(mod.rich_orders_blocks(rows, T, page=0))
data10 = [b[2] for b in btns if b[2].startswith("ord:track:10:")][0]
print("  in-block callback:", data10, " owning section:",
      [b[0] for b in btns if b[2] == data10][0])
asyncio.run(mod.track_parcel(FakeCallback(data10), OrderAction.unpack(data10), None, T))
print("  --- plain text (fallback body) ---")
print("\n".join(CAPTURED["text"].splitlines()[:12]))
print("  --- rich blocks: which section carries the parcel? ---")
for b in CAPTURED["blocks"]:
    d = b.model_dump(exclude_none=True, mode="json")
    if d["type"] != "details":
        continue
    txt = json.dumps(d, ensure_ascii=False)
    if "Отримано" in txt or "Відділення №20\"" in txt:
        pass
summary_with_parcel = []
for b in CAPTURED["blocks"]:
    d = b.model_dump(exclude_none=True, mode="json")
    if d["type"] == "details":
        inner = json.dumps(d["blocks"], ensure_ascii=False)
        s = json.dumps(d["summary"], ensure_ascii=False)
        if "Де посилка?" not in inner:
            summary_with_parcel.append((s[:60], "no button -> parcel shown here"))
print(" ", summary_with_parcel)
