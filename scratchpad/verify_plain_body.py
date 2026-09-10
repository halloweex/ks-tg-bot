"""The plain fallback body with a real Nova Poshta answer attached."""
import asyncio, json, sys
sys.path.insert(0, "/Users/vladislav/course-dbt/ks-tg-bot")
exec(open("/Users/vladislav/course-dbt/ks-tg-bot/scratchpad/verify_track.py").read().split("# --- scenario 1")[0])

from core.adapters.novaposhta.parse import TrackingStatus

class FakeNP:
    async def track_many(self, ttns, phone):
        return {ttns[0]: TrackingStatus(ttn=ttns[0], status="Отримано", status_code=9,
                                        city_recipient="Запоріжжя",
                                        warehouse_recipient="Відділення №20",
                                        scheduled_delivery="", actual_delivery="",
                                        recipient_date="2026-08-20 14:03:00",
                                        date_created="")}

rows = [order(i) for i in range(20, 8, -1)]          # 12 active
rows[0]["tracking_code"] = ""                        # the newest has no TTN at all
install(rows)
async def phone(chat_id): return "+380671112233"
mod.get_user_phone = phone
async def notyping(m): return None
mod.typing = notyping

btns = inblock_buttons(mod.rich_orders_blocks(rows, T, page=0))
data = [b[2] for b in btns if b[2].startswith("ord:track:10:")][0]
print("customer taps, inside order #200110's own section:", data)
asyncio.run(mod.track_parcel(FakeCallback(data), OrderAction.unpack(data), FakeNP(), T))
print("--- the plain body render() would fall back to ---")
print(CAPTURED["text"][:700])
