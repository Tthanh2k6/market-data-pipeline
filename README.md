# market-data-pipeline

A small Python pipeline that downloads free OHLCV market data from Yahoo Finance (`yfinance`) and Binance public klines, normalizes bars to UTC, validates records, computes basic technical indicators in pandas, and upserts rows into Supabase (PostgreSQL) via PostgREST on a GitHub Actions schedule.

## Architecture

- `symbols.yaml` - Configured instruments across indices, forex, crypto, commodities, metals, bonds, and volatility.
- `market_data/config.py` - Loads and validates `symbols.yaml` into `Symbol` records.
- `market_data/sources.py` - Fetches raw data from `yfinance` and Binance REST klines with timeouts and exponential-backoff retries.
- `market_data/transform.py` - Pure functions to normalize source schemas to UTC `[ts, open, high, low, close, volume]` and resample `1H` bars into `4H` bars anchored at `00:00` UTC.
- `market_data/validate.py` - Pure validation that drops duplicate timestamps, `NaN` OHLC rows and negative volume, repairs bars whose open/close sit within 0.1% outside high/low (a quirk of free FX feeds), drops larger OHLC violations, and logs gaps larger than `3x` the timeframe.
- `market_data/indicators.py` - Pure-pandas calculation of `sma_20`, `sma_50`, `ema_20`, Wilder `rsi_14`, and Wilder `atr_14`.
- `market_data/store.py` - Builds JSON-safe payloads and upserts batches of 500 rows to Supabase PostgREST (`on_conflict=symbol,timeframe,ts`).
- `market_data/run.py` - CLI entrypoint with per-symbol error isolation and `--dry-run` mode.
- `sql/schema.sql` - Table definition (`public.market_bars`), index, and Row Level Security policy.
- `.github/workflows/update.yml` & `.github/workflows/ci.yml` - Scheduled 4-hour data update (`17 */4 * * *`) and unit test CI.

## Setup

1. Create a Supabase project and execute `sql/schema.sql` in the SQL Editor. This creates `public.market_bars`, enables Row Level Security, grants `SELECT` to `authenticated` users only, revokes `anon` access, and leaves writes to the server-side `service_role` key (which bypasses RLS).
2. In your GitHub repository settings (`Settings -> Secrets and variables -> Actions`), add two repository secrets:
   - `SUPABASE_URL` (for example, `https://<project-ref>.supabase.co`)
   - `SUPABASE_SERVICE_ROLE_KEY` (keep server-side only; never expose in client code)
3. Enable the `Update Market Data` workflow in `.github/workflows/update.yml`, or trigger it manually with `workflow_dispatch`.

## Run Locally

Requires Python 3.11+.

```bash
pip install -r requirements-dev.txt
pytest -v
```

Run a dry run (fetches, normalizes, validates, and computes indicators without writing to Supabase):

```bash
python -m market_data.run --timeframes 1D,4H,1W --since-days 30 --dry-run
```

To upsert into Supabase locally, export `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` in your shell environment and omit `--dry-run`.

## Limits

- `yfinance` is an unofficial Yahoo Finance client. Hourly (`1h` / `60m`) history only goes back roughly 730 days, and intraday intervals below `1h` only go back about 60 days. `4H` bars for `yfinance` symbols are constructed by resampling `1H` bars anchored at `00:00` UTC and dropping any incomplete trailing bar.
- Crypto bars use the Binance public klines endpoint (`GET https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=4h&startTime=<ms>&limit=1000`), where `4h`, `1d`, and `1w` are native intervals and response rows are arrays (`[openTime, open, high, low, close, volume, closeTime, ...]`) formatted as strings.
- Free market data endpoints can lag, throttle, or revise recent bars. Because writes upsert on the `(symbol, timeframe, ts)` primary key, re-running the pipeline with an overlapping `--since-days` window overwrites revised bars idempotently.

## License

MIT License. See `LICENSE`.
