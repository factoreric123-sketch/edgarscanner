#!/usr/bin/env python3
"""
InsiderEdge Live Bot — v17

All v14 changes carried forward:

- Solo floor: 44→56
- Dead zone upper: -10%→-20%
- Kelly recalibrated on 1346-trade DC dataset
- cluster_hot_stock filter REMOVED (WR=85% after blacklist)
- deep_mid_solo filter REMOVED (floor 56 handles it)
- ATR floor: <1% blocked (0 wins in full dataset)
- Dynamic regime: mild stress (SPY r3m -3% to 0%) raises cluster floor to 56
- Blacklist expanded to 25 tickers
- Discord: rich signal cards for EVERY filing — traded and filtered

v15 fix:
- Added `timezone` to datetime imports (fixes: datetime.datetime has no attribute 'timezone')
"""

import requests, json, os, time
from datetime import datetime, timedelta, timezone
from collections import defaultdict

# ── CREDENTIALS ───────────────────────────────────────────────────────────────

# Credentials — read from environment variables (GitHub Secrets) with hardcoded fallback
# In GitHub Actions: set these as repo secrets
# Locally / PythonAnywhere: can still hardcode below or use .env
import os as _os
SEC_API_KEY   = _os.getenv("SEC_API_KEY")
POLYGON_KEY   = _os.getenv("POLYGON_KEY")
POLYGON_BASE  = "https://api.polygon.io"
ALPACA_KEY    = _os.getenv("ALPACA_KEY")
ALPACA_SECRET = _os.getenv("ALPACA_SECRET")
ALPACA_BASE   = "https://paper-api.alpaca.markets"
DISCORD_URL   = _os.getenv("DISCORD_URL")

# ── V15 CONFIG ────────────────────────────────────────────────────────────────

MAX_HOLD_DAYS             = 15
SOLO_MIN_SCORE            = 56     # v14: floor 56=67.6% WR n=102 hk=29.2%
CLUSTER_MIN_SCORE         = 36
R3M_SKIP_ZONE_LO          = -0.30
R3M_SKIP_ZONE_HI          = -0.20  # v14: narrowed — r3m -10 to -5 = 69% WR, freed
SCORE_90_100_MAX_R3M      = 0.0
HEALTHCARE_MIN_CLUSTER    = 3
HEALTHCARE_SECTORS        = {"Healthcare","Biotechnology","Biopharmaceuticals","Pharmaceuticals"}
SPY_WEAK_REGIME_THRESHOLD = -0.05
HEALTH_FILTER_BYPASS_SCORE= 70
MAX_CLUSTER_SIZE          = 5      # v16: cs=6 WR=45.5% (board grants) — cap at cs=5 WR=86.4%
MAX_INSIDER_BUYS_90D      = 3
ATR_MIN_PCT               = 1.0    # v14: ATR<1% = 0 wins in 1346 trades

# V15 regime constants
SPY_MILD_STRESS_LO        = -0.03  # SPY r3m -3% to 0% = mild stress
SPY_MILD_STRESS_HI        =  0.00
CLUSTER_STRESS_FLOOR      = 56     # raise cluster floor in mild stress

# Trail stop
TRAIL_INITIAL    = 0.12
TRAIL_TIER1_TRIG = 0.10
TRAIL_TIER1_STOP = 0.09
TRAIL_TIER2_TRIG = 0.20
TRAIL_TIER2_STOP = 0.07

STATE_FILE = "bot_v15_state.json"

# ── V15 BLACKLIST (25 tickers) ────────────────────────────────────────────────

TICKER_BLACKLIST = {
    "CMPO","BOLD","FRPT","INTZ","SLDB","CAMP","FLNC","IMNM","RPHM","AFCG",
    "MSTR","AKTS","HTGC","KKR","RRGB","EMN",
    "PATK","BDSX","OXM","PRGO","PODD","VANI",
    "LRMR","DMAC","NKTX",
}

# ── HELPERS ───────────────────────────────────────────────────────────────────

def sf(v, d=0.0):
    try: return float(v)
    except: return d

def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)

# ── STATE ─────────────────────────────────────────────────────────────────────

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f: return json.load(f)
        except: pass
    return {"positions": {}, "seen_accessions": [], "routine_history": {}}

def save_state(state):
    state["seen_accessions"] = state["seen_accessions"][-3000:]
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, default=str)

# ── DISCORD ───────────────────────────────────────────────────────────────────

def discord_send(title, body, color=0x5865F2):
    try:
        requests.post(DISCORD_URL,
            json={"embeds": [{"title": title[:256], "description": body[:4096], "color": color}]},
            timeout=10)
        time.sleep(0.5)
    except Exception as e:
        log(f"Discord error: {e}")

def _regime_label(spy_r3m):
    if spy_r3m is None:  return "❓ unknown"
    if spy_r3m < -0.03:  return "🔴 Deep Selloff"
    if spy_r3m < 0.00:   return "🟡 Mild Stress"
    return "🟢 Normal"

def _fmt_vol(v):
    if v is None: return "N/A"
    if v >= 1_000_000: return f"${v/1_000_000:.1f}M"
    if v >= 1_000:     return f"${v/1_000:.0f}K"
    return f"${v:,.0f}"

def _score_bar(pts, max_pts, label):
    filled = max(0, min(5, round(pts / max_pts * 5))) if max_pts else 0
    bar = "█" * filled + "░" * (5 - filled)
    return f"`{label:<8}` {bar}  {pts:+.0f}pts"

def discord_signal(
    ticker, filed_date, insider_name, title,
    cluster, cluster_size, total_value,
    score, score_components,
    r3m, atr_daily, atr_monthly, h52, spy_r3m,
    sector, health_ok,
    is_10b5, routine,
    avg_vol_30d, current_price, price_chg_1d,
    filter_reason, kelly,
    traded, existing_position=False, queued=False
):
    # ── Status + color ────────────────────────────────────────
    if traded:
        emoji = "🚀"; color = 0x2ECC71
        status = "TRADE TAKEN"
    elif queued:
        emoji = "⏳"; color = 0xF39C12
        status = "QUEUED — executes at market open"
    elif existing_position:
        emoji = "📌"; color = 0x3498DB
        status = "ALREADY IN POSITION"
    elif filter_reason:
        emoji = "🔍"; color = 0x95A5A6
        status = "FILTERED"
    else:
        emoji = "⚠️"; color = 0xE67E22
        status = "SKIPPED"

    # ── String formatting ─────────────────────────────────────
    cl_str   = f"Cluster  cs={cluster_size}" if cluster else "Solo"
    r3m_s    = f"{r3m*100:+.1f}%" if r3m is not None else "N/A"
    h52_s    = f"{h52:+.1f}%" if h52 is not None else "N/A"
    spy_s    = f"{spy_r3m*100:+.1f}%" if spy_r3m is not None else "N/A"
    regime   = _regime_label(spy_r3m)
    price_s  = f"${current_price:.2f}" if current_price else "N/A"
    chg_s    = f" ({price_chg_1d:+.1f}% today)" if price_chg_1d is not None else ""
    atr_d_s  = f"{atr_daily:.2f}%" if atr_daily is not None else "N/A"
    atr_m_s  = f"{atr_monthly:.2f}%" if atr_monthly is not None else "N/A"
    vol_s    = _fmt_vol(avg_vol_30d)
    trending = "✅ Yes" if (r3m is not None and r3m > 0) else "❌ No"
    multi    = f"✅ Yes  cs={cluster_size}" if cluster else "❌ No"
    repeat_s = "⚠️ Yes" if routine else "No"
    plan_s   = "⚠️ Yes (10b5-1)" if is_10b5 else "No"
    health_s = "✅ OK" if health_ok else "❌ Distressed"

    # ── Score breakdown ───────────────────────────────────────
    comp_map = [
        ("ATR",      "pts_atr",     20),
        ("52wHigh",  "pts_52w",     20),
        ("Selloff",  "pts_selloff", 25),
        ("Cluster",  "pts_cluster", 25),
        ("Value",    "pts_value",   18),
        ("SPY",      "pts_spy",     10),
        ("Pre5",     "pts_pre5",     5),
    ]
    score_lines = []
    for label, key, max_v in comp_map:
        pts = float(score_components.get(key, 0) or 0)
        score_lines.append(_score_bar(pts, max_v, label))
    score_block = "\n".join(score_lines)

    # ── Reason ────────────────────────────────────────────────
    reason_map = {
        "ticker_blacklisted":     "🚫 **Blacklisted** — confirmed chronic loser across N≥5 trades",
        "see_remarks":            "❓ **SEE REMARKS** — unparseable filing, no actionable signal",
        "atr_too_low":            f"📉 **ATR too low** — {atr_d_s} < 1.0% (0 wins in 1,346-trade dataset)",
        "private_placement":       "🏦 **Private placement** — value > 60× daily vol (not open market buy)",
        "10b5_plan":              "📋 **10b5-1 plan** — pre-scheduled, zero informational content",
        "cluster_too_large":      f"👥 **Cluster too large** — cs={cluster_size} > 5 (board grant pattern, WR=45.5%)",
        "routine_buyer":          "🔄 **Routine buyer** — same insider >3x in 90 days on this ticker",
        "score_too_low":          f"📊 **Score too low** — {score:.0f} < floor {SOLO_MIN_SCORE if not cluster else CLUSTER_MIN_SCORE}",
        "score_too_low_stress":   f"📊 **Score too low (stress regime)** — {score:.0f} < stress floor 56 (SPY r3m {spy_s})",
        "r3m_dead_zone":          f"⚠️ **Dead zone** — r3m {r3m_s} between -30% and -20% (52% WR historically)",
        "score_90_100_hot":       f"🔥 **Score 90-100 + trending** — r3m {r3m_s} ≥ 0 (ownership stacking trap)",
        "solo_weak_market":       f"📉 **Weak market** — solo signal + SPY r3m {spy_s} < -5%",
        "fmp_unavailable":        "⚙️ **FMP unavailable** — whitelist financialmodelingprep.com on PythonAnywhere",
    }
    if traded:
        reason_line = f"✅ **TRADE TAKEN** — score {score:.0f} clears all filters | Kelly: **{kelly:.0%}**"
    elif queued:
        reason_line = f"⏳ **QUEUED** — score {score:.0f} clears all filters | Kelly: **{kelly:.0%}** | Will execute at next market open"
    elif existing_position:
        reason_line = f"📌 **Already holding** {ticker} — signal noted, no new entry"
    elif filter_reason:
        reason_line = reason_map.get(filter_reason, f"❌ {filter_reason}")
    else:
        reason_line = "⚠️ Unknown skip reason"

    # ── Body ─────────────────────────────────────────────────
    SEP = "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    title_line = (f"**Title:** {title}\n"
                  if title and "SEE REMARKS" not in (title or "").upper() else "")
    kelly_line = f"\n**Kelly Position:** {kelly:.0%}" if traded else ""
    body = (
        f"**{ticker}**  —  30-Day Avg Volume: **{vol_s}**\n"
        f"Price: **{price_s}**{chg_s}\n"
        f"{SEP}\n"
        f"**Filing Date:** {filed_date}\n"
        f"**Insider Name:** {insider_name}\n"
        + title_line
        + f"**Sector:** {sector or 'Unknown'}\n"
        f"**Value:** +${total_value:,.0f}"
        + (" *(10b5-1 plan)*" if is_10b5 else "")
        + f"\n{SEP}\n"
        f"**Daily ATR (14d):** {atr_d_s}\n"
        f"**Monthly ATR (21d):** {atr_m_s}\n"
        f"**3-Month Return:** {r3m_s}\n"
        f"**52-Week High Δ:** {h52_s}\n"
        f"**SPY r3m:** {spy_s}  |  **Regime:** {regime}\n"
        f"{SEP}\n"
        f"**Multiple Buys?** {multi}\n"
        f"**Repeat Insider?** {repeat_s}\n"
        f"**Trending Up?** {trending}\n"
        f"**Pre-scheduled (10b5)?** {plan_s}\n"
        f"**Financial Health:** {health_s}\n"
        f"{SEP}\n"
        f"**Score: {score:.0f} / 100**\n"
        f"{score_block}\n"
        f"{SEP}\n"
        f"{reason_line}"
        + kelly_line
    )
    discord_send(f"{emoji} {ticker}  |  {status}", body, color)

def discord_exit(ticker, ret_pct, reason, hold_days, entry_px, exit_px, score, kelly):
    emoji = "✅" if ret_pct > 0 else "❌"
    exit_labels = {
        "trail_stop": "🛑 Trail stop triggered",
        "hold_14d":   "⏰ 14-day max hold expired",
        "hold_30d":   "⏰ Hold period complete",
    }
    lines = [
        f"**Return:** {ret_pct:+.2f}%",
        f"**Hold:** {hold_days} days",
        f"**Entry:** ${entry_px:.2f}  →  **Exit:** ${exit_px:.2f}",
        f"**Exit Reason:** {exit_labels.get(reason, reason)}",
        f"**Score at Entry:** {score:.0f}  |  **Kelly Used:** {kelly:.0%}",
    ]
    discord_send(f"{emoji} EXIT  {ticker}  {ret_pct:+.1f}%",
        "\n".join(lines),
        0x2ECC71 if ret_pct > 0 else 0xE74C3C)

# ── ALPACA ────────────────────────────────────────────────────────────────────

ALP_H = {
    "APCA-API-KEY-ID":     ALPACA_KEY,
    "APCA-API-SECRET-KEY": ALPACA_SECRET,
    "Accept":              "application/json",
    "Content-Type":        "application/json",
}

def alp_get(path):
    try:
        r = requests.get(f"{ALPACA_BASE}/v2{path}", headers=ALP_H, timeout=15)
        if r.status_code == 200: return r.json()
        log(f"Alpaca GET {path} → {r.status_code}: {r.text[:300]}")
    except Exception as e:
        log(f"Alpaca GET {path}: {e}")
    return None

def alp_post(path, data):
    try:
        r = requests.post(f"{ALPACA_BASE}/v2{path}", headers=ALP_H, json=data, timeout=15)
        if r.status_code in (200, 201): return r.json()
        log(f"Alpaca POST {path} → {r.status_code}: {r.text[:300]}")
    except Exception as e:
        log(f"Alpaca POST {path}: {e}")
    return None

def alp_delete(path):
    try:
        r = requests.delete(f"{ALPACA_BASE}/v2{path}", headers=ALP_H, timeout=15)
        if r.status_code in (200, 204): return True
        log(f"Alpaca DELETE {path} → {r.status_code}: {r.text[:300]}")
    except Exception as e:
        log(f"Alpaca DELETE {path}: {e}")
    return False

def get_equity():
    acc = alp_get("/account")
    if not acc:
        raise RuntimeError("Alpaca auth failed — check paper key/secret")
    return sf(acc.get("equity", 0))

def is_market_open():
    clock = alp_get("/clock")
    if not clock:
        raise RuntimeError("Alpaca clock failed — check auth")
    return clock.get("is_open", False)

def get_price_alpaca(ticker):
    data = alp_get(f"/stocks/{ticker}/trades/latest")
    if data and "trade" in data:
        p = sf(data["trade"].get("p", 0))
        if p > 0: return p
    data2 = alp_get(f"/stocks/{ticker}/quotes/latest")
    if data2 and "quote" in data2:
        ap = sf(data2["quote"].get("ap", 0))
        bp = sf(data2["quote"].get("bp", 0))
        if ap and bp: return (ap + bp) / 2
        if ap: return ap
    return 0

def place_order(ticker, notional):
    # Always use whole shares — works for fractional and non-fractional stocks
    price = get_price_alpaca(ticker) or get_price_polygon(ticker)
    if not price or price <= 0:
        log(f"  No price for {ticker}, order failed")
        return None
    shares = int(notional / price)
    if shares < 1:
        log(f"  Shares < 1 for {ticker} at ${price:.2f}, skip")
        return None
    payload = {
        "symbol":        ticker,
        "qty":           str(shares),
        "side":          "buy",
        "type":          "market",
        "time_in_force": "day",
    }
    result = alp_post("/orders", payload)
    if result and result.get("id"):
        log(f"  ORDER OK: {ticker} {shares} shares @ ~${price:.2f} = ${shares*price:.0f} | id={result['id']}")
        return result
    log(f"  ORDER FAILED {ticker}: {result}")
    return None

def close_position_alpaca(ticker):
    ok = alp_delete(f"/positions/{ticker}")
    if ok: log(f"  CLOSED {ticker}")
    return ok

# ── POLYGON ───────────────────────────────────────────────────────────────────

def polygon_bars(ticker, days=100):
    end   = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    url   = (f"{POLYGON_BASE}/v2/aggs/ticker/{ticker}/range/1/day/"
             f"{start}/{end}?adjusted=true&sort=asc&limit=300&apiKey={POLYGON_KEY}")
    try:
        r = requests.get(url, timeout=15)
        return r.json().get("results", [])
    except: return []

def get_3m_return(ticker):
    bars = polygon_bars(ticker, days=95)
    if len(bars) < 2: return None
    return (bars[-1]["c"] - bars[0]["c"]) / bars[0]["c"]

def get_spy_r3m():
    return get_3m_return("SPY")

def get_atr_pct(ticker, period=14):
    bars = polygon_bars(ticker, days=40)
    if len(bars) < period + 1: return None
    bars = bars[-(period+1):]
    trs = []
    for i in range(1, len(bars)):
        h, l, pc = bars[i]["h"], bars[i]["l"], bars[i-1]["c"]
        trs.append(max(h-l, abs(h-pc), abs(l-pc)))
    price = bars[-1]["c"] or 1
    return (sum(trs)/len(trs)) / price * 100

def get_pct_from_52w_high(ticker):
    bars = polygon_bars(ticker, days=365)
    if not bars: return None
    high_52 = max(b["h"] for b in bars)
    cur = bars[-1]["c"]
    return round((cur - high_52) / high_52 * 100, 1)

def get_price_polygon(ticker):
    bars = polygon_bars(ticker, days=5)
    return bars[-1]["c"] if bars else 0

def get_avg_30d_volume_dollars(ticker):
    """Average daily dollar volume over last 30 trading days."""
    bars = polygon_bars(ticker, days=50)
    if not bars: return None
    recent = bars[-30:]
    vols = [b.get("v", 0) * b.get("c", 0) for b in recent]
    return sum(vols) / len(vols) if vols else None

def get_monthly_atr_pct(ticker):
    """21-day ATR as % of price (monthly rhythm, vs 14-day daily ATR)."""
    bars = polygon_bars(ticker, days=70)
    if len(bars) < 22: return None
    bars = bars[-22:]
    trs = []
    for i in range(1, len(bars)):
        h, l, pc = bars[i]["h"], bars[i]["l"], bars[i-1]["c"]
        trs.append(max(h-l, abs(h-pc), abs(l-pc)))
    price = bars[-1]["c"] or 1
    return (sum(trs) / len(trs)) / price * 100

def get_current_price_and_change(ticker):
    """Current price + 1-day change % for display."""
    bars = polygon_bars(ticker, days=5)
    if len(bars) < 2: return None, None
    cur = bars[-1]["c"]; prev = bars[-2]["c"]
    chg = (cur - prev) / prev * 100 if prev else 0
    return cur, chg

# ── FMP REMOVED — v15 runs without FMP ───────────────────────────────────────

# Sector and financial health checks removed. health_ok=True always.
# healthcare_low_cluster and health_fail filters are disabled as a result.

def get_sector(ticker):          return "N/A"
def get_financial_health(ticker): return True, "fmp_removed"

# ── SEC API ───────────────────────────────────────────────────────────────────

def fetch_form4_filings(state, hours_back=20):
    """Fetch only filings since last scan — prevents missing filings due to size cap."""
    last = state.get("last_scan_time")
    if last:
        since = last
    else:
        since = (datetime.utcnow() - timedelta(hours=hours_back)).strftime("%Y-%m-%dT%H:%M:%S")

    all_filings = []
    page_size   = 50
    from_idx    = 0
    while True:
        payload = {
            "query": f'filedAt:["{since}" TO *]',
            "from":  str(from_idx),
            "size":  str(page_size),
            "sort":  [{"filedAt": {"order": "asc"}}],
        }
        try:
            r = requests.post("https://api.sec-api.io/insider-trading",
                              headers={"Authorization": SEC_API_KEY},
                              json=payload, timeout=30)
            log(f"SEC API → {r.status_code} (from={from_idx})")
            if r.status_code != 200:
                log(f"SEC API body: {r.text[:300]}")
                break
            data     = r.json()
            batch    = data.get("transactions", [])
            total    = data.get("total", {}).get("value", 0)
            all_filings.extend(batch)
            from_idx += len(batch)
            if from_idx >= total or not batch:
                break
        except Exception as e:
            log(f"SEC API error: {e}")
            break

    # Update last scan time to now
    state["last_scan_time"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")
    log(f"  insider-trading: {len(all_filings)} filings since {since}")
    return all_filings

def parse_filing_transactions(filing):
    """Parse /insider-trading endpoint response — different field names from search endpoint."""
    txns   = []
    issuer = filing.get("issuer") or {}
    ticker = (issuer.get("tradingSymbol") or filing.get("ticker") or "").strip()
    if not ticker: return []
    accession = filing.get("accessionNo", "")
    filed_at  = (filing.get("filedAt", "") or "")[:10]
    if not filed_at: return []
    owner = filing.get("reportingOwner") or {}
    rel   = owner.get("relationship") or {}
    name  = (owner.get("name") or "").strip()
    title = (rel.get("officerTitle") or "").strip()
    if not title:
        title = "Director" if rel.get("isDirector") else ("Officer" if rel.get("isOfficer") else "")
    ndt      = filing.get("nonDerivativeTable") or {}
    txn_list = ndt.get("transactions", [])
    for txn in txn_list:
        code = (txn.get("coding", {}).get("code") or "").upper()
        if code != "P": continue
        amounts = txn.get("amounts") or {}
        try:
            shares = abs(float(amounts.get("shares") or 0))
            price  = float(amounts.get("pricePerShare") or 0)
        except: continue
        value = shares * price
        if value < 50_000: continue
        coding    = txn.get("coding") or {}
        footnotes = str(txn.get("footnotes","")) + str(filing.get("footnotes",""))
        is_10b5   = bool(coding.get("planFlag") or coding.get("plan") or
                         "10b5" in footnotes.lower() or
                         str(coding.get("code","")).lower() == "a")
        txns.append({
            "ticker":    ticker,
            "accession": accession,
            "filed_at":  filed_at,
            "name":      name,
            "title":     title,
            "is_10b5":   is_10b5,
            "value":     value,
        })
    return txns

# ── V15 SCORING ───────────────────────────────────────────────────────────────

def get_pre5_return(ticker, as_of_date_str):
    try:
        entry_dt = datetime.strptime(as_of_date_str[:10], "%Y-%m-%d")
        from_dt  = (entry_dt - timedelta(days=14)).strftime("%Y-%m-%d")
        to_dt    = (entry_dt - timedelta(days=1)).strftime("%Y-%m-%d")
        url = (f"{POLYGON_BASE}/v2/aggs/ticker/{ticker}/range/1/day"
               f"/{from_dt}/{to_dt}?adjusted=true&sort=asc&limit=10"
               f"&apiKey={POLYGON_KEY}")
        r = requests.get(url, timeout=8)
        if r.status_code == 200:
            bars = r.json().get("results", [])
            last5 = bars[-5:] if len(bars) >= 5 else bars
            if len(last5) >= 2:
                start = last5[0].get("c", 0)
                end   = last5[-1].get("c", 0)
                if start > 0:
                    return (end - start) / start
        return None
    except Exception:
        return None


def score_signal(value, atr_pct, pct_from_52w_high, r3m, spy_r3m, cluster, cluster_size, pre5_return=None):
    comp = {}
    atr = atr_pct or 0
    if   atr >= 20:  comp["pts_atr"] = 14
    elif atr >= 12:  comp["pts_atr"] = 20
    elif atr >= 8:   comp["pts_atr"] = 12
    elif atr >= 6:   comp["pts_atr"] = 8
    elif atr >= 5:   comp["pts_atr"] = 16
    elif atr >= 4:   comp["pts_atr"] = 10
    elif atr >= 3:   comp["pts_atr"] = 6
    elif atr >= 2:   comp["pts_atr"] = 2
    elif atr >= 1:   comp["pts_atr"] = 8
    else:            comp["pts_atr"] = 0

    h52 = pct_from_52w_high or 0
    if   h52 <= -75:  comp["pts_52w"] = 14
    elif h52 <= -60:  comp["pts_52w"] = 20
    elif h52 <= -50:  comp["pts_52w"] = 14
    elif h52 <= -40:  comp["pts_52w"] = 12
    elif h52 <= -30:  comp["pts_52w"] = 10
    elif h52 <= -20:  comp["pts_52w"] = 6
    elif h52 <= -10:  comp["pts_52w"] = 8
    elif h52 <= -5:   comp["pts_52w"] = -8
    else:             comp["pts_52w"] = 2

    if r3m is not None:
        if   r3m <= -0.60:  comp["pts_selloff"] = 22
        elif r3m <= -0.50:  comp["pts_selloff"] = 22
        elif r3m <= -0.40:  comp["pts_selloff"] = 25
        elif r3m <= -0.30:  comp["pts_selloff"] = 8
        elif r3m <= -0.25:  comp["pts_selloff"] = 0
        elif r3m <= -0.10:  comp["pts_selloff"] = 0
        elif r3m <= -0.05:  comp["pts_selloff"] = 10
        elif r3m <= 0.0:    comp["pts_selloff"] = 12
        elif r3m <= 0.10:   comp["pts_selloff"] = 2
        elif r3m <= 0.25:   comp["pts_selloff"] = 4
        else:               comp["pts_selloff"] = 2
    else:
        comp["pts_selloff"] = 0

    if cluster:
        if   cluster_size >= 3:  comp["pts_cluster"] = 25
        elif cluster_size == 2:  comp["pts_cluster"] = 18
        else:                    comp["pts_cluster"] = 0
    else:
        comp["pts_cluster"] = 0

    if cluster:
        if   value >= 5_000_000:  comp["pts_value"] = 8
        elif value >= 2_000_000:  comp["pts_value"] = 18
        elif value >= 1_000_000:  comp["pts_value"] = 10
        elif value >= 500_000:    comp["pts_value"] = 10
        elif value >= 250_000:    comp["pts_value"] = 8
        elif value >= 100_000:    comp["pts_value"] = 14
        else:                     comp["pts_value"] = 0
    else:
        if   value >= 2_000_000:  comp["pts_value"] = 10
        elif value >= 1_000_000:  comp["pts_value"] = 12
        elif value >= 500_000:    comp["pts_value"] = 12
        elif value >= 250_000:    comp["pts_value"] = 3
        elif value >= 100_000:    comp["pts_value"] = 8
        else:                     comp["pts_value"] = 0

    spy = spy_r3m or 0
    if   spy <= -0.10:  comp["pts_spy"] = 10
    elif spy <= -0.05:  comp["pts_spy"] = 8
    elif spy <= 0.0:    comp["pts_spy"] = 0
    elif spy <= 0.05:   comp["pts_spy"] = 8
    elif spy <= 0.10:   comp["pts_spy"] = 0
    else:               comp["pts_spy"] = 10

    comp["pts_pre5"] = 5 if pre5_return is not None and pre5_return >= 0 else 0

    for k in ["pts_title","pts_ownership","pts_staleness","pts_repeat","pts_whale","pts_market","pts_recency"]:
        comp[k] = 0

    return min(sum(comp.values()), 100), comp

# ── V15 KELLY ─────────────────────────────────────────────────────────────────

def kelly_size(score, cluster, cluster_size):
    if cluster:
        if cluster_size >= 4:  return 0.33   # halfKelly=33.4%, WR=75%, n=83
        if cluster_size == 3:
            return 0.24 if score >= 60 else 0.0
        if cluster_size == 2:
            return 0.32 if score >= 60 else 0.15
        return 0.10
    else:
        return 0.29 if score >= 56 else 0.0

# ── V15 FILTERS ───────────────────────────────────────────────────────────────

def apply_filters(ticker, title, is_10b5, cluster, cluster_size, score,
                  r3m, spy_r3m, routine, atr_pct, avg_vol_30d=None, value=0):
    if ticker in TICKER_BLACKLIST:
        return "ticker_blacklisted"
    if "SEE REMARKS" in (title or "").upper():
        return "see_remarks"
    if atr_pct is not None and atr_pct < ATR_MIN_PCT:
        return "atr_too_low"
    if avg_vol_30d and avg_vol_30d > 0 and value > avg_vol_30d * 60:
        return "private_placement"
    if is_10b5 and not cluster:
        return "10b5_plan"
    if cluster and cluster_size > MAX_CLUSTER_SIZE:
        return "cluster_too_large"
    if routine:
        return "routine_buyer"
    # V15 dynamic regime
    _spy = spy_r3m if spy_r3m is not None else 0
    _in_mild_stress = (SPY_MILD_STRESS_LO <= _spy < SPY_MILD_STRESS_HI)
    _cluster_floor  = CLUSTER_STRESS_FLOOR if _in_mild_stress else CLUSTER_MIN_SCORE
    if not cluster and score < SOLO_MIN_SCORE:
        return "score_too_low"
    if cluster and score < _cluster_floor:
        return "score_too_low_stress" if _in_mild_stress else "score_too_low"
    if r3m is not None and R3M_SKIP_ZONE_LO < r3m <= R3M_SKIP_ZONE_HI:
        return "r3m_dead_zone"
    # cluster_hot_stock REMOVED — hot clusters WR=85% after blacklist
    # deep_mid_solo REMOVED — floor 56 handles weak signals
    if 90 <= score < 100 and r3m is not None and r3m >= SCORE_90_100_MAX_R3M:
        return "score_90_100_hot"
    # healthcare_low_cluster and health_fail removed — FMP removed in v15
    if not cluster and spy_r3m is not None and spy_r3m < SPY_WEAK_REGIME_THRESHOLD:
        return "solo_weak_market"
    return None

# ── ROUTINE BUYER ─────────────────────────────────────────────────────────────

def is_routine_buyer(state, name, ticker, today):
    key    = f"{name}_{ticker}"
    hist   = state.get("routine_history", {}).get(key, [])
    cutoff = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=90)).strftime("%Y-%m-%d")
    return len([d for d in hist if d >= cutoff]) >= MAX_INSIDER_BUYS_90D

def record_buy(state, name, ticker, today):
    key = f"{name}_{ticker}"
    state.setdefault("routine_history", {}).setdefault(key, []).append(today)

# ── TRAILING STOP ─────────────────────────────────────────────────────────────

def check_trail_stop(pos, current_price):
    entry = sf(pos["entry_price"])
    high  = sf(pos.get("high_price", entry))
    if current_price > high:
        pos["high_price"] = current_price
        high = current_price
    open_profit = (high - entry) / entry if entry else 0
    if   open_profit >= TRAIL_TIER2_TRIG:  trail = TRAIL_TIER2_STOP
    elif open_profit >= TRAIL_TIER1_TRIG:  trail = TRAIL_TIER1_STOP
    else:                                   trail = TRAIL_INITIAL
    return current_price <= high * (1 - trail)

# ── POSITION MANAGEMENT ───────────────────────────────────────────────────────

def check_positions(state):
    today = datetime.now().strftime("%Y-%m-%d")
    for ticker in list(state["positions"].keys()):
        pos    = state["positions"][ticker]
        hold_d = (datetime.strptime(today, "%Y-%m-%d")
                  - datetime.strptime(pos.get("entry_date", today), "%Y-%m-%d")).days
        if hold_d >= MAX_HOLD_DAYS:
            _exit(state, ticker, "hold_14d"); continue
        price = get_price_alpaca(ticker) or get_price_polygon(ticker)
        if not price or price <= 0: continue
        if check_trail_stop(pos, price):
            _exit(state, ticker, "trail_stop")
        else:
            save_state(state)

def _exit(state, ticker, reason):
    pos = state["positions"].get(ticker)
    if not pos: return
    price    = get_price_alpaca(ticker) or get_price_polygon(ticker)
    entry_px = sf(pos["entry_price"])
    hold_d   = (datetime.now() - datetime.strptime(pos["entry_date"], "%Y-%m-%d")).days
    ret_pct  = (price - entry_px) / entry_px * 100 if entry_px and price else 0
    close_position_alpaca(ticker)
    discord_exit(ticker, ret_pct, reason, hold_d, entry_px, price or 0,
                 pos.get("score", 0), pos.get("kelly", 0))
    del state["positions"][ticker]
    save_state(state)

MAX_TOTAL_EXPOSURE = 0.85   # v16: cap total deployed at 85% of equity

def get_deployed_pct(state, equity):
    if equity <= 0: return 0.0
    total = sum(sf(p.get("notional", 0)) for p in state["positions"].values())
    return total / equity

def enter_position(state, ticker, score, score_comp, cluster, cluster_size,
                   r3m, atr_daily, atr_monthly, h52, value, spy_r3m, sector, name,
                   filed_date, insider_name, title, is_10b5, routine,
                   avg_vol_30d, current_price, price_chg_1d, health_ok):
    equity   = get_equity()
    k        = kelly_size(score, cluster, cluster_size)

    # v16: exposure cap — never exceed 85% of equity across all positions
    deployed = get_deployed_pct(state, equity)
    remaining = MAX_TOTAL_EXPOSURE - deployed
    if remaining <= 0:
        log(f"  {ticker}: exposure cap reached ({deployed:.0%} deployed), skip")
        return
    if k > remaining:
        log(f"  {ticker}: Kelly {k:.0%} → {remaining:.0%} (exposure cap, {deployed:.0%} deployed)")
        k = remaining

    notional = equity * k
    if notional < 1:
        log(f"  Notional ${notional:.0f} too small, skip {ticker}")
        return
    price = get_price_alpaca(ticker) or get_price_polygon(ticker)
    if not price or price <= 0:
        log(f"  No price for {ticker}, skip")
        return
    result = place_order(ticker, notional)
    if not result: return
    today = datetime.now().strftime("%Y-%m-%d")
    state["positions"][ticker] = {
        "ticker":       ticker,
        "entry_date":   today,
        "entry_price":  price,
        "high_price":   price,
        "score":        score,
        "kelly":        k,
        "cluster":      cluster,
        "cluster_size": cluster_size,
        "notional":     notional,
    }
    record_buy(state, name, ticker, today)
    save_state(state)
    discord_signal(
        ticker, filed_date, insider_name, title,
        cluster, cluster_size, value,
        score, score_comp,
        r3m, atr_daily, atr_monthly, h52, spy_r3m,
        sector, health_ok,
        is_10b5, routine,
        avg_vol_30d, current_price, price_chg_1d,
        None, k, traded=True
    )

# ── SCAN FILINGS — runs anytime, market open or closed ────────────────────────
# Evaluates every new filing. Filtered signals are discarded.
# Passing signals go into state["pending_trades"] — executed when market opens.

def scan_filings(state):
    # Use 72h lookback on Mondays to catch all weekend filings
    now_utc = datetime.utcnow()
    hours_back = 72 if now_utc.weekday() == 0 else 20
    filings = fetch_form4_filings(state, hours_back=hours_back)
    spy_r3m = get_spy_r3m()
    log(f"Filings: {len(filings)} | SPY r3m: {spy_r3m*100:+.1f}% {_regime_label(spy_r3m)}"
        if spy_r3m else f"Filings: {len(filings)}")

    by_ticker_date = defaultdict(list)
    skipped_sells  = 0
    skipped_small  = 0
    skipped_seen   = 0
    seen_acc_name  = set()  # v16: dedup (accession+name) to fix NKE double-count bug

    for filing in filings:
        acc = filing.get("accessionNo", "")
        if acc in state["seen_accessions"]:
            skipped_seen += 1
            continue
        txns = parse_filing_transactions(filing)
        if txns:
            state["seen_accessions"].append(acc)
        else:
            tables = filing.get("nonDerivativeTable", {})
            all_txns = tables.get("transactions", []) if isinstance(tables, dict) else []
            if not all_txns:
                all_txns = filing.get("transactions", [])
            has_purchase = any(
                t.get("transactionCoding", {}).get("transactionCode", "") == "P"
                for t in all_txns
            )
            if not has_purchase:
                skipped_sells += 1
            else:
                skipped_small += 1
        for txn in txns:
            dedup_key = (txn["accession"], txn["name"])
            if dedup_key in seen_acc_name:
                continue
            seen_acc_name.add(dedup_key)
            key = (txn["ticker"], txn["filed_at"])
            by_ticker_date[key].append(txn)

    log(f"  Breakdown: {skipped_seen} already seen | "
        f"{skipped_sells} sells/grants | "
        f"{skipped_small} buys <$50K | "
        f"{len(by_ticker_date)} signals to evaluate")

    pending = state.setdefault("pending_trades", {})

    for (ticker, filed_date), txns in by_ticker_date.items():

        cluster_size = len(set(t["name"] for t in txns))
        cluster      = cluster_size > 1
        total_value  = sum(t["value"] for t in txns)
        rep          = max(txns, key=lambda t: t["value"])
        title        = rep["title"]
        is_10b5      = rep["is_10b5"]
        name         = rep["name"]
        routine      = is_routine_buyer(state, name, ticker, filed_date)

        r3m         = get_3m_return(ticker)
        atr_daily   = get_atr_pct(ticker)
        atr_monthly = get_monthly_atr_pct(ticker)
        h52         = get_pct_from_52w_high(ticker)
        avg_vol_30d = get_avg_30d_volume_dollars(ticker)
        cur_px, chg = get_current_price_and_change(ticker)
        sector       = get_sector(ticker)
        health_ok, _ = get_financial_health(ticker)
        score, score_comp = score_signal(total_value, atr_daily or 0, h52 or 0,
                                         r3m, spy_r3m, cluster, cluster_size)

        if ticker in state["positions"]:
            log(f"  {ticker}: already in position, skip")
            discord_signal(
                ticker, filed_date, name, title,
                cluster, cluster_size, total_value,
                score, score_comp,
                r3m, atr_daily, atr_monthly, h52, spy_r3m,
                sector, health_ok, is_10b5, routine,
                avg_vol_30d, cur_px, chg,
                None, 0, traded=False, existing_position=True
            )
            continue
        if ticker in pending:
            log(f"  {ticker}: already queued, skip")
            discord_signal(
                ticker, filed_date, name, title,
                cluster, cluster_size, total_value,
                score, score_comp,
                r3m, atr_daily, atr_monthly, h52, spy_r3m,
                sector, health_ok, is_10b5, routine,
                avg_vol_30d, cur_px, chg,
                None, 0, traded=False, queued=True
            )
            continue

        if atr_daily is None or h52 is None:
            log(f"  {ticker}: missing market data, skip")
            discord_send(
                f"⚙️ {ticker} | NO MARKET DATA",
                f"**{ticker}** — filing found but Polygon returned no price/ATR data. Skipped.\n"
                f"Insider: {name} ({title}) | Value: ${total_value:,.0f}",
                0x95A5A6
            )
            continue

        pre5_return = get_pre5_return(ticker, filed_at)

        score, score_comp = score_signal(total_value, atr_daily, h52,
                                         r3m, spy_r3m, cluster, cluster_size, pre5_return)

        reason = apply_filters(ticker, title, is_10b5, cluster, cluster_size, score,
                               r3m, spy_r3m, routine, atr_daily,
                               avg_vol_30d=avg_vol_30d, value=total_value)

        cl_str  = f"CLUSTER cs={cluster_size}" if cluster else "solo"
        r3m_str = f"{r3m*100:+.0f}%" if r3m is not None else "N/A"
        mkt_str = "OPEN" if is_market_open() else "CLOSED"
        log(f"  {ticker} | {cl_str} | score={score:.0f} | r3m={r3m_str} | "
            f"atr={atr_daily:.1f}% | 52w={h52:.0f}% | ${total_value:,.0f} | "
            f"{'FILTERED: '+reason if reason else 'QUEUED [mkt '+mkt_str+']'}")

        k = kelly_size(score, cluster, cluster_size)

        if reason:
            discord_signal(
                ticker, filed_date, name, title,
                cluster, cluster_size, total_value,
                score, score_comp,
                r3m, atr_daily, atr_monthly, h52, spy_r3m,
                sector, health_ok, is_10b5, routine,
                avg_vol_30d, cur_px, chg,
                reason, 0, traded=False
            )
        else:
            pending[ticker] = {
                "ticker":       ticker,
                "filed_date":   filed_date,
                "insider_name": name,
                "title":        title,
                "cluster":      cluster,
                "cluster_size": cluster_size,
                "total_value":  total_value,
                "score":        score,
                "score_comp":   score_comp,
                "r3m":          r3m,
                "atr_daily":    atr_daily,
                "atr_monthly":  atr_monthly,
                "h52":          h52,
                "spy_r3m":      spy_r3m,
                "sector":       sector,
                "health_ok":    health_ok,
                "is_10b5":      is_10b5,
                "routine":      routine,
                "avg_vol_30d":  avg_vol_30d,
                "kelly":        k,
                "queued_at":    datetime.now().isoformat(),
            }
            log(f"    → Queued for open | score={score:.0f} | kelly={k:.0%}")
            discord_signal(
                ticker, filed_date, name, title,
                cluster, cluster_size, total_value,
                score, score_comp,
                r3m, atr_daily, atr_monthly, h52, spy_r3m,
                sector, health_ok, is_10b5, routine,
                avg_vol_30d, cur_px, chg,
                None, k, traded=False, queued=True
            )

    save_state(state)

# ── EXECUTE PENDING — runs only when market is open ───────────────────────────

def execute_pending(state):
    pending = state.get("pending_trades", {})
    if not pending:
        return
    log(f"Executing {len(pending)} pending trade(s)…")
    for ticker in list(pending.keys()):
        sig = pending[ticker]

        if ticker in state["positions"]:
            log(f"  {ticker}: position already exists, dropping from queue")
            del pending[ticker]
            save_state(state)
            continue

        log(f"  Executing: {ticker} | score={sig['score']:.0f} | kelly={sig['kelly']:.0%}")
        enter_position(
            state,
            ticker,
            sig["score"],
            sig["score_comp"],
            sig["cluster"],
            sig["cluster_size"],
            sig["r3m"],
            sig["atr_daily"],
            sig["atr_monthly"],
            sig["h52"],
            sig["total_value"],
            sig["spy_r3m"],
            sig["sector"],
            sig["insider_name"],
            sig["filed_date"],
            sig["insider_name"],
            sig["title"],
            sig["is_10b5"],
            sig["routine"],
            sig["avg_vol_30d"],
            None, None,   # current_price and chg fetched fresh inside enter_position
            sig["health_ok"],
        )
        del pending[ticker]
        save_state(state)

# ── DAILY SUMMARY ─────────────────────────────────────────────────────────────

def post_daily_summary(state, daily_trades):
    equity    = get_equity()
    positions = state.get("positions", {})
    queued    = state.get("pending_trades", {})
    spy_r3m   = get_spy_r3m()

    wins      = [t for t in daily_trades if t["ret"] > 0]
    loss      = [t for t in daily_trades if t["ret"] <= 0]
    total_ret = sum(t["ret"] for t in daily_trades)

    pos_lines = "\n".join(
        f"  **{t}** — entry ${state['positions'][t]['entry_price']:.2f} | "
        f"score={state['positions'][t].get('score',0):.0f} | "
        f"kelly={state['positions'][t].get('kelly',0):.0%}"
        for t in positions
    ) or "None"

    trade_lines = "\n".join(
        f"  {'✅' if t['ret']>0 else '❌'} **{t['ticker']}** {t['ret']:+.1f}% — {t['reason']}"
        for t in daily_trades
    ) or "None"

    queued_line = ", ".join(queued.keys()) if queued else "None"

    body = (
        f"**Equity:** ${equity:,.0f}\n"
        f"**Regime:** {_regime_label(spy_r3m)}"
        + (f" | SPY r3m: {spy_r3m*100:+.1f}%" if spy_r3m else "")
        + "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"**Trades today:** {len(daily_trades)} "
        f"({len(wins)}W / {len(loss)}L)"
        + (f" | Net P&L: {total_ret:+.1f}%" if daily_trades else "")
        + f"\n{trade_lines}\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"**Open positions:** {len(positions)}\n{pos_lines}\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"**Queued for tomorrow:** {queued_line}"
    )
    discord_send("📊 InsiderEdge Daily Summary", body, 0x5865F2)
    log("Daily summary posted to Discord")

# ── MAIN ──────────────────────────────────────────────────────────────────────

def run_cycle(mode="scan"):
    """
    Single cycle — designed for GitHub Actions.
    Mode:
      scan      = full cycle (scan filings + execute pending + check positions)
      premarket = scan + queue signals only (no execution — market not open yet)
      heartbeat = post heartbeat to Discord only
      summary   = post daily summary
    """
    import sys
    log("=" * 55)
    log(f"InsiderEdge v16 — {mode.upper()} cycle")
    log("=" * 55)

    state  = load_state()
    equity = get_equity()
    now    = datetime.now()
    log(f"Equity: ${equity:,.0f} | Open: {list(state['positions'].keys())} | Queued: {len(state.get('pending_trades',{}))}")

    try:
        if mode == "heartbeat":
            spy_r3m   = get_spy_r3m()
            positions = state.get("positions", {})
            queued    = state.get("pending_trades", {})
            pos_lines = "\n".join(
                f"  • **{t}** — entry ${state['positions'][t]['entry_price']:.2f} | score={state['positions'][t].get('score',0):.0f}"
                for t in positions
            ) or "None"
            discord_send(
                "💓 Heartbeat",
                f"**{now.strftime('%b %d %H:%M')}** — GitHub Actions ✅\n"
                f"**Equity:** ${equity:,.0f}\n"
                f"**Regime:** {_regime_label(spy_r3m)}"
                + (f" | SPY r3m: {spy_r3m*100:+.1f}%" if spy_r3m else "")
                + f"\n**Positions:** {len(positions)} | **Queued:** {len(queued)}\n"
                + (pos_lines if positions else ""),
                0x2C2F33
            )
            return

        if mode == "summary":
            post_daily_summary(state, [])
            return

        # scan + premarket: always scan for new filings
        scan_filings(state)

        market_open = is_market_open()

        if mode == "scan" and market_open:
            # Execute anything queued
            execute_pending(state)
            # Check existing positions for exits
            if state["positions"]:
                check_positions(state)
        elif mode == "premarket":
            # Queue signals but don't execute yet
            queued = list(state.get("pending_trades", {}).keys())
            if queued:
                log(f"Pre-market: {len(queued)} trade(s) ready for 9:30 open: {queued}")
                discord_send("⏰ Pre-Market Queue",
                    f"**{len(queued)} trade(s) queued for 9:30 AM open:**\n" +
                    "\n".join(f"  • **{t}**" for t in queued),
                    0xF39C12)
        elif mode == "scan" and not market_open:
            queued = list(state.get("pending_trades", {}).keys())
            log(f"Market closed — {len(queued)} queued" if queued else "Market closed — nothing queued")

        # 4:30 PM summary
        if now.hour == 16 and now.minute >= 30:
            post_daily_summary(state, [])

    except Exception as e:
        log(f"ERROR: {e}")
        discord_send("⚠️ Bot Error", str(e), 0xE74C3C)
        sys.exit(1)
    finally:
        save_state(state)

def main():
    import sys
    mode = "scan"
    for i, arg in enumerate(sys.argv[1:], 1):
        if arg == "--mode" and i < len(sys.argv):
            mode = sys.argv[i+1]
    run_cycle(mode)

if __name__ == "__main__":
    main()
