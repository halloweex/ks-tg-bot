"""Ground truth: what actually goes on the wire for a rich message."""
import json, sys
sys.path.insert(0, "/Users/vladislav/course-dbt/ks-tg-bot")
from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.methods import SendRichMessage
from aiogram.types import InputRichMessage
import aiogram.types as T

BOT = Bot(token="123456:AAaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", session=AiohttpSession())

def wire_payload(blocks):
    """The JSON aiogram would put in the request body for rich_message."""
    m = SendRichMessage(chat_id=1, rich_message=InputRichMessage(blocks=list(blocks)))
    form = BOT.session.build_form_data(BOT, m)   # aiogram's own serialiser
    # aiogram's FormData: fields are (options, headers, value)
    out = {}
    for opts, _headers, value in form._fields:
        out[opts["name"]] = value
    return json.loads(out["rich_message"])

def walk_json(node, depth=1):
    if isinstance(node, dict):
        yield node, depth
        for v in node.values():
            yield from walk_json(v, depth + 1)
    elif isinstance(node, (list, tuple)):
        for v in node:
            yield from walk_json(v, depth)

BLOCK_CLASSES = tuple(getattr(T, n) for n in dir(T) if n.startswith("InputRichBlock")
                      and n not in ("InputRichBlock", "InputRichBlockUnion"))

def walk_objs(obj):
    """Every pydantic model instance in the python tree, with its class name."""
    from pydantic import BaseModel
    if isinstance(obj, BaseModel):
        yield obj
        for name in type(obj).model_fields:
            yield from walk_objs(getattr(obj, name, None))
    elif isinstance(obj, (list, tuple)):
        for x in obj:
            yield from walk_objs(x)

def real_block_objects(blocks):
    return [o for o in walk_objs(list(blocks)) if isinstance(o, BLOCK_CLASSES)]
