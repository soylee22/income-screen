# income-screen

Evidence-anchored acquisition screen for a UK buy-and-hold dividend snowball.
Criteria and evidence: `wiki/references/income-acquisition-evidence-review.md`.

## Run

```bash
python3 build_universe.py        # rebuild universe.txt (FTSE100+S&P500+CAC40+DAX+CH/HK), ~690 names
python3 screen.py                # incremental: fetch only names >30 days stale, rank, write CSVs
python3 screen.py --rerank       # rank from store.json only, fetch nothing (~6 sec)
python3 screen.py --refresh      # force full re-fetch of everything (~25 min)
python3 screen.py --max-age 7    # treat store rows older than 7 days as stale
python3 screen.py --top 30       # print N rows
```

Outputs: `screen-DATE.csv` (full ranked survivors), `rejects-DATE.csv` (every reject + the gate it failed).

## Site + diagnostics

```bash
python3 analyze.py      # country/sector breakdown, reject histogram, threshold sensitivity (from store, no fetch)
python3 gen_site.py     # build docs/index.html: doctrine + sensitivity + searchable/sortable table of all names
```

`docs/index.html` is self-contained and GitHub-Pages-ready (point Pages at /docs). Regenerate it
after any refresh so the published scrape date and survivor list stay current. The prose template
lives in `DOCTRINE.html`; `gen_site.py` injects the live data and sensitivity into it.

## Persistence

`store.json` is a per-ticker store with a `_fetched` date stamp. A normal run reuses anything
fetched within `--max-age` days (default 30) and fetches only the stale/missing names, so the
expensive 690-name pull happens once and updates incrementally. The store falls back to stale
data if a live fetch fails, so a name is never silently dropped on a bad Yahoo response.

## When to re-run (cadence)

- `--rerank` (instant): any time you want to re-sort or after editing thresholds/weights. Free.
- Monthly full-ish refresh (`python3 screen.py`, default 30-day age): the natural cadence. Yields
  and prices drift daily but the *gates* (ROE, payout record, leverage) move on quarterly results,
  so monthly is the right resolution for a monthly-buy discipline. This is the one to automate.
- `--refresh` after results season (late Feb / late Jul for US+EU, plus UK interims) when fundamentals
  actually move, or after a sharp market drop when cheap_ratio values are stale and you want the
  current yield-vs-own-average picture.
- Rebuild `universe.txt` ~quarterly: index reconstitutions add/drop constituents.

Don't re-fetch more often than weekly: nothing in the gates changes intra-week, and it only risks
Yahoo rate-limiting.
