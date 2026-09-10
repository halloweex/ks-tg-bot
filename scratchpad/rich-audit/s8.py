import sys, asyncio, time, json, types
sys.path.insert(0,'/Users/vladislav/course-dbt/ks-tg-bot')
exec(open('/Users/vladislav/course-dbt/ks-tg-bot/scratchpad/rich-audit/s1.py').read().split('stats([order()]')[0])
import bot.handlers.orders as O
import bot.screen as S

rows = [order(i, 4) for i in range(60)]

calls = {'rich': 0, 'dumped': 0, 'ms': 0.0}
real_rob = O.rich_orders_blocks
def spy_rob(*a, **k):
    calls['rich'] += 1
    t0=time.perf_counter(); r = real_rob(*a,**k); calls['ms'] += 1000*(time.perf_counter()-t0)
    return r
O.rich_orders_blocks = spy_rob
O.get_cached_orders = lambda cid: _done(rows)
O.track = lambda *a, **k: None
O.follow_up_parcel = lambda *a, **k: None
async def _done(v): return v
O.get_cached_orders = lambda cid: _done(rows)

seen = {}
async def fake_render(callback, text, reply_markup=None, *, blocks=None):
    seen['blocks_arg_built'] = blocks is not None
    # replicate the real gate: anchor is a PLAIN message (customer)
    anchor_is_rich = getattr(callback.message, 'rich_message', None) is not None
    seen['anchor_is_rich'] = anchor_is_rich
    seen['blocks_sent'] = anchor_is_rich and blocks is not None
    return callback.message
O.render = fake_render

class M:
    rich_message = None                       # a plain screen: the customer's
    chat = types.SimpleNamespace(id=999)
    message_id = 5
class U: id = 999                             # NOT in admin_ids
class CB:
    message = M(); from_user = U()
    async def answer(self, *a, **k): return None

from bot.handlers.orders import OrderAction
cd = OrderAction(action="show", order_id=1, page=0, state="")

t0=time.perf_counter()
asyncio.run(O.show_order(CB(), cd, None, T))
total=1000*(time.perf_counter()-t0)

print("show_order for a NON-admin, plain anchor (60 orders x 4 items):")
print("  rich_orders_blocks called   :", calls['rich'], "time in it =", round(calls['ms'],1), "ms")
print("  blocks passed to render     :", seen.get('blocks_arg_built'))
print("  anchor was rich             :", seen.get('anchor_is_rich'))
print("  blocks actually go on wire  :", seen.get('blocks_sent'))
print("  whole handler               :", round(total,1), "ms")
