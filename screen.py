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
MIN_FFO_COVER = 1.05                      # FFO / dividends paid, lane R (REITs). ROE is a
# category error for REITs: property depreciation crushes GAAP net income (so ROE), even
# though buildings rarely lose real value. NAREIT's standard is FFO (net income + real-estate
# D&A). A REIT that covers its dividend out of FFO is durable; ROE >= 8% would reject almost
# every quality REIT (Realty Income's ROE is ~3%).
MAX_NET_LEVERAGE = 4.0                    # (total debt - cash) / EBITDA, lane A
MAX_NET_LEVERAGE_UTIL = 6.5              # relaxed for regulated utilities: contracted/rate-
# regulated cash flows safely support higher leverage (the asset-light 4x cap is a category
# error for them, like ROE was for REITs). Applies to sector == Utilities.
MIN_OCF_COVER_UTIL = 1.5                 # operating cash flow / dividends, utilities. FCF-after-
# growth-capex is the wrong metric (utilities fund dividends from OCF and finance the rate-base
# build separately), and ROIC is depressed by the vast regulated asset base, so utilities are
# gated on OCF coverage + the relaxed leverage ceiling + the 10y uncut record, not ROIC/FCF.
UTILITY_SECTORS = {"Utilities"}
# Just-MAIN BDC inclusion (Lee): a BDC is a leveraged portfolio of private-company loans/equity,
# scored on ROE (which IS meaningful for a BDC, unlike a REIT) but it needs a lower size floor
# and a wider yield band than blue-chip operating cos. P/NAV (price/book) shown for the premium.
BDC_TICKERS = {"MAIN"}
BDC_MIN_MCAP = 2e9
BDC_YIELD_MAX = 12.0
MOM_12M_MIN = -0.10                       # falling-knife: drop names down >10% over 12m
MOM_VS_200D_MIN = -0.12                   # or sitting >12% below their 200-day average

# ----- Dividend-GROWTH screen (the second tab): a different strategy from income. Targets
# low-payout, fast-growing compounders (the quality/dividend-growth premium), not high-yield
# cash cows. See wiki dividend-growth-premium-evidence. Yield is NOT the point; the runway is.
GROWTH_MIN_MCAP = 2e9        # lower floor than income ($15bn) so mid-cap growers appear
GROWTH_YIELD_MIN = 1.0       # must pay a dividend, but a low yield is fine (it's a grower)
GROWTH_YIELD_MAX = 6.0       # above this for a "grower" is a yield-trap red flag
GROWTH_PAYOUT_CAP = 0.85     # the runway gate: low payout = room to keep raising. Kills the
                             # streaks-on-borrowed-time (Croda ~250%, Telus ~277% payout)
GROWTH_MIN_STREAK = 7        # minimum consecutive annual raises (the proven-grower bar)
GROWTH_MIN_DGR = 0.03        # minimum 5-year dividend-growth CAGR (must actually be growing)
GROWTH_STREAK_FULL = 15      # raise streak (yrs) at which length credibility maxes out
MIN_NI_CAGR = -0.10                       # earnings-freefall gate (lane A): drop only names whose
                                          # net income has fallen >10%/yr over the cycle. Profit not
                                          # revenue, so tobacco/staples cash-machines (flat revenue,
                                          # rising earnings via pricing/buybacks) are spared.

# Ranking: Gordon/DDM expected return = net yield + sustainable growth + cross-sectional
# reversion. Growth is capped (DMS: long-run real div growth ~1.8%/yr, so high growth
# mean-reverts and must not be trusted at face value) and haircut by quality.
GROWTH_CAP = 0.08            # never credit more than 8% sustainable dividend growth
GROWTH_FLOOR = -0.03         # floor a mild decline (gates already require uncut record)
QUAL_HAIRCUT_REF = 0.15      # ROIC/ROE at which positive growth gets full credit
STREAK_FULL = 10             # consecutive annual raises for full growth credibility
STREAK_FLOOR = 0.6           # growth-credit multiplier for a zero/broken raise streak
# Streak weights growth CREDIBILITY, not return: a high div_growth off a short streak is
# rebound-from-a-cut (Aviva-type) and gets discounted; a clean raiser keeps full credit.
# Multiplicative (not an additive pp bonus) so exp_return stays an honest return estimate
# and we don't stack a style-biased term on top of the quality haircut.
REVERSION_PP = 0.9           # max cross-sectional cheapness tilt (pp), kept modest
# GFC resilience: penalty (pp) to expected return by 2008-09 dividend behaviour (trough
# 2009/10 vs peak 2007/08). We cannot get CET1/Solvency II from Yahoo, but the dividend's
# actual crisis survival is the outcome those capital ratios are meant to predict.
# NOTE: a 2008-GFC dividend-fragility haircut was trialled then REMOVED (2026-06-12). It was
# 18 years old under a since-changed bank-capital regime, redundant with the recent 2020/PRA
# signal already in the gates, and the European 2008 dividend data in yfinance was unreliable
# (Allianz missing, Credit Agricole/AXA glitched). gfc_ratio is still stored for reference but
# is NOT used in scoring. Financials concentration is handled by the sector cap at allocation.

# Dividend withholding tax for a UK ISA investor (passive, no reclaim). UK/HK/IE 0;
# US 15% (W-8BEN; 0% in a SIPP); EU ~26%; Switzerland 35%; Japan 10% (treaty).
WHT_BY_COUNTRY = {"UK": 0.0, "HK": 0.0, "IE": 0.0, "US": 0.15, "NL": 0.15, "JP": 0.10,
                  "CA": 0.15, "ES": 0.19, "DE": 0.26, "FR": 0.26, "IT": 0.26, "BE": 0.30,
                  "CH": 0.35}
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
                   (".MC", "ES"), (".MI", "IT"), (".AS", "NL"), (".TO", "CA"), (".T", "JP")]:
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


def gfc_ratio(yearly):
    """Dividend resilience through the 2008-09 crisis: trough(2009,2010) / peak(2007,2008).
    1.0 = held, <0.6 = cut >40%, ~0 = slashed to nothing. None = no pre-crisis history."""
    if yearly is None or yearly.empty:
        return None
    pre = max(yearly.get(2007, 0), yearly.get(2008, 0))
    if pre <= 0:
        return None
    return min(yearly.get(2009, 0), yearly.get(2010, 0)) / pre


def raise_streak(yearly):
    """Consecutive annual dividend INCREASES ending at the latest complete year.

    The costly, forward-looking signalling event (Bhattacharya 1979; Miller-Rock 1985):
    a board only raises if it believes the cash flow is durable, because a later cut is
    punished hard. This is distinct from merely not cutting (uncut_10y gate) and is
    empirically orthogonal to div_growth/div_years in our universe (r<0.3), so it carries
    new information. Used in rank() to weight growth CREDIBILITY: rebound growth off a cut
    (short streak) is discounted; a clean raiser keeps full credit.

    Bridges the 2020/21 PRA-forced cuts the same way div_record does, so a regulator-banned
    dividend (UK banks/insurers in 2020) does not reset an otherwise unbroken raiser."""
    if yearly is None or yearly.empty:
        return 0
    y = yearly[yearly > 0]
    if len(y) < 2:
        return 0
    idx, vals = list(y.index), list(y.values)
    recovered = (2019 in y.index and y.loc[2019] > 0 and vals[-1] >= 0.9 * y.loc[2019])
    streak = 0
    for i in range(len(vals) - 1, 0, -1):
        if vals[i] > vals[i - 1] * 1.001:                # genuine raise (1% tol absorbs FX noise)
            streak += 1
        elif idx[i] in (2020, 2021) and recovered:       # bridge the COVID/PRA dip
            continue
        else:
            break
    return streak


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


def _series(df, *names):
    """Full row series (all years) for the first present line item."""
    if df is None or df.empty:
        return None
    for n in names:
        if n in df.index:
            s = pd.to_numeric(df.loc[n], errors="coerce").dropna()
            if len(s):
                return s
    return None


def margins_from_income(inc):
    """(normalized, current) operating margin. Normalized = winsorised MEDIAN op margin over
    the available years (robust to one-off disposal-gain years). Informational context only:
    is this name's current earnings depressed or elevated vs its own normal?"""
    rev, ebit = _series(inc, "Total Revenue"), _series(inc, "EBIT", "Operating Income")
    if rev is None or ebit is None:
        return None, None
    m = (ebit / rev).dropna()
    if len(m) < 2:
        return None, None
    return float(m.clip(m.quantile(0.1), m.quantile(0.9)).median()), float(m.iloc[0])


def growth_from_income(inc):
    """(revenue CAGR, net-income CAGR) over the available years, ~3yr from Yahoo's 4 columns.
    The business-growth gate: is the dividend growing on a growing business or a shrinking one?"""
    def cagr(s):
        if s is None or len(s) < 2:
            return None
        old, new = float(s.iloc[-1]), float(s.iloc[0])     # iloc[-1] oldest, iloc[0] newest
        if old <= 0 or new <= 0:
            return None
        return (new / old) ** (1 / (len(s) - 1)) - 1
    return cagr(_series(inc, "Total Revenue")), \
        cagr(_series(inc, "Net Income", "Net Income Common Stockholders"))


def fcf_from_statement(cf):
    fcf = divs_paid = ocf = None
    if cf is not None and not cf.empty:
        ocf = _row(cf, "Operating Cash Flow")
        fcf = _row(cf, "Free Cash Flow")
        if fcf is None and ocf is not None:
            capex = _row(cf, "Capital Expenditure")
            if capex is not None:
                fcf = ocf + capex
        dp = _row(cf, "Cash Dividends Paid")
        divs_paid = abs(dp) if dp is not None else None
    return fcf, divs_paid, ocf


def ffo_from_statements(inc, cf):
    """REIT Funds From Operations ~= net income + real-estate D&A (NAREIT standard, gains on
    sale ignored). Returns (ffo, dividends_paid). The correct dividend-durability metric for a
    REIT, in place of ROE/net income which depreciation distorts."""
    ni = _row(inc, "Net Income", "Net Income Common Stockholders")
    da = _row(cf, "Depreciation And Amortization", "Depreciation Amortization Depletion",
              "Depreciation Amortization Depletion Non Cash Adjustment", "Depreciation")
    dp = _row(cf, "Cash Dividends Paid")
    ffo = (ni + da) if (ni is not None and da is not None) else None
    return ffo, (abs(dp) if dp is not None else None)


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
    fcf = divs_paid_actual = roic = gross_prof = cash = ffo = ocf = None
    norm_margin = cur_margin = rev_cagr = ni_cagr = None
    if sector == "Real Estate":                          # REITs: pull statements for FFO
        try:
            ffo, divs_paid_actual = ffo_from_statements(t.income_stmt, t.cashflow)
        except Exception as e:
            print(f"    {symbol} FFO fetch partial: {e}", file=sys.stderr)
    elif sector not in FINANCIAL_SECTORS:                # operating cos: pull statements
        try:
            inc = t.income_stmt
            fcf, divs_paid_actual, ocf = fcf_from_statement(t.cashflow)
            roic, gross_prof, cash = roic_from_statements(inc, t.balance_sheet)
            norm_margin, cur_margin = margins_from_income(inc)
            rev_cagr, ni_cagr = growth_from_income(inc)
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
        "rec_norm_margin": norm_margin, "rec_cur_margin": cur_margin,
        "rev_cagr": rev_cagr, "ni_cagr": ni_cagr,
        "fcf": fcf, "divs_paid_actual": divs_paid_actual, "cash": cash, "ocf": ocf,
        "ffo": ffo, "price_to_book": info.get("priceToBook"),
        "total_debt": info.get("totalDebt"), "ebitda": info.get("ebitda"),
        "div_years": years, "uncut_10y": uncut, "gfc_ratio": gfc_ratio(yearly),
        "div_cagr5": cagr5, "div_growth": robust_div_growth(yearly),
        "div_streak": raise_streak(yearly),
        "earnings_growth": info.get("earningsGrowth"), "revenue_growth": info.get("revenueGrowth"),
        "trailing_pe": info.get("trailingPE"), "forward_pe": info.get("forwardPE"),
        "beta": info.get("beta"),
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
                 "roic", "gross_prof", "rec_norm_margin", "rec_cur_margin",
                 "rev_cagr", "ni_cagr", "fcf", "divs_paid_actual", "cash", "ocf", "ffo",
                 "price_to_book", "total_debt",
                 "ebitda", "div_years", "uncut_10y", "gfc_ratio", "div_cagr5", "div_growth",
                 "div_streak",
                 "earnings_growth", "revenue_growth", "trailing_pe", "forward_pe", "beta",
                 "mom_12m", "px", "sma200", "wk52high"]


def apply_gates(df):
    for c in EXPECTED_COLS:                  # robust to any store schema (old/partial)
        if c not in df.columns:
            df[c] = None
    rates = fx_rates(df["currency"])
    df["mcap_usd"] = df.apply(lambda r: (r["mcap_local"] or 0) * (rates.get(r["currency"]) or 0), axis=1)
    df["lane"] = df["sector"].map(
        lambda s: "R" if s == "Real Estate" else ("B" if s in FINANCIAL_SECTORS else "A"))
    # FCF yield + sector medians (for cross-sectional, evidence-backed cheapness)
    df["fcf_yield"] = df.apply(
        lambda r: r["fcf"] / r["mcap_local"] if (r.get("fcf") and r.get("mcap_local")) else None, axis=1)
    pay = df[pd.to_numeric(df["yield_pct"], errors="coerce") > 0]
    med_y = pay.groupby("sector")["yield_pct"].median().to_dict()
    med_fy = pay.assign(_fy=pd.to_numeric(pay["fcf_yield"], errors="coerce")) \
        .groupby("sector")["_fy"].median().to_dict()
    df["sec_med_yield"] = df["sector"].map(med_y)
    df["sec_med_fcfy"] = df["sector"].map(med_fy)

    def quality_ok(r):
        if r["lane"] == "R":                                 # REITs: FFO dividend coverage
            ffo, dp = _safe(r.get("ffo")), _safe(r.get("divs_paid_actual"))
            if ffo is None or not dp or dp <= 0:
                return False                                  # no FFO data = cannot verify, reject
            return ffo / dp >= MIN_FFO_COVER
        if r["lane"] == "A":
            roic, roa, roe = r.get("roic"), r.get("roa"), r.get("roe")
            opm = r.get("op_margin")
            if opm is not None and opm <= 0:                 # must be operationally profitable
                return False
            if r["sector"] in UTILITY_SECTORS:               # utilities: OCF cover, not ROIC
                return True                                  # (durability enforced by ocf_cover gate)
            if roic is not None and not pd.isna(roic):
                return roic >= MIN_ROIC_A or (roa is not None and roa >= MIN_ROA_A)
            if roa is not None and not pd.isna(roa):
                return roa >= MIN_ROA_A
            return roe is not None and not pd.isna(roe) and roe >= MIN_ROE_FALLBACK
        return r.get("roe") is not None and not pd.isna(r.get("roe")) and r["roe"] >= MIN_ROE_B

    def gate(r):
        fails = []
        is_bdc = r["ticker"] in BDC_TICKERS
        mcap_floor = BDC_MIN_MCAP if is_bdc else MIN_MCAP_USD
        ymax = BDC_YIELD_MAX if is_bdc else YIELD_MAX
        if r["mcap_usd"] < mcap_floor: fails.append("mcap")
        y = r["yield_pct"]
        if y is None or not (YIELD_MIN <= y <= ymax): fails.append("yield_band")
        if r["div_years"] < MIN_DIV_YEARS: fails.append("record<10y")
        if not r["uncut_10y"]: fails.append("cut_in_10y")
        if not quality_ok(r): fails.append("quality")
        if r["lane"] == "A":
            divs_paid = r.get("divs_paid_actual") or (r["mcap_local"] or 0) * (y or 0) / 100
            if r["sector"] in UTILITY_SECTORS:               # OCF cover (FCF-after-capex is wrong)
                ocf = r.get("ocf")
                if not ocf or divs_paid <= 0 or ocf / divs_paid < MIN_OCF_COVER_UTIL:
                    fails.append("ocf_cover")
            elif not r["fcf"] or divs_paid <= 0 or r["fcf"] / divs_paid < MIN_FCF_COVER:
                fails.append("fcf_cover")
            ebitda = r.get("ebitda")
            if ebitda and ebitda > 0 and r.get("total_debt") is not None:
                net_debt = r["total_debt"] - (r.get("cash") or 0)
                lev_cap = MAX_NET_LEVERAGE_UTIL if r["sector"] in UTILITY_SECTORS else MAX_NET_LEVERAGE
                if net_debt / ebitda > lev_cap: fails.append("leverage")
            # earnings-freefall gate: drop only genuine multi-year earnings deterioration (the
            # dividend's backing is collapsing). Profit not revenue; lenient on missing data.
            nc = r.get("ni_cagr")
            if nc is not None and not pd.isna(nc) and nc < MIN_NI_CAGR:
                fails.append("earnings_freefall")
        mom, px, sma = r.get("mom_12m"), r.get("px"), r.get("sma200")
        if (mom is not None and mom < MOM_12M_MIN) or (px and sma and (px / sma - 1) < MOM_VS_200D_MIN):
            fails.append("downtrend")
        return ",".join(fails)

    df["fails"] = df.apply(gate, axis=1)
    df["quality_pass"] = df.apply(quality_ok, axis=1)     # exposed for the growth screen
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


def _safe(v, default=None):
    return float(v) if v is not None and not pd.isna(v) else default


def _sustainable_growth(r):
    """Historical dividend growth, blended with a forward earnings estimate, then
    capped and floored. Returns the RAW (pre-quality-haircut) sustainable growth."""
    g = _safe(r.get("div_growth"), _safe(r.get("div_cagr5"), 0.0))
    eg = _safe(r.get("earnings_growth"))
    if eg is not None:                                   # forward sanity blend
        g = 0.7 * g + 0.3 * min(max(eg, -0.05), 0.12)
    return min(max(g, GROWTH_FLOOR), GROWTH_CAP)


def rank(survivors, mode="expret"):
    """Rank survivors by Gordon/DDM expected return (default) or by net income.

    expected_return (%) = net_yield + quality-haircut sustainable growth + cross-sectional
    reversion. All terms in percentage points, so the score is an interpretable estimate.
    """
    df = survivors.copy()
    df["wht"] = df["ticker"].map(lambda t: WHT_BY_COUNTRY.get(country(t), 0.20))
    df["net_yield"] = (df["yield_pct"] * (1 - df["wht"])).round(2)
    df["cheap_ratio"] = (df["yield_pct"] / df["yield_5y_avg"]).clip(0.5, 2.0).fillna(1.0)
    df["fcf_cover"] = df.apply(
        lambda r: r["fcf"] / (r.get("divs_paid_actual") or (r["mcap_local"] * r["yield_pct"] / 100))
        if r["lane"] == "A" and r["fcf"] and (r.get("divs_paid_actual") or (r["mcap_local"] and r["yield_pct"]))
        else None, axis=1)

    df["g_raw"] = df.apply(_sustainable_growth, axis=1)

    def quality_factor(r):                               # 0.5..1.0, scales positive growth
        if r["lane"] == "R" or r["sector"] in UTILITY_SECTORS:   # ROE/ROIC meaningless; neutral
            return 0.7
        q = r.get("roic") if (r["lane"] == "A" and pd.notna(r.get("roic"))) else r.get("roe")
        q = _safe(q)
        return 0.7 if q is None else min(max(0.5 + 0.5 * (q / QUAL_HAIRCUT_REF), 0.5), 1.0)

    df["qf"] = df.apply(quality_factor, axis=1)

    def streak_factor(r):                                # 0.6..1.0, scales positive growth
        s = _safe(r.get("div_streak"), 0.0)
        return min(max(STREAK_FLOOR + (1 - STREAK_FLOOR) * s / STREAK_FULL, STREAK_FLOOR), 1.0)

    df["sf"] = df.apply(streak_factor, axis=1)
    df["g_sust"] = df.apply(
        lambda r: r["g_raw"] * r["qf"] * r["sf"] if r["g_raw"] > 0 else r["g_raw"], axis=1)

    # Cross-sectional reversion: cheapness relative to the OTHER candidates (not the whole
    # sector, against which every survivor looks cheap). FCF yield for operating cos, dividend
    # yield for financials; demeaned within lane so it is a modest, centred re-rating tilt.
    def _val_for(r):                                     # utilities have no FCF-yield cheapness
        if r["sector"] in UTILITY_SECTORS:               # signal (capex-heavy), so no tilt
            return None
        return r.get("fcf_yield") if r["lane"] == "A" else r.get("yield_pct")
    df["_val"] = pd.to_numeric(df.apply(_val_for, axis=1), errors="coerce")

    def _lane_z(s):
        sd = s.std(ddof=0)
        return (s - s.mean()) / sd if sd and sd > 0 else s * 0

    df["reversion"] = (df.groupby("lane", group_keys=False)["_val"].apply(_lane_z)
                       .clip(-1.5, 1.5) * (REVERSION_PP / 1.5)).round(2).fillna(0.0)
    df["exp_return"] = (df["net_yield"] + df["g_sust"] * 100 + df["reversion"]).round(2)
    df["score"] = df["exp_return"]
    df["growth"] = df["g_raw"]                            # display alias

    if mode == "net_income":
        df = df[df["g_raw"] >= 0].sort_values("net_yield", ascending=False)
    else:
        df = df.sort_values("exp_return", ascending=False)
    return df


# --------------------------- dividend-GROWTH screen ---------------------------
def knife_flag(r):
    """Falling-knife / value-trap badge (shown, not auto-excluded, in the growth screen):
    a quality grower temporarily cheap is an opportunity, a collapsing one is a trap."""
    px, sma, mom = _safe(r.get("px")), _safe(r.get("sma200")), _safe(r.get("mom_12m"))
    below = (px / sma - 1) if (px and sma and sma > 0) else None
    if (below is not None and below < -0.20) or (mom is not None and mom < -0.20):
        return "KNIFE"                                    # severe: deep below 200d / -20% 12m
    if (below is not None and below < -0.02) or (mom is not None and mom < -0.10):
        return "below200d"                                # mild: cheap, possible value entry
    return ""


def apply_growth_gates(df):
    """Gate for the dividend-GROWTH screen. Assumes apply_gates has already run (lane,
    mcap_usd, quality_pass present). Targets low-payout fast growers, not income."""
    def ggate(r):
        f = []
        if _safe(r.get("mcap_usd"), 0) < GROWTH_MIN_MCAP: f.append("mcap")
        y = _safe(r.get("yield_pct"))
        if y is None or not (GROWTH_YIELD_MIN <= y <= GROWTH_YIELD_MAX): f.append("yield_band")
        if _safe(r.get("div_years"), 0) < MIN_DIV_YEARS: f.append("record<10y")
        if not r.get("uncut_10y"): f.append("cut_in_10y")
        if _safe(r.get("div_streak"), 0) < GROWTH_MIN_STREAK: f.append(f"streak<{GROWTH_MIN_STREAK}y")
        dgr = _safe(r.get("div_cagr5"), _safe(r.get("div_growth")))
        if dgr is None or dgr < GROWTH_MIN_DGR: f.append("low_growth")
        po = _safe(r.get("payout_ratio"))
        if po is None or po > GROWTH_PAYOUT_CAP or po <= 0: f.append("payout")
        if not r.get("quality_pass"): f.append("quality")
        return ",".join(f)
    df = df.copy()
    df["gfails"] = df.apply(ggate, axis=1)
    df["knife"] = df.apply(knife_flag, axis=1)
    return df


def rank_growth(df):
    """Rank growth survivors by the Chowder number (net yield + 5y dividend-growth CAGR, the
    standard dividend-growth metric and a Gordon total-return proxy), weighted by raise-streak
    length so a long proven grower outranks a short one with the same Chowder."""
    s = df[df["gfails"] == ""].copy()
    s["wht"] = s["ticker"].map(lambda t: WHT_BY_COUNTRY.get(country(t), 0.20))
    s["net_yield"] = (s["yield_pct"] * (1 - s["wht"])).round(2)
    s["dgr5"] = (pd.to_numeric(s["div_cagr5"], errors="coerce") * 100).round(1)
    s["chowder"] = (s["net_yield"] + s["dgr5"]).round(2)
    s["streak_cred"] = s["div_streak"].map(
        lambda v: 0.7 + 0.3 * min(_safe(v, 0) / GROWTH_STREAK_FULL, 1.0))
    s["growth_score"] = (s["chowder"] * s["streak_cred"]).round(2)
    return s.sort_values("growth_score", ascending=False)


# -------------------------------- CLI --------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", default=str(HERE / "universe.txt"))
    ap.add_argument("--mode", choices=["expret", "net_income"], default="expret")
    ap.add_argument("--yield-min", type=float, default=None,
                    help="override the yield floor (default 3.0); e.g. 1.5 to include lower-yield quality")
    ap.add_argument("--max-age", type=int, default=30)
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--rerank", action="store_true")
    ap.add_argument("--top", type=int, default=15)
    args = ap.parse_args()
    if args.yield_min is not None:
        global YIELD_MIN
        YIELD_MIN = args.yield_min
    max_age = None if args.refresh else (10**6 if args.rerank else args.max_age)

    tickers = [l.strip() for l in Path(args.tickers).read_text().splitlines()
               if l.strip() and not l.startswith("#")]
    print(f"Screening {len(tickers)} tickers...", file=sys.stderr)
    df = apply_gates(pd.DataFrame(fetch_universe(tickers, max_age)))
    ranked = rank(df[df["fails"] == ""], mode=args.mode)
    ranked.to_csv(HERE / f"screen-{date.today().isoformat()}.csv", index=False)

    ranked["roic%"] = (pd.to_numeric(ranked["roic"], errors="coerce") * 100).round(0)
    ranked["gsust%"] = (pd.to_numeric(ranked["g_sust"], errors="coerce") * 100).round(1)
    cols = ["ticker", "name", "sector", "lane", "net_yield", "roic%", "gsust%", "reversion",
            "exp_return", "div_years", "div_streak"]
    pd.set_option("display.width", 210)
    print(f"\n=== SURVIVORS {len(ranked)}/{len(df)}  [mode: {args.mode}]  "
          f"(mcap>=${MIN_MCAP_USD/1e9:.0f}bn, yield {YIELD_MIN}-{YIELD_MAX}%, 10y uncut, "
          f"quality: lane A ROIC/ROA + FCF cover + leverage, lane B ROE, lane R FFO cover; "
          f"trend) ===")
    print("exp_return = net_yield + quality-haircut growth + cross-sectional reversion (pp)\n")
    print(ranked[cols].head(args.top).round(2).to_string(index=False))
    print(f"\nBUY CANDIDATES (top 3, {args.mode}): {', '.join(ranked['ticker'].head(3))}")
    rej = df[df["fails"] != ""][["ticker", "fails"]]
    rej.to_csv(HERE / f"rejects-{date.today().isoformat()}.csv", index=False)


if __name__ == "__main__":
    main()
