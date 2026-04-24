# InsiderEdge v17 — GitHub Actions

**Cost: $0/month | Coverage: 98.9% of free tier**

## V17 Changes (all data-derived from 1,346-trade path analysis)
- **Trail stop: 8%/6%/4% → 12%/9%/7%** — 66% of winners dipped past -7% before peak
- **Hold: 14 → 15 days** — median peak day=15, WR drops after
- **Pre-entry momentum: +5pts** when stock was up 5 days before insider bought (WR 79.4% vs 66.5%)

## Backtest Results (v16 vs v17, 401 trades ex-YELL)
| | WR | Med |
|---|---|---|
| V16 sim | 58.1% | +2.84% |
| **V17 sim** | **65.1%** | **+3.34%** |
| Delta | **+7.0pp** | **+0.50%** |

## Daily Schedule
| Time (ET) | What |
|---|---|
| 8:30 AM | Pre-market scan 1 |
| 9:15 AM | Pre-market scan 2 |
| 9:30AM–4PM | Every 5 min |
| 4:15, 4:30, 5:00, 6:00 PM | After-close |
| 8:00, 10:00 PM | Pre-bed + bedtime |

## Setup
1. Create private GitHub repo, push this folder
2. Settings → Secrets → Actions → add 5 secrets:
   `SEC_API_KEY`, `POLYGON_KEY`, `ALPACA_KEY`, `ALPACA_SECRET`, `DISCORD_URL`
3. Actions tab → enable workflows
