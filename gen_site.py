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

CORE_TEMPLATE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Gold Screen, Core Dividend Recipe</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
:root{--bg:#090806;--bg2:#0f0d0a;--panel:#13110d;--hair:rgba(212,175,55,.16);--hair2:rgba(212,175,55,.30);
--ink:#F4EFE4;--ink2:#C3BBA8;--muted:#867C66;--gold:#D4AF37;--gold2:#F3E2A6;--golddim:rgba(212,175,55,.12);
--pass:#8FBF8A;--bad:#D08B6B;--mono:'JetBrains Mono','SF Mono',ui-monospace,Menlo,monospace;
--sans:'Inter',-apple-system,'Segoe UI',Helvetica,Arial,sans-serif}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--sans);font-size:14px;line-height:1.5;
-webkit-font-smoothing:antialiased;font-variant-numeric:tabular-nums}
body::before{content:"";position:fixed;inset:0;pointer-events:none;z-index:0;
background:radial-gradient(1100px 480px at 80% -10%,rgba(212,175,55,.08),transparent 60%)}
.wrap{position:relative;z-index:1;max-width:1180px;margin:0 auto;padding:34px 24px 70px}
header{border-bottom:1px solid var(--hair);padding-bottom:20px;margin-bottom:22px}
.brand{display:flex;align-items:center;gap:12px;font-family:var(--mono);font-size:12px;letter-spacing:.16em;
text-transform:uppercase;color:var(--gold);font-weight:600}
h1{font-size:30px;font-weight:700;margin:14px 0 6px;letter-spacing:-.01em;
background:linear-gradient(92deg,#F3E2A6,#D4AF37 60%,#B8923A);-webkit-background-clip:text;background-clip:text;
-webkit-text-fill-color:transparent}
.sub{color:var(--ink2);font-size:14.5px;max-width:760px}
.chips{display:flex;flex-wrap:wrap;gap:8px;margin:18px 0 4px}
.chip{font-family:var(--mono);font-size:11.5px;color:var(--gold2);background:var(--golddim);
border:1px solid var(--hair2);border-radius:20px;padding:6px 13px;letter-spacing:.02em}
.meta{font-family:var(--mono);font-size:11px;color:var(--muted);letter-spacing:.05em;margin-top:14px;
display:flex;gap:18px;flex-wrap:wrap}
.meta b{color:var(--gold)}
.field{display:flex;align-items:center;gap:9px;margin:20px 0 10px;background:var(--bg2);
border:1px solid var(--hair);border-radius:9px;padding:9px 13px;max-width:380px}
.field input{flex:1;background:transparent;border:none;outline:none;color:var(--ink);font-family:var(--sans);font-size:14px}
.tablewrap{overflow-x:auto;border:1px solid var(--hair);border-radius:11px;background:var(--panel)}
table{border-collapse:collapse;width:100%;min-width:980px}
thead th{position:sticky;top:0;background:#15120c;font-family:var(--mono);font-size:10.5px;letter-spacing:.05em;
text-transform:uppercase;color:var(--gold);font-weight:600;text-align:right;padding:13px 12px;cursor:pointer;
border-bottom:1px solid var(--hair2);white-space:nowrap}
thead th:nth-child(2),thead th:nth-child(3),thead th:nth-child(4),thead th:nth-child(5){text-align:left}
thead th:hover{color:var(--gold2)}
tbody td{padding:11px 12px;text-align:right;border-bottom:1px solid rgba(212,175,55,.07);white-space:nowrap;font-size:13.5px}
tbody td:nth-child(2),tbody td:nth-child(3),tbody td:nth-child(4),tbody td:nth-child(5){text-align:left}
tbody tr:hover{background:rgba(212,175,55,.05)}
.rank{color:var(--muted);font-family:var(--mono);font-size:12px}
.tkr{font-weight:700;color:var(--gold2)}
.nm{color:var(--ink)}.sec,.ctry{color:var(--ink2);font-size:12.5px}
.chwd{font-weight:700;color:var(--gold)}
.pos{color:var(--pass)}.neg{color:var(--bad)}
footer{margin-top:22px;color:var(--muted);font-size:11.5px;font-family:var(--mono);letter-spacing:.03em}
footer a{color:var(--gold)}
</style></head>
<body><div class="wrap">
<header>
<div class="brand"><span>&#9733;</span> Gold Screen &middot; Core Dividend Recipe</div>
<h1>Covered. Large. Growing. Paid for decades.</h1>
<p class="sub">A single, strict screen: large-cap companies paying a covered, decent dividend that they have both <b>raised</b> for years and <b>paid</b> for over a decade. No yield traps, no loss-makers, no missing data. Ordered by yield plus dividend growth.</p>
<div class="chips">
<span class="chip">Market cap &gt; $10bn</span>
<span class="chip">Yield 3&ndash;7% (indicated)</span>
<span class="chip">Payout 0&ndash;75%</span>
<span class="chip">&ge;6y continuous growth</span>
<span class="chip">&ge;10y continuous payout</span>
<span class="chip">complete data only</span>
</div>
<div class="meta"><span><b>{{N}}</b> companies pass</span><span>data <b>{{SCRAPE}}</b></span><span>built {{GENERATED}}</span><span><a href="index.html">full screen &amp; methodology &rarr;</a></span></div>
</header>
<div class="field">
<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#867C66" stroke-width="2"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/></svg>
<input id="q" placeholder="search ticker / name / sector / country">
</div>
<div class="tablewrap"><table id="t"><thead><tr>
<th data-k="rank">#</th><th data-k="ticker">Ticker</th><th data-k="name">Name</th>
<th data-k="country">Ctry</th><th data-k="sector">Sector</th>
<th data-k="mcap" title="Market cap, $bn">$bn</th>
<th data-k="yield" title="Indicated dividend yield">Yld%</th>
<th data-k="nety" title="After withholding tax">Net%</th>
<th data-k="payout" title="Dividend payout ratio">Pay%</th>
<th data-k="cg" title="Consecutive years of dividend increases">Grw y</th>
<th data-k="cp" title="Years paying a dividend">Pay y</th>
<th data-k="dgr" title="Dividend-growth CAGR over ~15 years">DGR%</th>
<th data-k="chowder" title="Net yield + dividend-growth CAGR">Chwd</th>
</tr></thead><tbody id="tb"></tbody></table></div>
<footer>Research tool, not advice. Past dividend behaviour does not guarantee future payments. &middot; tools/income-screen</footer>
</div>
<script>
const D={{DATA}};let sk="chwd",sa=false;
const tb=document.getElementById('tb'),q=document.getElementById('q');
function c(v){return v===null||v===undefined||v===""?'<span style="color:#5a5346">.</span>':v;}
function render(){
const term=q.value.toLowerCase();
let rows=D.filter(r=>!term||(r.ticker+' '+r.name+' '+r.sector+' '+r.country).toLowerCase().includes(term));
rows.sort((a,b)=>{let x=a[sk],y=b[sk];if(typeof x==="string")return sa?x.localeCompare(y):y.localeCompare(x);return sa?x-y:y-x;});
let rk=0;
tb.innerHTML=rows.map(r=>`<tr>
<td class="rank">${++rk}</td><td class="tkr">${r.ticker}</td><td class="nm">${r.name}</td>
<td class="ctry">${r.country}</td><td class="sec">${r.sector}</td>
<td>${r.mcap}</td><td>${r.yield}%</td><td>${r.nety}%</td>
<td>${r.payout}%</td><td>${r.cg}</td><td>${r.cp}</td>
<td class="${r.dgr<0?'neg':'pos'}">${r.dgr}%</td><td class="chwd">${r.chowder}</td></tr>`).join('');
}
document.querySelectorAll('#t th[data-k]').forEach(th=>th.onclick=()=>{const k=th.dataset.k;if(sk===k)sa=!sa;else{sk=k;sa=false;}render();});
q.oninput=render;render();
</script>
</body></html>
"""


def country(tk):
    for sfx, c in [(".L", "UK"), (".PA", "FR"), (".DE", "DE"), (".SW", "CH"), (".HK", "HK"),
                   (".MC", "ES"), (".MI", "IT"), (".AS", "NL"), (".TO", "CA")]:
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
    _nety = ny_map.get(t)
    if _nety is None and r.get("yield_pct") is not None:
        _nety = r.get("yield_pct") * (1 - S.WHT_BY_COUNTRY.get(S.country(t), 0.20))
    _dgr = S.dgr_window(S._annual_from_store(r.get("div_annual")))   # ~15y dividend-growth CAGR
    _dgr_pct = round(_dgr * 100, 1) if _dgr is not None else None
    _chow = round(_nety + _dgr_pct, 1) if (_nety is not None and _dgr_pct is not None) else None
    table.append({
        "ticker": t, "name": (r.get("name") or "")[:32], "country": r["country"],
        "sector": r.get("sector") or "", "lane": r["lane"],
        "yield": num(r.get("yield_pct")),
        "wht": int(S.WHT_BY_COUNTRY.get(S.country(t), 0.20) * 100),
        "nety": num(_nety, 2),
        "dgr": _dgr_pct, "chowder": _chow,
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
        "streak": r.get("div_streak"),
        "g": num(growth_map.get(t), 3),                    # for the net-income growth guard
        "p3": fails3.get(t, "x") == "", "p15": r["fails"] == "",
        "fails3": fails3.get(t, ""), "fails15": r["fails"],
        "ovr": bool(ovr_map.get(t)),
    })

# ---- dividend-growth screen (second tab): blue-chip aristocrat shape ----
dg = S.apply_gates(pd.DataFrame(rows))            # for lane / quality_pass / mcap_usd / roic
dg = S.apply_growth_gates(dg)                     # strict streak/record + long DGR from div_annual
dg["country"] = dg["ticker"].map(country)


def _safe_pct(v, d=0):
    try:
        if v is None or pd.isna(v):
            return None
        return round(float(v) * 100, d)
    except (TypeError, ValueError):
        return None


gtable = []
for _, r in dg.iterrows():
    t = r["ticker"]
    wht = S.WHT_BY_COUNTRY.get(S.country(t), 0.20)
    y = r.get("yield_pct")
    nety = round(float(y) * (1 - wht), 2) if (y is not None and not pd.isna(y)) else None
    dgr = r.get("gdgr")                            # long-window DGR % (strict, 2019-aware)
    dgr = None if (dgr is None or pd.isna(dgr)) else float(dgr)
    streak = int(r["gstreak"]) if pd.notna(r.get("gstreak")) else 0
    chow = round(nety + min(dgr, S.GROWTH_DGR_CAP * 100), 1) if (nety is not None and dgr is not None) else None
    qf = S._growth_qf(r)
    gsc = round(min(streak, S.GROWTH_STREAK_SCORE_CAP) + chow * qf, 1) if chow is not None else None
    mcap_usd = r.get("mcap_usd")
    gtable.append({
        "ticker": t, "name": (r.get("name") or "")[:32], "country": r["country"],
        "sector": r.get("sector") or "",
        "yield": num(r.get("yield_pct")), "nety": num(nety, 2),
        "dgr": num(dgr, 1), "streak": streak,
        "payout": _safe_pct(r.get("payout_ratio"), 0),
        "chowder": num(chow, 1), "gscore": num(gsc, 1),
        "knife": r.get("knife") or "",
        "mcap": round(float(mcap_usd) / 1e9, 1) if (mcap_usd and not pd.isna(mcap_usd)) else None,
        "pass": r["gfails"] == "", "gfails": r["gfails"],
    })
n_gsurv = int((dg["gfails"] == "").sum())
n_gblue = int(((dg["gfails"] == "") & (pd.to_numeric(dg["mcap_usd"], errors="coerce") >= S.GROWTH_MIN_MCAP_BLUE)).sum())

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
    .replace("{{DATA}}", json.dumps(table)).replace("{{GENERATED}}", date.today().isoformat())\
    .replace("{{DATA_GROWTH}}", json.dumps(gtable))
(DOCS / "index.html").write_text(html)
print(f"wrote {DOCS/'index.html'} ({n_surv}/{n_univ} income survivors, "
      f"{n_gsurv} growth survivors / {n_gblue} blue-chip, scraped {scrape_date})")

# ============ standalone GOLD core screen: Lee's exact validated recipe, own page ============
CORE = dict(mcap=10e9, ymin=3.0, ymax=7.0, paymax=0.75, cont_growth=6, cont_pay=10)


def _ok(v):
    return v is not None and not (isinstance(v, float) and pd.isna(v))


core_rows = []
for _, r in df.iterrows():                              # df carries mcap_usd, country, store fields
    mc, y, po, yrs = r.get("mcap_usd"), r.get("yield_pct"), r.get("payout_ratio"), r.get("div_years")
    ann = S._annual_from_store(r.get("div_annual"))
    cg = S.strict_raise_streak(ann, pra=False)          # pure continuous growth, matches TradingView
    dgr = S.dgr_window(ann)
    if not (_ok(mc) and _ok(y) and _ok(po) and _ok(yrs) and dgr is not None):
        continue                                        # no missing data: drop incomplete rows
    if not (mc > CORE["mcap"] and CORE["ymin"] <= y <= CORE["ymax"] and 0 < po <= CORE["paymax"]
            and cg >= CORE["cont_growth"] and yrs >= CORE["cont_pay"]):
        continue
    wht = S.WHT_BY_COUNTRY.get(S.country(r["ticker"]), 0.20)
    nety = round(y * (1 - wht), 2)
    dgrp = round(dgr * 100, 1)
    core_rows.append({
        "ticker": r["ticker"], "name": (r.get("name") or "")[:34], "country": r["country"],
        "sector": r.get("sector") or "", "mcap": round(mc / 1e9, 1),
        "yield": round(y, 2), "nety": nety, "payout": round(po * 100, 0),
        "cg": int(cg), "cp": int(yrs), "dgr": dgrp, "chowder": round(nety + dgrp, 1),
    })
core_rows.sort(key=lambda x: -x["chowder"])
core_html = CORE_TEMPLATE.replace("{{SCRAPE}}", scrape_date)\
    .replace("{{N}}", str(len(core_rows))).replace("{{GENERATED}}", date.today().isoformat())\
    .replace("{{DATA}}", json.dumps(core_rows))
(DOCS / "core.html").write_text(core_html)
print(f"wrote {DOCS/'core.html'} ({len(core_rows)} core-recipe names)")
