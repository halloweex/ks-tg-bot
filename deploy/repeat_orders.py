"""Does the bot bring repeat orders? A matched comparison, run on the server.

    ssh root@<host> 'python3 -' < deploy/repeat_orders.py

Reads the bot's SQLite (read-only, inside `ks-tg-bot`) and the warehouse's
`silver.orders` (inside `ks-postgres`). Prints aggregates only: buyer ids never
leave the server. Method and the first results: docs/repeat-orders.md.

Treated: bot users linked to a KeyCRM buyer (`user_crm_buyers`, admins
excluded), indexed at their registration day R (Kyiv). Controls: retail buyers
never linked to the bot, indexed at the same R. Both are stratified at R by

  recent  ordered in [R-6, R]: registering usually follows an order, to track it
  hist    orders in [R-180, R-7], bucketed 0 / 1 / 2-3 / 4+
  old     had an order before R-180

and only people who had ordered by R are compared. Orders count as the
dashboard counts them: retail, not a return, an active source.

Outcome: did the person order in [R+1, R+W]. Each treated person is compared
with the mean of the controls in the same stratum on the same day ("gap"). The
same is measured 28 days before R, when nobody had the bot yet; the change in
the gap ("did") is what the bot plausibly added, beyond who chooses to join.
"""
import json
import random
import statistics
import subprocess
from datetime import date, timedelta

BOT = r'''
import json, os, sqlite3
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
kyiv = ZoneInfo("Europe/Kyiv")
admins = {int(x) for x in os.environ.get("ADMIN_USER_IDS", "").split(",") if x.strip()}
c = sqlite3.connect("file:/app/data/bot_data.db?mode=ro", uri=True)
rows = c.execute("SELECT l.chat_id, l.buyer_id, u.created_at FROM user_crm_buyers l "
                 "JOIN users u USING (chat_id)").fetchall()
out = []
for chat, buyer, created in rows:
    if chat in admins:
        continue
    utc = datetime.fromisoformat(created).replace(tzinfo=timezone.utc)
    out.append([int(buyer), utc.astimezone(kyiv).date().isoformat()])
print(json.dumps({"links": out, "admins_excluded": len(rows) - len(out)}))
'''

bot = json.loads(subprocess.run(
    ["docker", "exec", "-i", "ks-tg-bot", "python", "-"], input=BOT,
    capture_output=True, text=True, check=True).stdout)
links = bot["links"]

SQL = """
CREATE TEMP TABLE t(person int, buyer_id int, r date);
INSERT INTO t VALUES {values};
WITH o AS (
  SELECT buyer_id, order_date, grand_total FROM silver.orders
   WHERE sales_type = 'retail' AND NOT is_return AND is_active_source
     AND buyer_id IS NOT NULL
),
dates AS (SELECT DISTINCT r - {off} AS d FROM t),
feat AS (
  SELECT d.d, o.buyer_id,
         bool_or(o.order_date BETWEEN d.d - 6 AND d.d) AS recent,
         count(*) FILTER (WHERE o.order_date BETWEEN d.d - 180 AND d.d - 7) AS hist,
         bool_or(o.order_date < d.d - 180) AS old,
         count(*) FILTER (WHERE o.order_date BETWEEN d.d + 1 AND d.d + {w}) AS n_after,
         coalesce(sum(o.grand_total) FILTER (
             WHERE o.order_date BETWEEN d.d + 1 AND d.d + {w}), 0) AS rev_after
    FROM dates d JOIN o ON o.order_date <= d.d + {w}
   GROUP BY d.d, o.buyer_id
  HAVING bool_or(o.order_date <= d.d)
),
strat AS (
  SELECT d, buyer_id, recent, old, n_after, rev_after,
         CASE WHEN hist = 0 THEN 0 WHEN hist = 1 THEN 1 WHEN hist <= 3 THEN 2 ELSE 3 END AS hb
    FROM feat
),
ctrl AS (
  SELECT d, recent, hb, old, count(*) AS k, avg(n_after) AS mn,
         avg((n_after > 0)::int) AS many, avg(rev_after) AS mrev
    FROM strat WHERE buyer_id NOT IN (SELECT buyer_id FROM t)
   GROUP BY 1, 2, 3, 4
)
SELECT t.person, s.recent, s.hb, s.old, s.n_after, s.rev_after, c.k, c.mn, c.many, c.mrev
  FROM t JOIN strat s ON s.buyer_id = t.buyer_id AND s.d = t.r - {off}
  LEFT JOIN ctrl c ON (c.d, c.recent, c.hb, c.old) = (s.d, s.recent, s.hb, s.old);
"""

LAST_FULL_DAY = date.today() - timedelta(days=1)


def boot(xs: list) -> tuple:
    """95% bootstrap interval of the mean."""
    rnd = random.Random(7)
    means = sorted(statistics.fmean(rnd.choices(xs, k=len(xs))) for _ in range(4000))
    return round(means[100], 3), round(means[3899], 3)


def matched(w: int, off: int) -> tuple:
    """{person: row} for treated people with a matched stratum, and how many
    were eligible (index day + window inside the data)."""
    eligible = [(i, b, r) for i, (b, r) in enumerate(links)
                if date.fromisoformat(r) - timedelta(days=off) + timedelta(days=w)
                <= LAST_FULL_DAY]
    if not eligible:
        return {}, 0
    values = ",".join(f"({i},{b},'{r}')" for i, b, r in eligible)
    out = subprocess.run(
        ["docker", "exec", "-i", "ks-postgres", "psql", "-U", "postgres", "-d", "ks",
         "-At", "-F", "|", "-v", "ON_ERROR_STOP=1"],
        input=SQL.format(values=values, w=w, off=off), capture_output=True, text=True,
        check=True).stdout
    rows = [line.split("|") for line in out.splitlines() if line.count("|") == 9]
    return {int(r[0]): r for r in rows if r[6]}, len(eligible)


def ordered(row: list) -> float:
    return float(row[4] != "0")


def gap(row: list) -> float:
    """Ordered (0/1) minus the share of matched controls who ordered."""
    return ordered(row) - float(row[8])


print(json.dumps({"links": len(links), "admins_excluded": bot["admins_excluded"],
                  "last_full_day": LAST_FULL_DAY.isoformat()}))
for w in (7, 14, 28):
    post, eligible = matched(w, 0)
    pre, _ = matched(w, 28)
    both = sorted(set(post) & set(pre))
    result = {
        "window_days": w, "eligible": eligible, "matched": len(post),
        "bot_ordered": round(statistics.fmean(map(ordered, post.values())), 3) if post else None,
        "controls_ordered": round(statistics.fmean(float(r[8]) for r in post.values()), 3) if post else None,
        "gap": round(statistics.fmean(map(gap, post.values())), 3) if post else None,
        "gap_ci95": boot([gap(r) for r in post.values()]) if post else None,
        "in_both": len(both),
    }
    if len(both) >= 2:
        did = [gap(post[p]) - gap(pre[p]) for p in both]
        result.update({
            "gap_before": round(statistics.fmean(gap(pre[p]) for p in both), 3),
            "did": round(statistics.fmean(did), 3), "did_ci95": boot(did)})
    print(json.dumps(result))
