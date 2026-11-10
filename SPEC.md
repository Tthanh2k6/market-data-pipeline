# SPEC: market-data-pipeline (Python → Supabase, GitHub Actions)

Goal: a small, honest, tested sample project. Download free market data, normalize to OHLCV (UTC), validate, add indicators, upsert into Supabase (Postgres) on a schedule via GitHub Actions.

## Constraints
- Python 3.11+. Dependencies only: pandas, numpy, requests, yfinance, pyyaml; dev: pytest. No TA-Lib, no supabase-py (write via PostgREST with requests).
- Never hardcode secrets. Read SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY from environment. The service key is only used server-side (GitHub Actions secret).
- Parsers/transforms are pure functions (no I/O) so they can be unit-tested with fixtures. Network code is isolated in `sources.py` and `store.py`.

## Layout
```
market_data/
  __init__.py
  config.py        # load symbols.yaml -> list of Symbol(asset_class, symbol, source, provider_symbol)
  sources.py       # fetch_yfinance(provider_symbol, interval, start) -> DataFrame; fetch_binance(symbol, interval, start) -> DataFrame. Retries with exponential backoff (3 tries), timeouts.
  transform.py     # normalize(df, source) -> columns [ts(UTC tz-aware), open, high, low, close, volume]; resample_4h(df_1h) anchored at 00:00 UTC (origin="epoch" not ok; use origin="start_day"), drop incomplete last bar
  validate.py      # validate(df, timeframe) -> (clean_df, issues: list[str]): drop duplicate ts, drop rows with NaN OHLC, flag high < max(open, close) or low > min(open, close) or negative volume (drop those rows), report gaps larger than 3x timeframe (weekends excluded for non-crypto: only report, never fail)
  indicators.py    # add_indicators(df): sma_20, sma_50, ema_20, rsi_14 (Wilder), atr_14 (Wilder); pure pandas
  store.py         # upsert(rows, table="market_bars"): POST {SUPABASE_URL}/rest/v1/market_bars?on_conflict=symbol,timeframe,ts with headers apikey, Authorization Bearer, Prefer: resolution=merge-duplicates,return=minimal; batches of 500
  run.py           # CLI: python -m market_data.run --timeframes 1D,4H,1W --since-days 30 [--dry-run]. Logging with logging module, per-symbol try/except so one failure does not stop others; non-zero exit if every symbol failed.
symbols.yaml       # 2–3 examples per class: indices (^GSPC, ^NDX), forex (EURUSD=X, USDJPY=X), crypto (BTCUSDT, ETHUSDT via binance), commodities (CL=F), metals (GC=F, SI=F), bonds (^TNX), volatility (^VIX)
sql/schema.sql     # table public.market_bars(symbol text, asset_class text, timeframe text check in ('4H','1D','1W'), ts timestamptz, open/high/low/close double precision, volume double precision, sma_20, sma_50, ema_20, rsi_14, atr_14 double precision, source text, inserted_at timestamptz default now(), primary key(symbol,timeframe,ts)); ALTER TABLE ... ENABLE ROW LEVEL SECURITY; a SELECT policy for role authenticated only; no anon access; comment explaining service_role bypasses RLS for the writer.
.github/workflows/update.yml   # cron "17 */4 * * *" + workflow_dispatch; setup-python, pip install -r requirements.txt, run; secrets SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY; upload log as artifact
.github/workflows/ci.yml       # pytest on push/PR
tests/                         # pytest, NO network: fixtures as small CSV/JSON in tests/fixtures (yfinance-shaped DataFrame CSV, Binance klines JSON array). Test normalize (both sources), resample_4h (bar boundaries, incomplete last bar dropped), validate (each issue type), indicators (RSI bounds 0–100, SMA values on a known series, ATR positive), store payload building (no HTTP: test a pure function build_payload).
requirements.txt, requirements-dev.txt, README.md, LICENSE (MIT, Trung Thanh Tran), .gitignore
```

## Data-source facts to respect (write them in README "Limits")
- yfinance is an unofficial Yahoo Finance client; hourly history ("60m"/"1h") only goes back about 730 days; intraday below 1h only ~60 days. 4H is built by resampling 1H.
- Binance public klines endpoint: GET https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=4h&startTime=<ms>&limit=1000 ; intervals 4h, 1d, 1w are native. Response rows: [openTime, open, high, low, close, volume, closeTime, ...] strings.
- Free sources can lag or revise data; the pipeline is idempotent (upsert on primary key), so re-running fixes revised bars.

## README
Plain English, short: what it does, architecture list, setup (create table with sql/schema.sql, add 2 secrets, enable workflow), run locally with --dry-run, limits, license. No hype, no emojis, no claims of production use.

Write all files with write_file. Do NOT run terminal commands (not permitted). At the end list the files you wrote.
