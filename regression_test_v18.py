#!/usr/bin/env python3
"""
InsiderEdge regression_test_v18.py — deploy gate for filter/scoring/kelly changes.
Imports the bot's OWN functions and runs canonical cases through them.
ANY FAIL = DO NOT DEPLOY.

Usage: python regression_test_v18.py bot_v18.py
"""
import sys, importlib.util

BOT = sys.argv[1] if len(sys.argv) > 1 else "bot_v18.py"
spec = importlib.util.spec_from_file_location("bot", BOT)
bot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bot)

P = F = 0
def check(name, cond, detail=""):
    global P, F
    if cond: P += 1; print(f"  ✅ {name}")
    else:    F += 1; print(f"  ❌ {name}  {detail}")

def filters(**kw):
    a = dict(ticker="TEST", title="CEO", is_10b5=False, cluster=False, cluster_size=1,
             score=65, r3m=-0.40, spy_r3m=0.05, routine=False, atr_pct=5.0,
             avg_vol_30d=50_000_000, value=500_000, h52=-40,
             days_to_earnings=-20, insider_name="SMITH JOHN")
    a.update(kw); return bot.apply_filters(**a)

print(f"=== REGRESSION GATE on {BOT} ===\n")
print("MUST QUEUE — real-money winners:")
# FCN 2026-05-13
s,c = bot.score_signal(2_070_000, 5.46, -23.5, -0.179, None, True, 3, None)
r = filters(cluster=True, cluster_size=3, score=s, r3m=-0.179, spy_r3m=None,
            atr_pct=5.46, value=2_070_000, h52=-23.5, days_to_earnings=-19)
check(f"FCN queues (score={s:.0f})", r is None and bot.kelly_size(s, True, 3) > 0, f"reason={r}")
# PAHC 2026-05-29
s,c = bot.score_signal(317_700, 5.65, -50.3, -0.453, 0.106, False, 1, -0.03)
r = filters(score=s, r3m=-0.453, spy_r3m=0.106, atr_pct=5.65, value=317_700,
            h52=-50.3, days_to_earnings=-22)
check(f"PAHC queues (score={s:.0f})", r is None and bot.kelly_size(s, False, 1) > 0, f"reason={r}")
# EYE 2026-06-05
s,c = bot.score_signal(776_000, 5.70, -46.2, -0.421, 0.099, False, 1, 0.02)
r = filters(score=s, r3m=-0.421, spy_r3m=0.099, atr_pct=5.70, value=776_000,
            h52=-46.2, days_to_earnings=-22)
check(f"EYE queues (score={s:.0f})", r is None and bot.kelly_size(s, False, 1) > 0, f"reason={r}")

print("\nMUST BLOCK — live losses & traps:")
check("GMEX: HRT Financial", filters(insider_name="HRT FINANCIAL LP") == "institutional_buyer")
check("Placement: 250x vol", filters(value=5_000_000, avg_vol_30d=20_000) == "private_placement")
check("Solo 10b5-1", filters(is_10b5=True) == "10b5_plan")
check("Solo score 50 (review tier ≠ queue)", filters(score=50) == "score_too_low")
check("52w -96%", filters(h52=-96) == "52w_too_far")
check("ATR 0.5%", filters(atr_pct=0.5) == "atr_too_low")

print("\nV18 PATCHES — fail here = patch not applied:")
r = filters(cluster=True, cluster_size=3, score=75, days_to_earnings=-60)
check("P2 stale cluster (earn 60d ago)", r == "stale_cluster", f"got={r}")
r = filters(cluster=False, score=75, days_to_earnings=-60)
check("P2 solos exempt from stale", r is None, f"got={r}")
s,c = bot.score_signal(500_000, 8.0, -50, -0.45, 0.05, False, 1, 0.05)
check("P1 pre5 cluster-only (solo gets 0)", c.get("pts_pre5", 0) == 0, f"pts_pre5={c.get('pts_pre5')}")
s,c = bot.score_signal(500_000, 8.0, -50, -0.45, 0.05, True, 3, 0.05)
check("P1 pre5 still works for clusters", c.get("pts_pre5", 0) == 5, f"pts_pre5={c.get('pts_pre5')}")
check("P5 score=100 hot trap", filters(score=100, r3m=0.10) == "score_90_100_hot",
      f"got={filters(score=100, r3m=0.10)}")

print(f"\n{'='*40}\n{P} passed, {F} failed — "
      + ("✅ SAFE TO DEPLOY" if F == 0 else "🛑 DO NOT DEPLOY"))
sys.exit(1 if F else 0)
