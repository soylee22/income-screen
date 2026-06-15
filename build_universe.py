#!/usr/bin/env python3
"""Build universe.txt from Wikipedia constituent lists: FTSE 100 + S&P 500 + CAC 40 + DAX + IBEX 35 + S&P/TSX 60.

Usage: python3 build_universe.py   (writes universe.txt, one yfinance ticker per line)
"""

import io
import sys
from pathlib import Path

import pandas as pd
import requests

HERE = Path(__file__).parent
HEADERS = {"User-Agent": "Mozilla/5.0 (income-screen research tool)"}

# index -> (url, suffix appended when ticker has no exchange suffix)
SOURCES = {
    "FTSE100": ("https://en.wikipedia.org/wiki/FTSE_100_Index", ".L"),
    "SP500": ("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", ""),
    "CAC40": ("https://en.wikipedia.org/wiki/CAC_40", ".PA"),
    "DAX": ("https://en.wikipedia.org/wiki/DAX", ".DE"),
    "IBEX35": ("https://en.wikipedia.org/wiki/IBEX_35", ".MC"),
    "TSX60": ("https://en.wikipedia.org/wiki/S%26P/TSX_60", ".TO"),
    "FTSE250": ("https://en.wikipedia.org/wiki/FTSE_250_Index", ".L"),  # UK mid-cap growers
}
EXTRAS = ["NESN.SW", "NOVN.SW", "ROG.SW", "ZURN.SW", "ABBN.SW",   # CH majors
          "0941.HK", "0883.HK", "2628.HK",                          # HK income names
          "BTI", "MO", "PM", "IMBBY", "JAPAY",                       # tobacco (Lee's picks)
          "MAIN"]                                                    # BDC (Lee's pick; BDC lane)


def constituents(url, suffix):
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    tables = pd.read_html(io.StringIO(resp.text))
    for t in tables:
        cols = [str(c).strip() for c in t.columns]
        t.columns = cols
        tick_col = next((c for c in cols if c.lower() in ("ticker", "symbol", "epic")
                         or "ticker" in c.lower()), None)
        name_col = next((c for c in cols if "company" in c.lower() or "name" in c.lower()
                         or "security" in c.lower()), None)
        if tick_col and name_col and len(t) >= 30:
            out = []
            for raw in t[tick_col].astype(str):
                tk = raw.strip().upper().rstrip(".")
                if not tk or tk == "NAN":
                    continue
                if "." in tk and tk.split(".")[-1] in ("L", "PA", "DE", "AS", "MC", "MI", "BR", "SW"):
                    pass                       # already exchange-suffixed (CAC/DAX style)
                else:
                    tk = tk.replace(".", "-")  # BRK.B -> BRK-B, BT.A -> BT-A
                    tk += suffix
                out.append(tk)
            return out
    raise RuntimeError(f"no constituent table found at {url}")


def main():
    seen, lines = set(), []
    for idx, (url, suffix) in SOURCES.items():
        try:
            ticks = constituents(url, suffix)
            print(f"{idx}: {len(ticks)}", file=sys.stderr)
            lines.append(f"# {idx}")
            for tk in ticks:
                if tk not in seen:
                    seen.add(tk)
                    lines.append(tk)
        except Exception as e:
            print(f"{idx} FAILED: {e}", file=sys.stderr)
    lines.append("# EXTRAS (CH/HK)")
    for tk in EXTRAS:
        if tk not in seen:
            seen.add(tk)
            lines.append(tk)
    (HERE / "universe.txt").write_text("\n".join(lines) + "\n")
    print(f"wrote {len(seen)} tickers to universe.txt", file=sys.stderr)


if __name__ == "__main__":
    main()
