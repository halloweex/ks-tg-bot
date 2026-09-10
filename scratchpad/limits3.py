"""text_bytes: which strings it can see at all."""
import sys, json
sys.path.insert(0, "/Users/vladislav/course-dbt/ks-tg-bot")
sys.path.insert(0, "/Users/vladislav/course-dbt/ks-tg-bot/scratchpad")
from bot import rich
from aiogram.types import RichTextBold, RichTextUrl

LONG = "Твоє замовлення вже у відділенні Нової Пошти, забери його протягом трьох діб. " * 20
print("visible bytes of LONG:", len(LONG.encode()))

cases = {
    "para(str)                ": rich.para(LONG),
    "para([str])  <- rich text": rich.para([LONG]),
    "para([str, bold])        ": rich.para([LONG, RichTextBold(text="!")]),
    "heading([str])           ": rich.heading([LONG]),
    "details(summary=str)     ": rich.details(LONG, [rich.para("x")]),
    "details(summary=[str])   ": rich.details([LONG], [rich.para("x")]),
    "bullets([str])           ": rich.bullets([LONG]),
    "button label             ": rich.buttons(rich.button(LONG[:60])),
    "table cell(str)          ": rich.table([[rich.cell(LONG)]]),
    "table cell([str])        ": rich.table([[rich.cell([LONG])]]),
    "table caption=str        ": rich.table([[rich.cell('x')]], caption=LONG),
    "table caption=[str]      ": rich.table([[rich.cell('x')]], caption=[LONG]),
}
for name, block in cases.items():
    print(f"  {name} text_bytes = {rich.text_bytes([block]):>6}")

print()
print("the test that is supposed to pin this:")
print("  rich.text_bytes([rich.para('абв')]) =", rich.text_bytes([rich.para("абв")]), "(> 3 passes)")
print("  rich.text_bytes([rich.para(['абв'])]) =", rich.text_bytes([rich.para(["абв"])]))
