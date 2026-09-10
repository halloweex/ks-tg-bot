import json, sys
sys.path.insert(0, '/Users/vladislav/course-dbt/ks-tg-bot')
from bot import rich
from bot.handlers.orders import rich_orders_blocks, _order_summary, _order_details
from core.i18n import customer_texts
from aiogram.enums import InputRichBlockType
from aiogram.types import InputRichMessage

T = customer_texts("uk")
BT = {m.value for m in InputRichBlockType}

def order(idx=1, nitems=4, **kw):
    row = {
        "id": idx, "order_name": f"#2001{idx}", "source": "keycrm",
        "status_name": "Прибув у відділення", "status_group_id": 4,
        "grand_total": 2800, "currency": "грн",
        "ordered_at": f"2026-08-{(idx%28)+1:02d}T10:00:00",
        "tracking_code": "20451496877635",
        "delivery_city": "Запоріжжя", "receive_point": "Відділення №20",
        "products_json": json.dumps(
            [{"name": f"LALARECIPE Hempseed Serum {i}", "qty": 1} for i in range(nitems)],
            ensure_ascii=False),
    }
    row.update(kw)
    return row

def walk(v, d=1):
    if isinstance(v, dict):
        yield v, d
        for x in v.values():
            yield from walk(x, d+1)
    elif isinstance(v, (list, tuple)):
        for x in v:
            yield from walk(x, d)

def wire(blocks):
    """What aiogram actually serialises for rich_message."""
    from aiogram.client.session.aiohttp import AiohttpSession
    from aiogram.methods import SendRichMessage
    s = AiohttpSession()
    m = SendRichMessage(chat_id=1, rich_message=InputRichMessage(blocks=list(blocks)))
    # aiogram builds the request; take the json payload for rich_message
    from aiogram import Bot
    bot = Bot(token="123456:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")
    form = s.build_form_data(bot=bot, method=m)
    for f in form._fields:
        if f[0]['name'] == 'rich_message':
            return json.loads(f[2])
    raise AssertionError([f[0]['name'] for f in form._fields])

def stats(blocks, label):
    payload = wire(blocks)
    nodes = [n for n,_ in walk(payload)]
    typed = [n for n in nodes if n.get("type") in BT]
    untyped = [n for n in nodes if "type" not in n]
    othertyped = [n for n in nodes if "type" in n and n["type"] not in BT]
    from collections import Counter
    print(f"--- {label}")
    print("  count_blocks()      :", rich.count_blocks(blocks))
    print("  dict nodes on wire  :", len(nodes))
    print("  typed-as-block      :", len(typed))
    print("  no 'type' key       :", len(untyped))
    print("  type not in enum    :", Counter(n['type'] for n in othertyped))
    print("  block type breakdown:", Counter(n['type'] for n in typed))
    return payload

stats([order()] and rich_orders_blocks([order(i, 4) for i in range(60)], T), "60 orders x 4 items (through rich_orders_blocks)")
stats(rich_orders_blocks([order(i, 30) for i in range(25)], T), "25 orders x 30 items")
