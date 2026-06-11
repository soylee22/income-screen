#!/usr/bin/env python3
"""Diagnostics + sensitivity for the income screen, all from store.json (no fetch)."""
import json
from pathlib import Path
import pandas as pd
import screen as S

HERE = Path(__file__).parent
store = json.loads((HERE / "store.json").read_text())
rows = [r for r in store.values() if "ticker" in r]
base = pd.DataFrame(rows)


def country(tk):
    if tk.endswith(".L"): return "UK"
    if tk.endswith(".PA"): return "FR"
    if tk.endswith(".DE"): return "DE"
    if tk.endswith((".SW",)): return "CH"
    if tk.endswith(".HK"): return "HK"
    return "US"


def run_gates(df):
    d = S.apply_gates(df.copy())
    return d


d = run_gates(base)
d["country"] = d["ticker"].map(country)
surv = d[d["fails"] == ""]
ranked = S.rank(surv)

print("=== SURVIVORS BY COUNTRY ===")
tab = pd.DataFrame({
    "in_universe": d.groupby("country").size(),
    "survivors": surv.groupby("country").size(),
}).fillna(0).astype(int)
tab["rate"] = (tab["survivors"] / tab["in_universe"] * 100).round(1)
print(tab.to_string())

print("\n=== SURVIVORS BY SECTOR ===")
print(surv.groupby("sector").size().sort_values(ascending=False).to_string())

print("\n=== UK NAMES: why excluded ===")
uk = d[d["country"] == "UK"][["ticker", "yield_pct", "roe", "div_years", "fails"]]
print(uk[uk["fails"] != ""].to_string(index=False))

print("\n=== SPECIFIC NAMES (banks/insurers/oils) ===")
watch = ["HSBA.L", "AV.L", "LGEN.L", "LLOY.L", "BARC.L", "NWG.L", "STAN.L",
         "SHEL.L", "BP.L", "GIS", "CAP.PA", "ACN"]
w = d[d["ticker"].isin(watch)][["ticker", "yield_pct", "yield_5y_avg", "roe",
                                 "div_years", "fails"]]
print(w.to_string(index=False))

print("\n=== REJECT-REASON HISTOGRAM ===")
from collections import Counter
c = Counter()
for f in d[d["fails"] != ""]["fails"]:
    for reason in f.split(","):
        c[reason] += 1
for reason, n in c.most_common():
    print(f"  {reason:14s} {n}")

print("\n=== SENSITIVITY (survivor count + top-10 stability vs baseline) ===")
base_top = list(ranked["ticker"].head(10))


def variant(**kw):
    saved = {k: getattr(S, k) for k in kw}
    for k, v in kw.items():
        setattr(S, k, v)
    dd = S.apply_gates(base.copy())
    ss = dd[dd["fails"] == ""]
    rr = S.rank(ss)
    top = list(rr["ticker"].head(10))
    jac = len(set(top) & set(base_top)) / len(set(top) | set(base_top))
    for k, v in saved.items():
        setattr(S, k, v)
    return len(ss), round(jac, 2)


print(f"  BASELINE: {len(surv)} survivors, top10 = {base_top}")
for label, kw in [
    ("yield_min 2.5", {"YIELD_MIN": 2.5}), ("yield_min 3.5", {"YIELD_MIN": 3.5}),
    ("yield_max 7", {"YIELD_MAX": 7.0}), ("yield_max 9", {"YIELD_MAX": 9.0}),
    ("roe 0.08", {"MIN_ROE": 0.08}), ("roe 0.12", {"MIN_ROE": 0.12}),
    ("fcf 1.0", {"MIN_FCF_COVER": 1.0}), ("fcf 1.5", {"MIN_FCF_COVER": 1.5}),
    ("debt/ebitda 3", {"MAX_DEBT_EBITDA": 3.0}), ("debt/ebitda 5", {"MAX_DEBT_EBITDA": 5.0}),
    ("years 8", {"MIN_DIV_YEARS": 8}), ("years 15", {"MIN_DIV_YEARS": 15}),
    ("mcap 5bn", {"MIN_MCAP_USD": 5e9}), ("mcap 25bn", {"MIN_MCAP_USD": 25e9}),
]:
    n, jac = variant(**kw)
    print(f"  {label:18s} -> {n:3d} survivors, top10 overlap {jac}")
