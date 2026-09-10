"""When does render() actually put the plain body on screen?"""
import asyncio, sys
sys.path.insert(0, "/Users/vladislav/course-dbt/ks-tg-bot")
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import EditMessageText
from aiogram.types import Message
from bot import screen, rich

def bad(msg):
    return TelegramBadRequest(method=EditMessageText(chat_id=1, message_id=1, text="x"),
                              message=msg)

class Bot:
    def __init__(self, rich_ok): self.rich_ok = rich_ok; self.sent = []
    async def send_rich_message(self, **kw):
        if not self.rich_ok:
            raise bad("Bad Request: RICH_MESSAGE_BLOCKS_TOO_MANY")
        self.sent.append(("rich", kw)); return "rich message"
    async def send_message(self, chat_id, text, **kw):
        self.sent.append(("plain", text)); return "plain message"

class Msg:
    def __init__(self, bot, edit_fails):
        self.bot = bot; self.edit_fails = edit_fails
        self.chat = type("C", (), {"id": 1})(); self.message_id = 5
        self.rich_message = object()          # the anchor is rich
    async def edit_text(self, *a, **kw):
        if self.edit_fails: raise bad("Bad Request: message can't be edited")
        return "edited rich"
    async def answer(self, text, **kw):
        self.bot.sent.append(("answer", text)); return "new plain message"

class CB:
    def __init__(self, msg): self.message = msg; self.bot = msg.bot

# aiogram's isinstance(message, Message) check in render(): use a real class check bypass
import bot.screen as S
orig = S.isinstance if hasattr(S, "isinstance") else isinstance
S.isinstance = lambda o, c: True if c is Message else orig(o, c)

for edit_fails, rich_ok in ((False, True), (True, True), (True, False)):
    b = Bot(rich_ok); m = Msg(b, edit_fails)
    out = asyncio.run(screen.render(CB(m), "PLAIN BODY", None, blocks=[rich.para("x")]))
    print(f"edit_fails={edit_fails!s:5} send_rich_ok={rich_ok!s:5} -> result={out!r}, what went out: {[s[0] for s in b.sent] or ['edit only']}")
