#!/usr/bin/env python3
"""Income snowball acquisition screen (evidence-anchored, cross-country, sector-aware).

Pipeline: hard gates (size, yield band, uncut record + PRA override, robust quality,
FCF cover, net leverage, trend) then a winsorised composite rank, OR a net-income view.
Quality uses ROIC + gross profitability + operating margin for operating companies
(robust to buyback-driven equity distortion that breaks ROE) and ROE for financials.

Usage:
  python3 screen.py                         # incremental fetch, composite rank
  python3 screen.py --mode net_income       # sort survivors by net yield (growth-guarded)
  python3 screen.py --rerank                # rank from store only (instant)
  python3 screen.py --refresh               # force full re-fetch
  python3 screen.py --tickers my.txt --top 30
"""

import argparse
import json
import sys
import time
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

HERE = Path(__file__).parent
STORE = HERE / "store.json"
FINANCIAL_SECTORS = {"Financial Services", "Real Estate"}

# ======================== CONFIG (all tunables live here) ========================
MIN_MCAP_USD = 15e9
YIELD_MIN, YIELD_MAX = 3.0, 8.0          # below 3 not income; above 8 = trap zone
MIN_DIV_YEARS = 10
MAX_CUT = 0.20                            # >20% YoY fall in last 10y = a cut
MIN_ROIC_A = 0.08                         # lane A (operating cos): ROIC floor...
MIN_ROA_A = 0.05                          # ...or ROA floor (robust fallback)
MIN_ROE_B = 0.08                          # lane B (financials/REITs): ROE floor
MIN_ROE_FALLBACK = 0.10                  # lane A last-resort if ROIC & ROA both missing
MIN_FCF_COVER = 1.3                       # FCF / dividends paid, lane A
MAX_NET_LEVERAGE = 4.0                    # (total debt - cash) / EBITDA, lane A
MOM_12M_MIN = -0.10                       # falling-knife: drop names down >10% over 12m
MOM_VS_200D_MIN = -0.12                   # or sitting >12% below their 200-day average

WEIGHTS = {"cheap": 0.35, "quality": 0.30, "growth": 0.20, "safety": 0.15}

# Dividend withholding tax for a UK ISA investor (passive, no reclaim). UK/HK/IE 0;
# US 15% (W-8BEN; 0% in a SIPP); EU ~26%; Switzerland 35%; Japan 10% (treaty).
WHT_BY_COUNTRY = {"UK": 0.0, "HK": 0.0, "IE": 0.0, "US": 0.15, "NL": 0.15, "JP": 0.10,
                  "ES": 0.19, "DE": 0.26, "FR": 0.26, "IT": 0.26, "BE": 0.30, "CH": 0.35}
TAX_TILT = 0.7        # score penalty per unit of WHT (prefer tax-efficient domiciles)

# Domicile overrides for ADRs / names whose tax home is not their listing suffix.
# A US-listed ADR of a UK company (BTI, IMBBY) suffers UK (0%) withholding, not US 15%.
COUNTRY_OVERRIDE = {"BTI": "UK", "IMBBY": "UK", "UL": "UK", "JAPAY": "JP", "BUD": "BE"}

# PRA-override: UK financials the Bank of England forced to suspend dividends in March
# 2020. Forgive ONLY that 2020 cut, and only for names verified (2026-06-11) to have
# restored to >=90% of 2019. They still clear every other gate. Auditable; edit freely.
PRA_OVERRIDE = {
    "HSBA.L": "PRA-forced 2020 suspension; ordinary dividend restored above 2019",
    "LLOY.L": "PRA-forced 2020 suspension; restored to ~94% of 2019",
    "NWG.L":  "PRA-forced 2020 suspension; restored and growing",
    "BARC.L": "PRA-forced 2020 suspension; restored (still fails yield floor)",
    "STAN.L": "PRA-forced 2020 suspension; restored (still fails yield floor)",
    "AV.L":   "2020 rebase under PRA pressure; recovered above 2019",
}

FX_PAIRS = {"GBP": "GBPUSD=X", "GBp": "GBPUSD=X", "EUR": "EURUSD=X", "CHF": "CHFUSD=X",
            "HKD": "HKDUSD=X", "JPY": "JPYUSD=X", "DKK": "DKKUSD=X", "SEK": "SEKUSD=X",
            "NOK": "NOKUSD=X", "AUD": "AUDUSD=X", "CAD": "CADUSD=X", "SGD": "SGDUSD=X"}
# ================================================================================


def country(tk):
    if tk in COUNTRY_OVERRIDE:
        return COUNTRY_OVERRIDE[tk]
    for sfx, c in [(".L", "UK"), (".PA", "FR"), (".DE", "DE"), (".SW", "CH"), (".HK", "HK"),
                   (".MC", "ES"), (".MI", "IT"), (".AS", "NL"), (".T", "JP")]:
        if tk.endswith(sfx):
            return c
    return "US"


# -------------------------------- fetch helpers --------------------------------
def fx_rates(currencies):
    rates = {"USD": 1.0}
    for cur in set(currencies) - {"USD", None}:
        pair = FX_PAIRS.get(cur)
        try:
            rates[cur] = yf.Ticker(pair).fast_info["lastPrice"] if pair else None
        except Exception:
            rates[cur] = None
    return rates


def trim_specials(divs):
    """Drop one-off specials: any payment > 2x the trailing-8 median."""
    if len(divs) < 5:
        return divs
    ref = divs.shift(1).rolling(8, min_periods=4).median().fillna(divs.median())
    return divs[divs <= 2.0 * ref]


def annual_dividends(t):
    divs = t.dividends
    if divs is None or divs.empty:
        return pd.Series(dtype=float)
    divs = trim_specials(divs)
    yearly = divs.groupby(divs.index.year).sum()
    return yearly[yearly.index < date.today().year]


def div_record(yearly):
    """(years, uncut_last10, cagr5) with the 2020/21 COVID-PRA recovery exemption."""
    years = len(yearly)
    if years < 2:
        return years, False, None
    chg = yearly.tail(11).pct_change().dropna()
    cut_years = set(chg[chg <= -MAX_CUT].index)
    if cut_years & {2020, 2021} and 2019 in yearly.index and yearly.loc[2019] > 0 \
            and yearly.iloc[-1] >= 0.9 * yearly.loc[2019]:
        cut_years -= {2020, 2021}
    cagr5 = (yearly.iloc[-1] / yearly.iloc[-6]) ** (1 / 5) - 1 \
        if years >= 6 and yearly.iloc[-6] > 0 else None
    return years, not cut_years, cagr5


def robust_div_growth(yearly):
    """Log-linear trend slope over the last 8 complete years: robust to single-year
    spikes/dips (a far better growth signal than endpoint-to-endpoint CAGR)."""
    y = yearly.tail(8)
    y = y[y > 0]
    if len(y) < 4:
        return None
    slope = np.polyfit(np.arange(len(y)), np.log(y.values), 1)[0]
    return float(np.exp(slope) - 1)


def _row(df, *names):
    """First present, non-null value among `names` from a statement's latest column."""
    if df is None or df.empty:
        return None
    for n in names:
        if n in df.index:
            v = df.loc[n].iloc[0]
            if pd.notna(v):
                return float(v)
    return None


def fcf_from_statement(cf):
    fcf = divs_paid = None
    if cf is not None and not cf.empty:
        fcf = _row(cf, "Free Cash Flow")
        if fcf is None:
            ocf, capex = _row(cf, "Operating Cash Flow"), _row(cf, "Capital Expenditure")
            if ocf is not None and capex is not None:
                fcf = ocf + capex
        dp = _row(cf, "Cash Dividends Paid")
        divs_paid = abs(dp) if dp is not None else None
    return fcf, divs_paid


def roic_from_statements(inc, bs):
    """ROIC = EBIT*(1-tax) / invested capital; gross profitability = GP/assets; cash."""
    roic = gross_prof = cash = None
    ebit = _row(inc, "EBIT", "Operating Income")
    tax, pretax = _row(inc, "Tax Provision"), _row(inc, "Pretax Income")
    gp = _row(inc, "Gross Profit")
    ic = _row(bs, "Invested Capital")
    assets = _row(bs, "Total Assets")
    cash = _row(bs, "Cash And Cash Equivalents",
                "Cash Cash Equivalents And Short Term Investments")
    if gp is not None and assets and assets > 0:
        gross_prof = gp / assets
    if ebit is not None and ic and ic > 0:
        tr = (tax / pretax) if (tax is not None and pretax and pretax > 0) else 0.21
        roic = ebit * (1 - min(max(tr, 0.0), 0.40)) / ic
    return roic, gross_prof, cash


def fetch_one(symbol):
    t = yf.Ticker(symbol)
    info = t.info
    sector = info.get("sector")
    yearly = annual_dividends(t)
    years, uncut, cagr5 = div_record(yearly)
    fcf = divs_paid_actual = roic = gross_prof = cash = None
    if sector not in FINANCIAL_SECTORS:                  # operating cos: pull statements
        try:
            fcf, divs_paid_actual = fcf_from_statement(t.cashflow)
            roic, gross_prof, cash = roic_from_statements(t.income_stmt, t.balance_sheet)
        except Exception as e:
            print(f"    {symbol} statement fetch partial: {e}", file=sys.stderr)
    if fcf is None:
        fcf = info.get("freeCashflow")
    return {
        "ticker": symbol, "name": info.get("shortName"), "sector": sector,
        "currency": info.get("currency"), "mcap_local": info.get("marketCap"),
        "yield_pct": info.get("dividendYield"),
        "yield_5y_avg": info.get("fiveYearAvgDividendYield"),
        "payout_ratio": info.get("payoutRatio"),
        "roe": info.get("returnOnEquity"), "roa": info.get("returnOnAssets"),
        "op_margin": info.get("operatingMargins"), "gross_margin": info.get("grossMargins"),
        "roic": roic, "gross_prof": gross_prof,
        "fcf": fcf, "divs_paid_actual": divs_paid_actual, "cash": cash,
        "total_debt": info.get("totalDebt"), "ebitda": info.get("ebitda"),
        "div_years": years, "uncut_10y": uncut,
        "div_cagr5": cagr5, "div_growth": robust_div_growth(yearly),
        "mom_12m": info.get("52WeekChange"),
        "px": info.get("currentPrice") or info.get("regularMarketPrice"),
        "sma200": info.get("twoHundredDayAverage"), "wk52high": info.get("fiftyTwoWeekHigh"),
    }


# -------------------------------- store / fetch loop --------------------------------
def load_store():
    return json.loads(STORE.read_text()) if STORE.exists() else {}


def fetch_universe(tickers, max_age_days):
    store = load_store()
    today = date.today()
    rows, fetched, reused, failed = [], 0, 0, []
    for i, sym in enumerate(tickers, 1):
        cached = store.get(sym)
        fresh = bool(cached and max_age_days is not None and "_fetched" in cached
                     and (today - date.fromisoformat(cached["_fetched"])).days <= max_age_days)
        if fresh:
            rows.append(cached); reused += 1; continue
        try:
            row = fetch_one(sym); row["_fetched"] = today.isoformat()
            store[sym] = row; rows.append(row); fetched += 1
            if fetched % 25 == 0:
                STORE.write_text(json.dumps(store))
                print(f"  [{i}/{len(tickers)}] checkpoint, {fetched} fetched", file=sys.stderr)
        except Exception as e:
            if cached:
                rows.append(cached)
            failed.append(sym)
            print(f"  [{i}/{len(tickers)}] {sym} FAILED: {e}", file=sys.stderr)
        time.sleep(0.4)
    STORE.write_text(json.dumps(store))
    print(f"  store: {fetched} fetched, {reused} reused, {len(failed)} failed "
          f"({len(store)} held)", file=sys.stderr)
    return [r for r in rows if "ticker" in r]


# -------------------------------- gates --------------------------------
EXPECTED_COLS = ["ticker", "name", "sector", "currency", "mcap_local", "yield_pct",
                 "yield_5y_avg", "payout_ratio", "roe", "roa", "op_margin", "gross_margin",
                 "roic", "gross_prof", "fcf", "divs_paid_actual", "cash", "total_debt",
                 "ebitda", "div_years", "uncut_10y", "div_cagr5", "div_growth", "mom_12m",
                 "px", "sma200", "wk52high"]


def apply_gates(df):
    for c in EXPECTED_COLS:                  # robust to any store schema (old/partial)
        if c not in df.columns:
            df[c] = None
    rates = fx_rates(df["currency"])
    df["mcap_usd"] = df.apply(lambda r: (r["mcap_local"] or 0) * (rates.get(r["currency"]) or 0), axis=1)
    df["lane"] = df["sector"].map(lambda s: "B" if s in FINANCIAL_SECTORS else "A")

    def quality_ok(r):
        if r["lane"] == "A":
            roic, roa, roe = r.get("roic"), r.get("roa"), r.get("roe")
            opm = r.get("op_margin")
            if opm is not None and opm <= 0:                 # must be operationally profitable
                return False
            if roic is not None and not pd.isna(roic):
                return roic >= MIN_ROIC_A or (roa is not None and roa >= MIN_ROA_A)
            if roa is not None and not pd.isna(roa):
                return roa >= MIN_ROA_A
            return roe is not None and not pd.isna(roe) and roe >= MIN_ROE_FALLBACK
        return r.get("roe") is not None and not pd.isna(r.get("roe")) and r["roe"] >= MIN_ROE_B

    def gate(r):
        fails = []
        if r["mcap_usd"] < MIN_MCAP_USD: fails.append("mcap")
        y = r["yield_pct"]
        if y is None or not (YIELD_MIN <= y <= YIELD_MAX): fails.append("yield_band")
        if r["div_years"] < MIN_DIV_YEARS: fails.append("record<10y")
        if not r["uncut_10y"]: fails.append("cut_in_10y")
        if not quality_ok(r): fails.append("quality")
        if r["lane"] == "A":
            divs_paid = r.get("divs_paid_actual") or (r["mcap_local"] or 0) * (y or 0) / 100
            if not r["fcf"] or divs_paid <= 0 or r["fcf"] / divs_paid < MIN_FCF_COVER:
                fails.append("fcf_cover")
            ebitda = r.get("ebitda")
            if ebitda and ebitda > 0 and r.get("total_debt") is not None:
                net_debt = r["total_debt"] - (r.get("cash") or 0)
                if net_debt / ebitda > MAX_NET_LEVERAGE: fails.append("leverage")
        mom, px, sma = r.get("mom_12m"), r.get("px"), r.get("sma200")
        if (mom is not None and mom < MOM_12M_MIN) or (px and sma and (px / sma - 1) < MOM_VS_200D_MIN):
            fails.append("downtrend")
        return ",".join(fails)

    df["fails"] = df.apply(gate, axis=1)
    df["overridden"] = df.apply(
        lambda r: r["ticker"] in PRA_OVERRIDE and "cut_in_10y" in r["fails"], axis=1)
    df["fails"] = df.apply(
        lambda r: ",".join(f for f in r["fails"].split(",") if f != "cut_in_10y")
        if r["overridden"] else r["fails"], axis=1)
    return df


# -------------------------------- ranking --------------------------------
def winsor(s, lo=0.05, hi=0.95):
    s = pd.to_numeric(s, errors="coerce")
    if s.notna().sum() < 5:
        return s
    return s.clip(s.quantile(lo), s.quantile(hi))


def zscore(s):
    s = pd.to_numeric(s, errors="coerce")
    sd = s.std(ddof=0)
    return (s - s.mean()) / sd if sd and sd > 0 else s * 0


def zw(s):
    return zscore(winsor(s))


def rank(survivors, mode="composite"):
    df = survivors.copy()
    df["cheap_ratio"] = (df["yield_pct"] / df["yield_5y_avg"]).clip(0.5, 2.0).fillna(1.0)
    df["fcf_cover"] = df.apply(
        lambda r: r["fcf"] / (r.get("divs_paid_actual") or (r["mcap_local"] * r["yield_pct"] / 100))
        if r["lane"] == "A" and r["fcf"] and (r.get("divs_paid_actual") or (r["mcap_local"] and r["yield_pct"]))
        else None, axis=1)
    df["wht"] = df["ticker"].map(lambda t: WHT_BY_COUNTRY.get(country(t), 0.20))
    df["net_yield"] = (df["yield_pct"] * (1 - df["wht"])).round(2)
    # growth: robust log-linear slope, fall back to 5y CAGR
    df["growth"] = pd.to_numeric(df.get("div_growth"), errors="coerce").fillna(
        pd.to_numeric(df["div_cagr5"], errors="coerce"))
    # robust quality blend: return metric (ROIC->ROA lane A, ROE lane B) + gross profit + op margin
    df["q_ret"] = df.apply(
        lambda r: (r.get("roic") if pd.notna(r.get("roic")) else r.get("roa"))
        if r["lane"] == "A" else r.get("roe"), axis=1)
    qmat = pd.concat([zw(df["q_ret"]), zw(df["gross_prof"]), zw(df["op_margin"])], axis=1)
    z_qual = qmat.mean(axis=1, skipna=True).fillna(0)
    z_cheap = zw(df["cheap_ratio"])
    z_growth = zw(df["growth"].clip(-0.10, 0.15))
    z_safe = zw(df["fcf_cover"]).fillna(0.0)
    df["score"] = (WEIGHTS["cheap"] * z_cheap + WEIGHTS["quality"] * z_qual
                   + WEIGHTS["growth"] * z_growth.fillna(0) + WEIGHTS["safety"] * z_safe
                   - TAX_TILT * df["wht"])
    if mode == "net_income":
        # income-first: drop melting ice cubes (shrinking dividend), then sort by net yield
        keep = df["growth"].fillna(0) >= 0
        df = df[keep].sort_values("net_yield", ascending=False)
    else:
        df = df.sort_values("score", ascending=False)
    return df


# -------------------------------- CLI --------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", default=str(HERE / "universe.txt"))
    ap.add_argument("--mode", choices=["composite", "net_income"], default="composite")
    ap.add_argument("--max-age", type=int, default=30)
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--rerank", action="store_true")
    ap.add_argument("--top", type=int, default=15)
    args = ap.parse_args()
    max_age = None if args.refresh else (10**6 if args.rerank else args.max_age)

    tickers = [l.strip() for l in Path(args.tickers).read_text().splitlines()
               if l.strip() and not l.startswith("#")]
    print(f"Screening {len(tickers)} tickers...", file=sys.stderr)
    df = apply_gates(pd.DataFrame(fetch_universe(tickers, max_age)))
    ranked = rank(df[df["fails"] == ""], mode=args.mode)
    ranked.to_csv(HERE / f"screen-{date.today().isoformat()}.csv", index=False)

    ranked["mom%"] = (pd.to_numeric(ranked["mom_12m"], errors="coerce") * 100).round(0)
    ranked["roic%"] = (pd.to_numeric(ranked["roic"], errors="coerce") * 100).round(0)
    ranked["grow%"] = (pd.to_numeric(ranked["growth"], errors="coerce") * 100).round(0)
    cols = ["ticker", "name", "sector", "yield_pct", "net_yield", "wht",
            "cheap_ratio", "roic%", "grow%", "mom%", "div_years", "score"]
    pd.set_option("display.width", 200)
    print(f"\n=== SURVIVORS {len(ranked)}/{len(df)}  [mode: {args.mode}]  "
          f"(mcap>=${MIN_MCAP_USD/1e9:.0f}bn, yield {YIELD_MIN}-{YIELD_MAX}%, 10y uncut, "
          f"ROIC/ROA quality, FCF cover, net leverage, trend) ===\n")
    print(ranked[cols].head(args.top).round(3).to_string(index=False))
    print(f"\nBUY CANDIDATES (top 3, {args.mode}): {', '.join(ranked['ticker'].head(3))}")
    rej = df[df["fails"] != ""][["ticker", "fails"]]
    rej.to_csv(HERE / f"rejects-{date.today().isoformat()}.csv", index=False)


if __name__ == "__main__":
    main()
