import sys, json
sys.path.insert(0,'/Users/vladislav/course-dbt/ks-tg-bot')
from bot import rich
import aiogram.types as t
# inline rich-text objects that carry a type colliding with the block enum
for name in ('RichTextAnchor','RichTextMathematicalExpression'):
    cls = getattr(t, name, None)
    print(name, '->', cls.model_fields['type'].default if cls else 'MISSING')
a = t.RichTextAnchor(name='x') if hasattr(t,'RichTextAnchor') else None
print('anchor dump:', a.model_dump(exclude_none=True, mode='json'))
one = [rich.para(["before ", a, " after"])]
print('count_blocks(paragraph with anchor) =', rich.count_blocks(one))
try:
    m = t.RichTextMathematicalExpression(text='e=mc^2')
except Exception as e:
    m = t.RichTextMathematicalExpression(expression='e=mc^2')
print('math dump:', m.model_dump(exclude_none=True, mode='json'))
print('count_blocks(paragraph with math) =', rich.count_blocks([rich.para(["a", m])]))
# and bold/url for contrast
print('count_blocks(paragraph with bold) =', rich.count_blocks([rich.para(["a", t.RichTextBold(text='b')])]))
