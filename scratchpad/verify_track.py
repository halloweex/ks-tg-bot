"""Tap the in-block 'Де посилка?' button and see what track_parcel draws."""
import asyncio, json, sys
sys.path.insert(0, "/Users/vladislav/course-dbt/ks-tg-bot")

from bot.handlers import orders as mod
from bot.callbacks import OrderAction
from core.i18n import customer_texts

T = customer_texts("uk")


def order(idx, **kw):
    row = {
        "id": idx, "order_name": f"#2001{idx}", "source": "keycrm",
        "status_name": "Прибув у відділення", "status_group_id": 4,
        "grand_total": 2800, "currency": "грн",
        "ordered_at": f"2026-08-{idx:02d}T10:00:00",
        "tracking_code": f"204514968776{idx:02d}",
        "delivery_city": "Запоріжжя", "receive_point": "Відділення №20",
        "products_json": json.dumps([{"name": "LALARECIPE Serum", "qty": 1}],
                                    ensure_ascii=False),
    }
    row.update(kw)
    return row


def types(blocks):
    return [b.model_dump(exclude_none=True, mode="json")["type"] for b in blocks]


def cancelled_digest(blocks):
    return [b.text for b in blocks
            if b.model_dump(exclude_none=True, mode="json")["type"] == "paragraph"
            and isinstance(b.text, str) and b.text.startswith("❌")]


def inblock_buttons(blocks):
    """(summary-of-owning-section, callback_data) for every in-block button."""
    out = []
    for b in blocks:
        if b.model_dump(exclude_none=True, mode="json")["type"] != "details":
            continue
        summary = "".join(x if isinstance(x, str) else getattr(x, "text", "")
                          for x in (b.summary if isinstance(b.summary, list) else [b.summary]))
        for inner in b.blocks:
            if inner.model_dump(exclude_none=True, mode="json")["type"] == "buttons":
                for btn in inner.buttons:
                    out.append((summary.strip(), btn.text, btn.callback_data))
    return out


# --- fakes -------------------------------------------------------------------
class FakeMessage:
    def __init__(self): self.chat = type("C", (), {"id": 7})()
    async def edit_text(self, *a, **k): raise AssertionError("not reached")


class FakeCallback:
    def __init__(self, data):
        self.data = data
        self.message = FakeMessage()
        self.from_user = type("U", (), {"id": 7})()
    async def answer(self, *a, **k): pass


CAPTURED = {}


async def fake_render(callback, text, reply_markup=None, *, blocks=None):
    CAPTURED["text"] = text
    CAPTURED["kb"] = reply_markup
    CAPTURED["blocks"] = blocks
    return None


def install(rows):
    async def cached(chat_id): return rows
    async def phone(chat_id): return None
    mod.get_cached_orders = cached
    mod.get_user_phone = phone
    mod.render = fake_render
    mod.track = lambda *a, **k: None


# --- scenario 1: cancelled expanded, then a tap on an in-block track ---------
rows = [order(i) for i in range(20, 8, -1)]          # 12 active, newest first
rows.append(order(1, status_group_id=mod.CANCELLED_STATUS_GROUP,
                  tracking_code="", status_name="Скасовано"))
install(rows)

before = mod.rich_orders_blocks(rows, T, cancelled=True, page=0)
print("BEFORE (customer expanded «Скасовані»)")
print("  cancelled digest:", cancelled_digest(before))
btns = inblock_buttons(before)
print("  in-block buttons, first three:", btns[:3])
print("  in-block button count:", len(btns))

# the tap: parse the callback data exactly as aiogram would
data = btns[0][2]
cd = OrderAction.unpack(data)
print("  tapped:", data, "->", cd)
asyncio.run(mod.track_parcel(FakeCallback(data), cd, None, T))
after = CAPTURED["blocks"]
print("AFTER the tap")
print("  cancelled digest:", cancelled_digest(after))
print("  slab last row:", [b.text for row in CAPTURED["kb"].inline_keyboard for b in row][-3:])
