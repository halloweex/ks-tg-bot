import sys
sys.path.insert(0,'/Users/vladislav/course-dbt/ks-tg-bot')
from bot import rich
from aiogram.types import RichTextBold, RichTextUrl
S = "абв" * 473 + "аб"          # ukrainian-ish, 2 bytes/char
print('len(S)=',len(S),'bytes=',len(S.encode()))
cases = {
 'para(str)':            [rich.para(S)],
 'para([str])':          [rich.para([S])],
 'heading(str)':         [rich.heading(S)],
 'heading([str])':       [rich.heading([S])],
 'details(summary=str)': [rich.details(S, [])],
 'details(summary=[str])':[rich.details([S], [])],
 'cell(str)':            [rich.table([[rich.cell(S)]])],
 'cell([str])':          [rich.table([[rich.cell([S])]])],
 'caption=str':          [rich.table([[rich.cell('x')]], caption=S)],
 'caption=[str]':        [rich.table([[rich.cell('x')]], caption=[S])],
 'button text':          [rich.buttons(rich.button(S, callback_data='c'))],
 'bullets([str])':       [rich.bullets([S])],
 'para([bold])':         [rich.para([RichTextBold(text=S)])],
 'para([str,bold])':     [rich.para([S, RichTextBold(text='x')])],
 'para(url)':            [rich.para([RichTextUrl(text=S, url='https://e.x')])],
}
for k,v in cases.items():
    print(f'{k:24s} text_bytes={rich.text_bytes(v)}')
