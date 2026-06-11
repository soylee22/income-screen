#!/usr/bin/env python3
"""Income snowball acquisition screen (evidence-anchored, cross-country, sector-aware).

Criteria per wiki/references/income-acquisition-evidence-review.md (2026-06-10):
gates = quality + yield band + uncut record + (non-financials) FCF cover & leverage;
ranking = cheapness vs own 5y yield history + quality + dividend growth + safety.

Usage:
  python3 screen.py                     # default universe.txt, fresh fetch
  python3 screen.py --cache             # reuse cached fetch from today
  python3 screen.py --tickers my.txt    # custom universe (one ticker per line)
  python3 screen.py --top 20            # rows to print
"""

import argparse
import json
import sys
import time
from datetime import date
from pathlib import Path

import pandas as pd
import yfinance as yf

HERE = Path(__file__).parent
CACHE = HERE / "cache"

FINANCIAL_SECTORS = {"Financial Services", "Real Estate"}

# PRA-override: UK financials the Bank of England's PRA FORCED to suspend dividends in
# March 2020. We forgive ONLY that 2020 cut, and only for names verified (2026-06-11) to
# have restored the dividend to >=90% of their 2019 level. They must still clear every
# other gate (yield band, ROE, trend, etc.). Auditable list, edit here to add/remove.
PRA_OVERRIDE = {
    "HSBA.L": "PRA-forced 2020 suspension; ordinary dividend restored above 2019",
    "LLOY.L": "PRA-forced 2020 suspension; restored to ~94% of 2019",
    "NWG.L":  "PRA-forced 2020 suspension; restored and growing",
    "BARC.L": "PRA-forced 2020 suspension; restored (still fails yield floor)",
    "STAN.L": "PRA-forced 2020 suspension; restored (still fails yield floor)",
    "AV.L":   "2020 rebase under PRA pressure; recovered above 2019",
}

# Gates (lane A = general corporates, lane B = financials/REITs)
MIN_MCAP_USD = 15e9
YIELD_MIN, YIELD_MAX = 3.0, 8.0          # trap zone above 8 (quintile evidence)
MIN_DIV_YEARS = 10                        # growers/payers cohort
MAX_CUT = 0.20                            # >20% YoY fall in any of last 10 complete years = cut
MIN_ROE = 0.10                            # lane A (0.08 lane B)
MIN_FCF_COVER = 1.3                       # FCF / dividends paid, lane A only (~payout <= 77% of FCF)
MAX_DEBT_EBITDA = 4.0                     # lane A only
MOM_12M_MIN = -0.10                       # falling-knife filter: drop names down >10% over 12m
MOM_VS_200D_MIN = -0.12                   # or sitting >12% below their 200-day average

WEIGHTS = {"cheap": 0.35, "quality": 0.30, "growth": 0.20, "safety": 0.15}

FX_PAIRS = {"GBP": "GBPUSD=X", "GBp": "GBPUSD=X", "EUR": "EURUSD=X", "CHF": "CHFUSD=X",
            "HKD": "HKDUSD=X", "DKK": "DKKUSD=X", "SEK": "SEKUSD=X", "NOK": "NOKUSD=X",
            "AUD": "AUDUSD=X", "CAD": "CADUSD=X", "JPY": "JPYUSD=X", "SGD": "SGDUSD=X"}


def fx_rates(currencies):
    rates = {"USD": 1.0}
    for cur in set(currencies) - {"USD", None}:
        pair = FX_PAIRS.get(cur)
        if not pair:
            rates[cur] = None
            continue
        try:
            rates[cur] = yf.Ticker(pair).fast_info["lastPrice"]
        except Exception:
            rates[cur] = None
    return rates


def trim_specials(divs):
    """Drop one-off special payments: any payment > 2.0x the median of the prior
    8 payments (UK interim/final asymmetry stays under this; B-share returns and
    sale-proceeds specials, e.g. Aviva 2022 / HSBC 2023, get removed)."""
    if len(divs) < 5:
        return divs
    ref = divs.shift(1).rolling(8, min_periods=4).median().fillna(divs.median())
    return divs[divs <= 2.0 * ref]


def annual_dividends(ticker_obj):
    """Complete-year dividend totals (specials trimmed), oldest->newest."""
    divs = ticker_obj.dividends
    if divs is None or divs.empty:
        return pd.Series(dtype=float)
    divs = trim_specials(divs)
    yearly = divs.groupby(divs.index.year).sum()
    return yearly[yearly.index < date.today().year]  # drop partial current year


def div_record(yearly):
    """(years_paying, uncut_last10, cagr5) from complete-year totals.

    COVID/PRA exemption: a >MAX_CUT fall in 2020 or 2021 is forgiven IF the latest
    complete year's dividend has recovered to >=90% of the 2019 level (forced
    suspension restored = not a cash-driven cut; unrecovered or non-COVID cuts count).
    """
    years = len(yearly)
    if years < 2:
        return years, False, None
    chg = yearly.tail(11).pct_change().dropna()
    cut_years = set(chg[chg <= -MAX_CUT].index)
    if cut_years & {2020, 2021} and 2019 in yearly.index and yearly.loc[2019] > 0 \
            and yearly.iloc[-1] >= 0.9 * yearly.loc[2019]:
        cut_years -= {2020, 2021}
    uncut = not cut_years
    cagr5 = None
    if years >= 6 and yearly.iloc[-6] > 0:
        cagr5 = (yearly.iloc[-1] / yearly.iloc[-6]) ** (1 / 5) - 1
    return years, uncut, cagr5


def fetch_one(symbol):
    t = yf.Ticker(symbol)
    info = t.info
    yearly = annual_dividends(t)
    years, uncut, cagr5 = div_record(yearly)
    return {
        "ticker": symbol,
        "name": info.get("shortName"),
        "sector": info.get("sector"),
        "currency": info.get("currency"),
        "mcap_local": info.get("marketCap"),
        "yield_pct": info.get("dividendYield"),            # already in %, correct on .L
        "yield_5y_avg": info.get("fiveYearAvgDividendYield"),
        "payout_ratio": info.get("payoutRatio"),
        "roe": info.get("returnOnEquity"),
        "gross_margin": info.get("grossMargins"),
        "fcf": info.get("freeCashflow"),
        "total_debt": info.get("totalDebt"),
        "ebitda": info.get("ebitda"),
        "div_years": years,
        "uncut_10y": uncut,
        "div_cagr5": cagr5,
        "mom_12m": info.get("52WeekChange"),               # trailing 1y price change
        "px": info.get("currentPrice") or info.get("regularMarketPrice"),
        "sma200": info.get("twoHundredDayAverage"),
        "wk52high": info.get("fiftyTwoWeekHigh"),
    }


STORE = HERE / "store.json"


def load_store():
    return json.loads(STORE.read_text()) if STORE.exists() else {}


def fetch_universe(tickers, max_age_days):
    """Persistent per-ticker store. Reuse rows fetched within max_age_days;
    fetch only the stale/missing names; always write back. max_age_days=None
    forces a full refresh; a huge value reuses everything regardless of age."""
    store = load_store()
    today = date.today()
    rows, fetched, reused, failed = [], 0, 0, []
    for i, sym in enumerate(tickers, 1):
        cached = store.get(sym)
        fresh = False
        if cached and max_age_days is not None and "_fetched" in cached:
            age = (today - date.fromisoformat(cached["_fetched"])).days
            fresh = age <= max_age_days
        if fresh:
            rows.append(cached)
            reused += 1
            continue
        try:
            row = fetch_one(sym)
            row["_fetched"] = today.isoformat()
            store[sym] = row
            rows.append(row)
            fetched += 1
            if fetched % 25 == 0:
                STORE.write_text(json.dumps(store))  # checkpoint
                print(f"  [{i}/{len(tickers)}] checkpoint, {fetched} fetched", file=sys.stderr)
        except Exception as e:
            if cached:                       # fall back to stale data rather than drop the name
                rows.append(cached)
            failed.append(sym)
            print(f"  [{i}/{len(tickers)}] {sym} FAILED: {e}", file=sys.stderr)
        time.sleep(0.4)
    STORE.write_text(json.dumps(store))
    print(f"  store: {fetched} fetched, {reused} reused, {len(failed)} failed "
          f"({len(store)} total held)", file=sys.stderr)
    return [r for r in rows if "ticker" in r]


def apply_gates(df):
    rates = fx_rates(df["currency"])
    df["mcap_usd"] = df.apply(
        lambda r: (r["mcap_local"] or 0) * (rates.get(r["currency"]) or 0), axis=1)
    df["lane"] = df["sector"].map(lambda s: "B" if s in FINANCIAL_SECTORS else "A")

    def gate(r):
        fails = []
        if r["mcap_usd"] < MIN_MCAP_USD: fails.append("mcap")
        y = r["yield_pct"]
        if y is None or not (YIELD_MIN <= y <= YIELD_MAX): fails.append("yield_band")
        if r["div_years"] < MIN_DIV_YEARS: fails.append("record<10y")
        if not r["uncut_10y"]: fails.append("cut_in_10y")
        min_roe = MIN_ROE if r["lane"] == "A" else 0.08
        if pd.isna(r["roe"]) or r["roe"] is None or r["roe"] < min_roe: fails.append("roe")
        if r["lane"] == "A":
            divs_paid = (r["mcap_local"] or 0) * (y or 0) / 100
            if not r["fcf"] or divs_paid <= 0 or r["fcf"] / divs_paid < MIN_FCF_COVER:
                fails.append("fcf_cover")
            if r["ebitda"] and r["ebitda"] > 0 and r["total_debt"] is not None:
                if r["total_debt"] / r["ebitda"] > MAX_DEBT_EBITDA: fails.append("leverage")
        # falling-knife filter: drop names in a clear downtrend (both lanes)
        mom, px, sma = r.get("mom_12m"), r.get("px"), r.get("sma200")
        downtrend = (mom is not None and mom < MOM_12M_MIN) or \
                    (px and sma and (px / sma - 1) < MOM_VS_200D_MIN)
        if downtrend:
            fails.append("downtrend")
        return ",".join(fails)

    df["fails"] = df.apply(gate, axis=1)
    # PRA override: forgive ONLY the 2020 cut for the documented UK financials
    df["overridden"] = df.apply(
        lambda r: r["ticker"] in PRA_OVERRIDE and "cut_in_10y" in r["fails"], axis=1)
    df["fails"] = df.apply(
        lambda r: ",".join(f for f in r["fails"].split(",") if f != "cut_in_10y")
        if r["overridden"] else r["fails"], axis=1)
    return df


def zscore(s):
    s = pd.to_numeric(s, errors="coerce")
    sd = s.std(ddof=0)
    return (s - s.mean()) / sd if sd and sd > 0 else s * 0


def rank(survivors):
    df = survivors.copy()
    # Cheapness: yield vs own 5y average (Weiss signal; honestly a value tilt). >1 = cheap.
    df["cheap_ratio"] = (df["yield_pct"] / df["yield_5y_avg"]).clip(0.5, 2.0).fillna(1.0)
    df["fcf_cover"] = df.apply(
        lambda r: r["fcf"] / (r["mcap_local"] * r["yield_pct"] / 100)
        if r["lane"] == "A" and r["fcf"] and r["mcap_local"] and r["yield_pct"] else None, axis=1)
    z_cheap = zscore(df["cheap_ratio"])
    z_qual = (zscore(df["roe"]) + zscore(df["gross_margin"])) / 2
    z_growth = zscore(pd.to_numeric(df["div_cagr5"], errors="coerce").clip(upper=0.15))
    z_safe = zscore(df["fcf_cover"]).fillna(0.0)  # lane B gets neutral safety
    df["score"] = (WEIGHTS["cheap"] * z_cheap + WEIGHTS["quality"] * z_qual.fillna(0)
                   + WEIGHTS["growth"] * z_growth.fillna(0) + WEIGHTS["safety"] * z_safe)
    return df.sort_values("score", ascending=False)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", default=str(HERE / "universe.txt"))
    ap.add_argument("--max-age", type=int, default=30,
                    help="reuse stored rows up to this many days old (default 30)")
    ap.add_argument("--refresh", action="store_true", help="force full re-fetch")
    ap.add_argument("--rerank", action="store_true",
                    help="rank from the store only, fetch nothing (instant)")
    ap.add_argument("--top", type=int, default=15)
    args = ap.parse_args()
    max_age = None if args.refresh else (10**6 if args.rerank else args.max_age)

    tickers = [l.strip() for l in Path(args.tickers).read_text().splitlines()
               if l.strip() and not l.startswith("#")]
    print(f"Screening {len(tickers)} tickers...", file=sys.stderr)
    df = pd.DataFrame(fetch_universe(tickers, max_age))
    df = apply_gates(df)

    survivors = df[df["fails"] == ""]
    ranked = rank(survivors)

    out = HERE / f"screen-{date.today().isoformat()}.csv"
    ranked.to_csv(out, index=False)

    ranked["mom12m_pct"] = (pd.to_numeric(ranked["mom_12m"], errors="coerce") * 100).round(1)
    cols = ["ticker", "name", "sector", "lane", "yield_pct", "yield_5y_avg",
            "cheap_ratio", "roe", "div_cagr5", "mom12m_pct", "div_years", "score"]
    pd.set_option("display.width", 200)
    print(f"\n=== SURVIVORS {len(ranked)}/{len(df)} (gates: mcap>=$10bn, yield {YIELD_MIN}-{YIELD_MAX}%, "
          f"10y+ uncut record, ROE, lane-A FCF cover & leverage) ===\n")
    print(ranked[cols].head(args.top).round(3).to_string(index=False))
    print(f"\nBUY CANDIDATES THIS MONTH (top 3 by composite): "
          f"{', '.join(ranked['ticker'].head(3))}")
    print(f"\nRejected with reasons -> see {out.name} alongside full ranked CSV")
    rej = df[df["fails"] != ""][["ticker", "fails"]]
    rej.to_csv(HERE / f"rejects-{date.today().isoformat()}.csv", index=False)
    print(rej.to_string(index=False))


if __name__ == "__main__":
    main()
