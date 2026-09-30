# Global Dividend-Growth Top-50 — auditable methodology

**Snapshot date:** 2026-09-30  
**TradingView screen:** `Div Strat` / screen id `FA7EUhoA`  
**Purpose:** reproduce the final 50-stock portfolio built in ChatGPT from the TradingView exports.  
**Important:** this is a **VIG-style global approximation**, not an exact replication of Vanguard VIG / the S&P U.S. Dividend Growers Index.

---

## 1. Final rule in one line

> Start with primary-listed stocks in the configured countries that have at least 10 years of continuous dividend growth; remove non-common-equity structures; require an estimated average daily traded value of at least US$1m; remove the highest-yielding 25% by **indicated dividend yield**; then select the 50 largest remaining companies by market cap and equal-weight them at 2% each.

No profitability score, analyst score, P/E score, ROIC score, momentum score or subjective override is used in the **final** portfolio-selection step.

---

## 2. TradingView starting universe

Configured markets in the saved screen:

- America / US
- Germany
- Canada
- United Kingdom
- France
- Spain
- Primary listing only

Dividend-growth filter:

```
IF ContinuousDivGrowth > 9
THEN keep
ELSE exclude
```

Because TradingView reports the streak as an integer number of years, `> 9` means **10 or more years**.

The screen also used an industry allow-list designed to omit REIT / fund-style categories before the calculation below.

Snapshot used for the final run: **526 source rows**.

---

## 3. Structural / security-type cleanup

The export was parsed for obvious securities that should not be treated as ordinary common shares.

Exact logic used in the calculation:

```python
IF "CEF" appears in the TradingView security badges:
    exclude as closed-end fund / investment trust

ELSE IF "P" appears as the preferred-security badge:
    exclude as preferred / depositary preferred

ELSE IF company name ends in an LP/L.P. partnership form:
    exclude as LP / MLP

ELSE:
    keep
```

This is a pragmatic approximation of the security-type exclusions that sit upstream of the real S&P index.

**Snapshot count used in the portfolio build:**

```
526 starting rows
- 6 structural/security-type exclusions
= 520
```

For a future rerun, do not hard-code "6": generate and retain the actual rejection list from that day's export.

---

## 4. Liquidity calculation

### 4.1 Why a calculation was needed

The benchmark rule being approximated is a **US$1m daily value traded** test for a new candidate.  
TradingView supplied **1-month share volume**, not a 3-month median daily value-traded series.

Therefore the portfolio used a one-month average-dollar-volume proxy.

### 4.2 September 2026 trading-day divisors

The following divisors were used:

```
US exchanges (NYSE, NASDAQ, AMEX, CBOE, OTC): 21
Canada (TSX):                                  21
UK (LSE):                                      22
Germany (XETR):                                22
France / Euronext:                             22
Spain (BME):                                   22
```

### 4.3 FX rates frozen for the snapshot

Rates used on 2026-09-30:

```
USD -> USD = 1.00000
CAD -> USD = 0.70259
EUR -> USD = 1.13315
GBP -> USD = 1.32652
GBX -> USD = 1.32652 / 100 = 0.0132652
```

TradingView UK stock prices are commonly quoted in **GBX (pence)**, so 3,600 GBX = £36.00.

### 4.4 Exact formulas

For every stock:

```
average_daily_shares
    = volume_1m_shares / trading_days_for_exchange

price_usd =
    IF currency == "USD": price
    ELSE IF currency == "CAD": price * 0.70259
    ELSE IF currency == "EUR": price * 1.13315
    ELSE IF currency == "GBX": price * 0.0132652
    ELSE: unresolved/manual review

average_daily_value_usd
    = average_daily_shares * price_usd
```

Decision rule:

```
IF average_daily_value_usd >= 1,000,000:
    keep
ELSE:
    exclude
```

Missing / zero volume or an unresolved price/FX value fails the test because liquidity cannot be demonstrated.

### 4.5 Worked example — Judges Scientific (LSE:JDG)

Inputs:

```
Price               = 3,600 GBX = £36.00
September 1M volume = 750,550 shares
UK trading days     = 22
GBP/USD             = 1.32652
```

Calculation:

```
avg shares/day
= 750,550 / 22
= 34,115.9091

avg value/day GBP
= 34,115.9091 * £36
= £1,228,172.73

avg value/day USD
= £1,228,172.73 * 1.32652
= $1,629,195.69
```

Therefore:

```
$1.629m >= $1.000m
=> PASS liquidity
```

### 4.6 Snapshot liquidity result

The run used for the portfolio produced:

```
520 after structural cleanup
- 59 failing the >= $1m/day proxy
= 461 liquidity-qualified stocks
```

**Important approximation:** this is **not** the exact S&P test. The real benchmark uses a 3-month **median** daily value traded test. We used 1-month **average** value traded because that was the available TradingView export.

We deliberately used the **new-candidate $1m threshold for everybody**. We did not use the lower existing-constituent buffer because this portfolio was constructed from scratch.

---

## 5. Remove the highest-yielding 25%

This is done **after** structural and liquidity exclusions.

Yield field:

```
TradingView: Dividend yield % (indicated)
```

Do **not** use the TTM dividend-yield field for this step.

Ordering used for reproducibility:

```
1. indicated dividend yield DESC
2. market cap DESC
3. TradingView symbol ASC
```

Number removed:

```
N = 461

remove_count
= ceil(N * 0.25)
= ceil(461 * 0.25)
= ceil(115.25)
= 116
```

Decision rule:

```
sort eligible stocks by indicated yield descending

IF row_number <= 116:
    exclude as top-25%-yield group
ELSE:
    keep
```

Snapshot result:

```
461 liquidity-qualified
- 116 highest-indicated-yield stocks
= 345 final eligible stocks
```

In this snapshot the boundary was clean:

```
last removed:    approximately 3.33% indicated yield
highest retained: approximately 3.32% indicated yield
```

So the tie-break rules did not determine the boundary.

---

## 6. Final 50 selection

From the 345 survivors:

```
sort by MarketCap USD DESC

IF rank_by_market_cap <= 50:
    include in portfolio
ELSE:
    exclude from portfolio
```

This means market cap is used as a **selection rule**, not as a portfolio weighting rule.

The portfolio is **not market-cap weighted**.

---

## 7. Portfolio weights

There are exactly 50 stocks.

```
target_weight_each
= 100% / 50
= 2.00%
```

Therefore:

```
FOR every selected stock:
    target weight = 2.00%
```

No company receives a larger weight because it has a larger market cap.

Portfolio indicated yield is calculated as:

```
portfolio_indicated_yield
= SUM(target_weight_i * indicated_yield_i)
```

With equal 2% weights this is also the simple arithmetic mean of the 50 indicated yields.

Snapshot workbook result:

```
approx portfolio indicated yield = 1.82%
approx portfolio TTM yield       = 1.76%
```

These yields change with prices and dividend data and are not fixed portfolio rules.

---

## 8. Full decision tree / pseudocode

```python
candidates = []

for stock in tradingview_export:

    # A. Base screen
    if not stock.is_primary_listing:
        continue

    if stock.continuous_dividend_growth_years <= 9:
        continue

    # B. Structural cleanup
    if "CEF" in stock.security_badges:
        continue

    if "P" in stock.security_badges:
        continue

    if stock.company_name matches LP_or_MLP_structure:
        continue

    # C. Liquidity proxy
    days = trading_days(stock.exchange)

    if stock.volume_1m is missing or stock.volume_1m <= 0:
        continue

    price_usd = convert_price_to_usd(
        stock.price,
        stock.currency,
        snapshot_fx_rates
    )

    if price_usd is unresolved:
        continue

    avg_daily_shares = stock.volume_1m / days
    avg_daily_value_usd = avg_daily_shares * price_usd

    if avg_daily_value_usd < 1_000_000:
        continue

    candidates.append(stock)

# D. Yield-trap removal
candidates.sort(
    by = [
        indicated_dividend_yield DESC,
        market_cap_usd DESC,
        symbol ASC
    ]
)

remove_count = ceil(len(candidates) * 0.25)
candidates = candidates[remove_count:]

# E. Final size selection
candidates.sort(by = market_cap_usd DESC)
portfolio = candidates[:50]

# F. Equal weighting
for stock in portfolio:
    stock.target_weight = 1 / 50   # 0.02 = 2%
```

---

## 9. Snapshot count reconciliation

The complete funnel used for the final workbook is:

```
526  TradingView rows
  6  structural/security-type exclusions
----
520
 59  liquidity failures (< $1m/day one-month proxy)
----
461
116  top 25% highest indicated yields
----
345  eligible survivors

345 sorted by market cap
 50 selected
295 not selected solely because they were below the top-50 market-cap cutoff

50 * 2% = 100%
```

---

## 10. Final 50 — 2026-09-30 snapshot

The canonical machine-readable list is stored beside this document in:

`global-dividend-growth-top50-2026-09-30.csv`

The 50 symbols are:

```
NASDAQ:AAPL
NASDAQ:MSFT
NASDAQ:AVGO
NYSE:LLY
NYSE:JPM
NASDAQ:WMT
NYSE:V
NYSE:XOM
NYSE:JNJ
NYSE:MA
NYSE:ABBV
NASDAQ:CSCO
NYSE:ORCL
NASDAQ:LRCX
NASDAQ:COST
NYSE:BAC
NYSE:CAT
NYSE:KO
NYSE:MRK
NYSE:PG
NYSE:UNH
NYSE:PM
NYSE:HD
TSX:RY
NYSE:GS
NASDAQ:TXN
NASDAQ:KLAC
XETR:SAP
NASDAQ:AMGN
NASDAQ:LIN
NYSE:APH
NYSE:IBM
TSX:TD
NASDAQ:QCOM
NASDAQ:ADI
EURONEXT:SU
NASDAQ:GILD
NYSE:ABT
NYSE:ETN
NYSE:MCD
NYSE:UNP
NYSE:NEE
BME:IBE
NYSE:CB
NYSE:PH
NYSE:LMT
NYSE:SPGI
NYSE:MDT
NASDAQ:SBUX
NYSE:SYK
```

---

## 11. What this method intentionally does NOT do

The final 50 is **not** selected using:

- highest dividend yield;
- ROIC / FCF / profitability ranking;
- P/E or PEG;
- analyst ratings;
- momentum;
- sector quotas;
- subjective stock picking.

Those metrics may be useful for analysis, but they are not part of the final portfolio rule.

The earlier 35-income / 15-quality-growth concept was superseded by the simpler final rule:

> **VIG-style eligibility -> liquidity -> remove top yield quartile -> top 50 by market cap -> equal weight 2%.**

---

## 12. Differences from actual VIG / S&P U.S. Dividend Growers

For audit purposes, do not describe this portfolio as an exact VIG replication.

Key differences:

1. **Geography:** this screen deliberately includes non-US markets.
2. **Dividend-history source:** TradingView's continuous-dividend-growth field is used rather than S&P's proprietary eligibility history.
3. **Liquidity:** one-month average dollar volume is used here; the benchmark methodology uses a 3-month median daily value traded test.
4. **Constituent buffers:** the fresh-portfolio calculation does not use incumbent liquidity/yield buffers.
5. **Final selection:** this portfolio takes only the 50 largest survivors; the benchmark does not impose this 50-stock limit.
6. **Weighting:** this portfolio is equal-weighted at 2%; VIG's index uses float-adjusted market-cap weighting subject to its index caps.

---

## 13. Rebalance / rerun protocol

For a future rerun, never reuse the snapshot counts, FX rates, volume totals, yields or market caps.

Recompute in this exact order:

```
1. refresh TradingView screen
2. apply 10+ year dividend-growth gate
3. structural/security-type cleanup
4. calculate fresh USD liquidity
5. exclude < $1m/day
6. recompute and remove top 25% indicated yield
7. sort survivors by fresh market cap
8. select top 50
9. assign 2% each
10. save full pass/fail audit table and date-stamped portfolio
```

Every stage should retain a rejection reason so the path from raw universe to final portfolio can be reconstructed.
