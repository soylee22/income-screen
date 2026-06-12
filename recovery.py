#!/usr/bin/env python3
"""Recovery-value scanner: the deep-value counterpart to the income screen.

Finds operating companies whose CURRENT earnings are cyclically or temporarily depressed
but whose NORMALIZED (mid-cycle) earning power is intact, trading cheap on those normalized
earnings. Beagles/Temple-Bar style. It deliberately INVERTS the income screen's stance: the
depressed, fallen names that the falling-knife gate rejects are exactly this tool's candidates.

Return thesis (per the transcript): buy at ~3-4x normalized earnings; when earnings normalise
and the multiple re-rates to a normal ~12x, that is a 3-4x re-rating plus the earnings recovery.

Usage:
  python3 recovery.py --topup        # backfill normalized-margin fields (statements, slow)
  python3 recovery.py                # scan from the store
  python3 recovery.py --top 25
"""
import argparse
import json
import sys
import time
from datetime import date

import pandas as pd
import yfinance as yf
import screen as S

# ======================== CONFIG ========================
MIN_MCAP_USD = 5e9            # recovery lives smaller than blue-chip income; lower floor
MIN_NORM_MARGIN = 0.05       # it must be a real business in a normal year (not perma-junk)
DEPRESSED_MAX = 0.85         # current margin must be < 85% of normalized (genuinely depressed)
MAX_NET_LEV = 5.0            # survival floor (looser than income; depressed EBITDA inflates it)
TARGET_MULTIPLE = 12.0       # the "normal" earnings multiple a recovered business re-rates to
MAX_NORM_PE = 12.0           # must be cheap on NORMALIZED earnings to have upside
MOM_FLOOR = -0.60            # exclude the actively-collapsing (down >60% in 12m = maybe broken)
# ========================================================


def _series(inc, *names):
    for n in names:
        if inc is not None and n in inc.index:
            s = pd.to_numeric(inc.loc[n], errors="coerce").dropna()
            if len(s):
                return s
    return None


def margins_from_stmt(t):
    """(norm_margin, cur_margin, revenue_ttm). norm = winsorised MEDIAN operating margin over
    the available years (median is robust to one-off disposal-gain years that wreck the mean)."""
    try:
        inc = t.income_stmt
        rev = _series(inc, "Total Revenue")
        ebit = _series(inc, "EBIT", "Operating Income")
        if rev is None or ebit is None:
            return None, None, None
        m = (ebit / rev).dropna()
        if len(m) < 2:
            return None, None, None
        norm = float(m.clip(m.quantile(0.1), m.quantile(0.9)).median())
        return norm, float(m.iloc[0]), float(rev.iloc[0])
    except Exception:
        return None, None, None


def topup(force=False):
    store = S.load_store()
    items = [(s, r) for s, r in store.items()
             if "ticker" in r and r.get("sector") not in S.FINANCIAL_SECTORS]
    n = 0
    for i, (sym, row) in enumerate(items, 1):
        if not force and row.get("rec_norm_margin") is not None:
            continue
        try:
            nm, cm, rev = margins_from_stmt(yf.Ticker(sym))
            row["rec_norm_margin"], row["rec_cur_margin"], row["rec_revenue"] = nm, cm, rev
            n += 1
            if n % 25 == 0:
                S.STORE.write_text(json.dumps(store))
                print(f"  [{i}/{len(items)}] {n} done", file=sys.stderr)
        except Exception as e:
            print(f"  {sym} fail: {e}", file=sys.stderr)
        time.sleep(0.4)
    S.STORE.write_text(json.dumps(store))
    print(f"topup done: {n}/{len(items)}", file=sys.stderr)


def scan():
    store = S.load_store()
    rows = [r for r in store.values()
            if "ticker" in r and r.get("sector") not in S.FINANCIAL_SECTORS]
    df = pd.DataFrame(rows)
    rates = S.fx_rates(df["currency"])
    df["mcap_usd"] = df.apply(lambda r: (r.get("mcap_local") or 0) * (rates.get(r["currency"]) or 0), axis=1)

    def metrics(r):
        nm, cm, rev, mc = r.get("rec_norm_margin"), r.get("rec_cur_margin"), r.get("rec_revenue"), r.get("mcap_local")
        if not nm or not rev or not mc or nm <= 0:
            return pd.Series({"norm_earn": None, "norm_pe": None, "upside": None,
                              "margin_gap": None, "net_lev": None})
        norm_earn = nm * rev * 0.79                       # normalized op profit, ~21% tax
        norm_pe = mc / norm_earn if norm_earn > 0 else None
        upside = (norm_earn * TARGET_MULTIPLE / mc - 1) if norm_earn > 0 else None
        margin_gap = (cm / nm) if (cm is not None and nm) else None
        ebitda, debt, cash = r.get("ebitda"), r.get("total_debt"), (r.get("cash") or 0)
        net_lev = ((debt - cash) / ebitda) if (debt is not None and ebitda and ebitda > 0) else None
        return pd.Series({"norm_earn": norm_earn, "norm_pe": norm_pe, "upside": upside,
                          "margin_gap": margin_gap, "net_lev": net_lev})

    df = pd.concat([df, df.apply(metrics, axis=1)], axis=1)

    def gate(r):
        f = []
        if r["mcap_usd"] < MIN_MCAP_USD: f.append("mcap")
        if not r.get("rec_norm_margin") or r["rec_norm_margin"] < MIN_NORM_MARGIN: f.append("norm_margin<5%")
        if r["norm_earn"] is None or r["norm_earn"] <= 0: f.append("no_norm_earn")
        if r["margin_gap"] is None or r["margin_gap"] >= DEPRESSED_MAX: f.append("not_depressed")
        if r["net_lev"] is not None and r["net_lev"] > MAX_NET_LEV: f.append("leverage")
        if r["norm_pe"] is None or r["norm_pe"] > MAX_NORM_PE: f.append("not_cheap")
        if r.get("mom_12m") is not None and r["mom_12m"] < MOM_FLOOR: f.append("collapsing")
        if r.get("gross_margin") is not None and r["gross_margin"] <= 0: f.append("franchise")
        return ",".join(f)

    df["fails"] = df.apply(gate, axis=1)
    surv = df[df["fails"] == ""].sort_values("upside", ascending=False)
    return df, surv


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--topup", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--top", type=int, default=20)
    args = ap.parse_args()
    if args.topup:
        topup(force=args.force)
        return
    df, surv = scan()
    s = surv.copy()
    s["norm%"] = (pd.to_numeric(s["rec_norm_margin"], errors="coerce") * 100).round(1)
    s["cur%"] = (pd.to_numeric(s["rec_cur_margin"], errors="coerce") * 100).round(1)
    s["up%"] = (pd.to_numeric(s["upside"], errors="coerce") * 100).round(0)
    cols = ["ticker", "name", "sector", "norm%", "cur%", "norm_pe", "net_lev", "up%"]
    pd.set_option("display.width", 200)
    print(f"\n=== RECOVERY CANDIDATES {len(surv)}/{len(df)} "
          f"(mcap>=${MIN_MCAP_USD/1e9:.0f}bn, norm margin>={MIN_NORM_MARGIN*100:.0f}%, currently "
          f"depressed <{DEPRESSED_MAX*100:.0f}% of normal, survivable, cheap on normalized) ===")
    print("upside = normalized earnings x12 / price - 1 (earnings recovery + re-rating to ~12x)\n")
    print(s[cols].head(args.top).round(2).to_string(index=False))
    print(f"\nTOP RECOVERY PLAYS: {', '.join(surv['ticker'].head(3))}")


if __name__ == "__main__":
    main()
