#!/usr/bin/env python3
"""Generate a self-contained GitHub-Pages site for the income screen.

Reads store.json, re-applies gates + ranking + sensitivity, emits docs/index.html
(Palantir-style, vanilla-JS searchable/sortable table, no external deps)."""
import json
from datetime import date
from pathlib import Path

import pandas as pd
import screen as S

HERE = Path(__file__).parent
DOCS = HERE / "docs"
DOCS.mkdir(exist_ok=True)


def country(tk):
    for sfx, c in [(".L", "UK"), (".PA", "FR"), (".DE", "DE"), (".SW", "CH"), (".HK", "HK")]:
        if tk.endswith(sfx):
            return c
    return "US"


store = json.loads((HERE / "store.json").read_text())
rows = [r for r in store.values() if "ticker" in r]
df = S.apply_gates(pd.DataFrame(rows))
df["country"] = df["ticker"].map(country)
surv = df[df["fails"] == ""]
ranked = S.rank(surv)
rank_map = {t: i + 1 for i, t in enumerate(ranked["ticker"])}
score_map = dict(zip(ranked["ticker"], ranked["score"]))
cheap_map = dict(zip(ranked["ticker"], ranked["cheap_ratio"]))

fetched_dates = [r.get("_fetched") for r in rows if r.get("_fetched")]
scrape_date = max(fetched_dates) if fetched_dates else date.today().isoformat()


def num(x, d=2):
    try:
        return round(float(x), d)
    except (TypeError, ValueError):
        return None


table = []
for _, r in df.iterrows():
    t = r["ticker"]
    table.append({
        "rank": rank_map.get(t, ""),
        "ticker": t, "name": (r.get("name") or "")[:32], "country": r["country"],
        "sector": r.get("sector") or "", "lane": r["lane"],
        "yield": num(r.get("yield_pct")), "y5avg": num(r.get("yield_5y_avg")),
        "cheap": num(cheap_map.get(t)), "roe": num(r.get("roe"), 3),
        "cagr": num(r.get("div_cagr5"), 3),
        "mom": num(r.get("mom_12m") * 100, 1) if r.get("mom_12m") is not None else None,
        "years": r.get("div_years"),
        "status": "PASS" if r["fails"] == "" else "reject",
        "fails": r["fails"], "score": num(score_map.get(t), 3),
        "fetched": r.get("_fetched", ""),
    })
table.sort(key=lambda x: (x["status"] != "PASS", x["rank"] if x["rank"] != "" else 9999))

# sensitivity
base_top = list(ranked["ticker"].head(10))
sens = []
def variant(label, **kw):
    saved = {k: getattr(S, k) for k in kw}
    for k, v in kw.items():
        setattr(S, k, v)
    dd = S.apply_gates(pd.DataFrame(rows))
    ss = dd[dd["fails"] == ""]
    top = list(S.rank(ss)["ticker"].head(10))
    jac = len(set(top) & set(base_top)) / len(set(top) | set(base_top))
    for k, v in saved.items():
        setattr(S, k, v)
    sens.append({"label": label, "n": len(ss), "overlap": round(jac, 2)})

for lbl, kw in [("Yield floor 2.5% (base 3.0)", {"YIELD_MIN": 2.5}),
                ("Yield floor 3.5%", {"YIELD_MIN": 3.5}),
                ("Yield cap 7% (base 8.0)", {"YIELD_MAX": 7.0}),
                ("Yield cap 9%", {"YIELD_MAX": 9.0}),
                ("ROE floor 8% (base 10)", {"MIN_ROE": 0.08}),
                ("ROE floor 12%", {"MIN_ROE": 0.12}),
                ("FCF cover 1.0x (base 1.3)", {"MIN_FCF_COVER": 1.0}),
                ("FCF cover 1.5x", {"MIN_FCF_COVER": 1.5}),
                ("Debt/EBITDA 3x (base 4)", {"MAX_DEBT_EBITDA": 3.0}),
                ("Record 8y (base 10)", {"MIN_DIV_YEARS": 8}),
                ("Record 15y", {"MIN_DIV_YEARS": 15}),
                ("Size floor $5bn (base 10)", {"MIN_MCAP_USD": 5e9}),
                ("Size floor $25bn", {"MIN_MCAP_USD": 25e9})]:
    variant(lbl, **kw)

n_surv, n_univ = len(surv), len(df)
by_country = df.groupby("country").size().to_dict()
surv_country = surv.groupby("country").size().to_dict()
country_rows = "".join(
    f"<tr><td>{c}</td><td>{surv_country.get(c,0)}</td><td>{by_country.get(c,0)}</td>"
    f"<td>{round(surv_country.get(c,0)/by_country.get(c,1)*100,1)}%</td></tr>"
    for c in sorted(by_country, key=lambda k: -by_country[k]))
sens_rows = "".join(
    f"<tr><td>{s['label']}</td><td>{s['n']}</td>"
    f"<td class='{'bad' if s['overlap']<0.6 else 'ok' if s['overlap']<0.85 else 'good'}'>"
    f"{int(s['overlap']*100)}%</td></tr>" for s in sens)

DOC = HERE / "DOCTRINE.html"  # extracted prose, written below
html = DOC.read_text().replace("{{SCRAPE}}", scrape_date)\
    .replace("{{NSURV}}", str(n_surv)).replace("{{NUNIV}}", str(n_univ))\
    .replace("{{COUNTRY_ROWS}}", country_rows).replace("{{SENS_ROWS}}", sens_rows)\
    .replace("{{DATA}}", json.dumps(table)).replace("{{GENERATED}}", date.today().isoformat())
(DOCS / "index.html").write_text(html)
print(f"wrote {DOCS/'index.html'} ({n_surv}/{n_univ} survivors, scraped {scrape_date})")
