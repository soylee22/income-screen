#!/usr/bin/env python3
"""Build docs/recovery.html: the recovery-value scanner dashboard (companion to index.html)."""
import json
from datetime import date

import pandas as pd
import recovery as R
import screen as S

DOCS = S.HERE / "docs"
DOCS.mkdir(exist_ok=True)


def num(x, d=1):
    try:
        return round(float(x), d)
    except (TypeError, ValueError):
        return None


df, surv = R.scan()
df["country"] = df["ticker"].map(S.country)
rank_map = {t: i + 1 for i, t in enumerate(surv["ticker"])}
fetched = [r.get("_fetched") for r in df.to_dict("records") if r.get("_fetched")]
scrape_date = max(fetched) if fetched else date.today().isoformat()

table = []
for _, r in df.iterrows():
    t = r["ticker"]
    nm, cm = r.get("rec_norm_margin"), r.get("rec_cur_margin")
    table.append({
        "rank": rank_map.get(t, ""),
        "ticker": t, "name": (r.get("name") or "")[:30], "country": r["country"],
        "sector": r.get("sector") or "",
        "norm": num(nm * 100, 1) if nm is not None and not pd.isna(nm) else None,
        "cur": num(cm * 100, 1) if cm is not None and not pd.isna(cm) else None,
        "gap": num(r.get("margin_gap"), 2),
        "npe": num(r.get("norm_pe"), 1),
        "lev": num(r.get("net_lev"), 1),
        "up": num(r.get("upside") * 100, 0) if r.get("upside") is not None and not pd.isna(r.get("upside")) else None,
        "status": "CAND" if r["fails"] == "" else "reject",
        "fails": r["fails"],
        "fetched": r.get("_fetched", ""),
    })
table.sort(key=lambda x: (x["status"] != "CAND", -(x["up"] if x["up"] is not None else -1e9)))

top = ", ".join(surv["ticker"].head(3))
html = (S.HERE / "RECOVERY_DOCTRINE.html").read_text()\
    .replace("{{SCRAPE}}", scrape_date).replace("{{NCAND}}", str(len(surv)))\
    .replace("{{NUNIV}}", str(len(df))).replace("{{TOP}}", top)\
    .replace("{{DATA}}", json.dumps(table)).replace("{{GENERATED}}", date.today().isoformat())
(DOCS / "recovery.html").write_text(html)
print(f"wrote {DOCS/'recovery.html'} ({len(surv)} candidates / {len(df)} operating cos)")
