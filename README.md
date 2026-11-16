# market-data-pipeline

A Python job that pulls free daily, 4-hour and weekly market data (indices, FX, crypto, commodities, metals, bonds, VIX), cleans it, adds a few indicators and upserts it into a Supabase table. It is meant to run on a GitHub Actions schedule. If you need a small, tested example of that setup, or a starting point for your own data feed, this is it.

Sources: Yahoo Finance through `yfinance`, and Binance public klines for crypto. 14 symbols are configured in `symbols.yaml`.

## Try it (no Supabase needed)

```bash
pip install -r requirements-dev.txt
pytest -q                     # 23 tests, offline
python -m market_data.run --timeframes 1D --since-days 5 --dry-run
```

The dry run fetches, normalizes, validates and computes indicators, but writes nothing. Output from a run on 2026-11-16 (last lines):

```
WARNING market_data.run: [EURUSD 1D] Repaired 3 row(s) whose open/close sat within 0.1% outside high/low
INFO market_data.run: [DRY-RUN] EURUSD (forex) 1D: 3 bar(s) ready (latest=2026-11-16T00:00:00+00:00)
INFO market_data.run: [DRY-RUN] BTCUSDT (crypto) 1D: 4 bar(s) ready (latest=2026-11-15T00:00:00+00:00)
INFO market_data.run: [DRY-RUN] VIX (volatility) 1D: 3 bar(s) ready (latest=2026-11-16T00:00:00+00:00)
INFO market_data.run: Pipeline finished: 14/14 symbol(s) succeeded, 0 failed
```

Requires Python 3.11+.

## How it works

1. `sources.py` downloads raw bars with timeouts and retries.
2. `transform.py` converts them to UTC `[ts, open, high, low, close, volume]`. Yahoo has no 4-hour interval, so 4H bars are built from 1-hour bars, aligned to 00:00 UTC, and the unfinished last bar is dropped.
3. `validate.py` drops duplicate timestamps, rows with NaN prices and negative volume. If open or close sits less than 0.1% outside high/low (common in free FX data), it widens high/low instead of dropping the bar. Bigger violations are dropped. Gaps over 3x the timeframe are logged.
4. `indicators.py` adds `sma_20`, `sma_50`, `ema_20`, `rsi_14` and `atr_14` (RSI and ATR use Wilder smoothing), in plain pandas.
5. `store.py` upserts in batches of 500 through the Supabase REST API, keyed on `(symbol, timeframe, ts)`, so re-running over an overlapping window is safe.

`run.py` handles each symbol separately, so one failing symbol does not stop the rest.

## Run it against Supabase

1. Run `sql/schema.sql` in the Supabase SQL Editor. It creates `public.market_bars`, turns on Row Level Security, gives `SELECT` to `authenticated` users only and removes `anon` access.
2. Set `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` as environment variables (locally) or as repository secrets (in GitHub Actions). The service role key bypasses RLS, so keep it server-side.
3. Run the command above without `--dry-run`.

For the schedule, `.github/workflows/update.yml` is set to run at minute 17 of every 4th hour and can also be started by hand. `.github/workflows/ci.yml` runs the tests on push.

## Limits

- The scheduled workflow is currently disabled in this repository's Actions settings and has never run, so no live Supabase run is shown here. CI on push passes.
- `yfinance` is an unofficial client. Hourly history goes back about 730 days, and intervals under 1 hour about 60 days.
- Free endpoints can lag, rate-limit or revise recent bars. Upserts overwrite revised bars when you re-run with an overlapping `--since-days`.
- Only 4H, 1D and 1W timeframes are supported.
- No retry queue or alerting. A failed run shows up as a failed Actions job and a log artifact kept for 14 days.

## License

MIT, see `LICENSE`.
