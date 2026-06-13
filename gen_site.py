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

# Gate at the income floor (3%) and the broad floor (1.5%). Metrics come from the 1.5%
# superset so names that only qualify at the lower floor still carry their numbers; the
# dashboard toggle flips which floor's pass/fail decides the survivor set.
df3 = S.apply_gates(pd.DataFrame(rows))
surv3 = df3[df3["fails"] == ""]
ranked3 = S.rank(surv3, mode="expret")
ni3 = S.rank(surv3, mode="net_income")
fails3 = dict(zip(df3["ticker"], df3["fails"]))
ovr_map = dict(zip(df3["ticker"], df3["overridden"]))

S.YIELD_MIN = 1.5
df = S.apply_gates(pd.DataFrame(rows))
df["country"] = df["ticker"].map(country)
surv15 = df[df["fails"] == ""]
ranked = S.rank(surv15, mode="expret")
S.YIELD_MIN = 3.0

exp_map = dict(zip(ranked["ticker"], ranked["exp_return"]))
ny_map = dict(zip(ranked["ticker"], ranked["net_yield"]))
gs_map = dict(zip(ranked["ticker"], ranked["g_sust"]))
growth_map = dict(zip(ranked["ticker"], ranked["growth"]))

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
    roic = r.get("roic")
    roic = roic if (roic is not None and not pd.isna(roic)) else None
    qual = roic if roic is not None else (r.get("roa") if r["lane"] == "A" else r.get("roe"))
    qual = qual if (qual is not None and not pd.isna(qual)) else None
    gval = growth_map.get(t)
    if gval is None or pd.isna(gval):
        gval = r.get("div_growth")
    gval = gval if (gval is not None and not pd.isna(gval)) else None
    table.append({
        "ticker": t, "name": (r.get("name") or "")[:32], "country": r["country"],
        "sector": r.get("sector") or "", "lane": r["lane"],
        "yield": num(r.get("yield_pct")),
        "wht": int(S.WHT_BY_COUNTRY.get(S.country(t), 0.20) * 100),
        "nety": num(ny_map.get(t), 2) if ny_map.get(t) is not None else (
            num(r.get("yield_pct") * (1 - S.WHT_BY_COUNTRY.get(S.country(t), 0.20)), 2)
            if r.get("yield_pct") is not None else None),
        "roic": num(roic * 100, 0) if roic is not None else None,
        "qual": num(qual * 100, 0) if qual is not None else None,
        "gsust": num(gs_map.get(t) * 100, 1) if (gs_map.get(t) is not None and not pd.isna(gs_map.get(t)))
        else (num(gval * 100, 0) if gval is not None else None),
        "mgap": num(r.get("rec_cur_margin") / r.get("rec_norm_margin"), 2)
        if (r.get("rec_norm_margin") and r.get("rec_cur_margin") is not None
            and not pd.isna(r.get("rec_norm_margin")) and r.get("rec_norm_margin") != 0) else None,
        "epsg": num(r.get("ni_cagr") * 100, 0)
        if (r.get("ni_cagr") is not None and not pd.isna(r.get("ni_cagr"))) else None,
        "exp": num(exp_map.get(t), 1),
        "mom": num(r.get("mom_12m") * 100, 1) if r.get("mom_12m") is not None else None,
        "years": r.get("div_years"),
        "g": num(growth_map.get(t), 3),                    # for the net-income growth guard
        "p3": fails3.get(t, "x") == "", "p15": r["fails"] == "",
        "fails3": fails3.get(t, ""), "fails15": r["fails"],
        "ovr": bool(ovr_map.get(t)),
    })

# sensitivity (relative to the 3% income baseline)
base_top = list(ranked3["ticker"].head(10))
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
                ("ROA floor 4% (base 5)", {"MIN_ROA_A": 0.04}),
                ("ROIC floor 6% (base 8)", {"MIN_ROIC_A": 0.06}),
                ("ROIC floor 10%", {"MIN_ROIC_A": 0.10}),
                ("FCF cover 1.0x (base 1.3)", {"MIN_FCF_COVER": 1.0}),
                ("FCF cover 1.5x", {"MIN_FCF_COVER": 1.5}),
                ("Net leverage 3x (base 4)", {"MAX_NET_LEVERAGE": 3.0}),
                ("Growth cap 6% (base 8)", {"GROWTH_CAP": 0.06}),
                ("Growth cap 12%", {"GROWTH_CAP": 0.12}),
                ("Record 15y (base 10)", {"MIN_DIV_YEARS": 15}),
                ("Size floor $25bn (base 15)", {"MIN_MCAP_USD": 25e9})]:
    variant(lbl, **kw)

n_surv, n_univ = len(surv3), len(df)
df3["country"] = df3["ticker"].map(country)
by_country = df3.groupby("country").size().to_dict()
surv_country = surv3.assign(country=surv3["ticker"].map(country)).groupby("country").size().to_dict()
country_rows = "".join(
    f"<tr><td>{c}</td><td>{surv_country.get(c,0)}</td><td>{by_country.get(c,0)}</td>"
    f"<td>{round(surv_country.get(c,0)/by_country.get(c,1)*100,1)}%</td></tr>"
    for c in sorted(by_country, key=lambda k: -by_country[k]))
sens_rows = "".join(
    f"<tr><td>{s['label']}</td><td>{s['n']}</td>"
    f"<td class='{'bad' if s['overlap']<0.6 else 'ok' if s['overlap']<0.85 else 'good'}'>"
    f"{int(s['overlap']*100)}%</td></tr>" for s in sens)

comp_top = ", ".join(ranked3["ticker"].head(3))
ni_top = ", ".join(ni3["ticker"].head(3))

DOC = HERE / "DOCTRINE.html"  # extracted prose, written below
html = DOC.read_text().replace("{{SCRAPE}}", scrape_date)\
    .replace("{{NSURV}}", str(n_surv)).replace("{{NUNIV}}", str(n_univ))\
    .replace("{{COUNTRY_ROWS}}", country_rows).replace("{{SENS_ROWS}}", sens_rows)\
    .replace("{{COMP_TOP}}", comp_top).replace("{{NI_TOP}}", ni_top)\
    .replace("{{DATA}}", json.dumps(table)).replace("{{GENERATED}}", date.today().isoformat())
(DOCS / "index.html").write_text(html)
print(f"wrote {DOCS/'index.html'} ({n_surv}/{n_univ} survivors, scraped {scrape_date})")
