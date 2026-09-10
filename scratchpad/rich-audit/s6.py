import sys, time
sys.path.insert(0,'/Users/vladislav/course-dbt/ks-tg-bot')
exec(open('/Users/vladislav/course-dbt/ks-tg-bot/scratchpad/rich-audit/s1.py').read().split('stats([order()]')[0])
import bot.rich as R

# instrument _dumped
calls = {'n':0, 'blocks':0}
orig = R._dumped
def spy(blocks):
    calls['n'] += 1; calls['blocks'] += len(blocks)
    return orig(blocks)
R._dumped = spy

for n, items in ((60,4),(25,30),(8,30),(10,3),(3,1)):
    rows = [order(i, items) for i in range(n)]
    calls['n']=0; calls['blocks']=0
    t0=time.perf_counter(); b = rich_orders_blocks(rows, T); t1=time.perf_counter()
    dumped_calls, dumped_blocks = calls['n'], calls['blocks']
    # cost of a single full build without the measuring at all
    print(f"{n}x{items}: build={1000*(t1-t0):.1f} ms  _dumped calls={dumped_calls} top-level blocks dumped={dumped_blocks}  sections={sum(1 for x in b if getattr(x,'type',None)=='details')}")

# how much of the build time is fits()?
R._dumped = orig
import cProfile, pstats, io
rows=[order(i,4) for i in range(60)]
pr=cProfile.Profile(); pr.enable(); rich_orders_blocks(rows,T); pr.disable()
s=io.StringIO(); pstats.Stats(pr,stream=s).sort_stats('cumulative').print_stats(12); print(s.getvalue()[:2000])
