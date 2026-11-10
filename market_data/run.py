"""CLI entrypoint for the market data pipeline."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
import logging
from pathlib import Path
import sys

import pandas as pd

from market_data.config import DEFAULT_SYMBOLS_PATH, Symbol, load_symbols
from market_data.indicators import add_indicators
from market_data.sources import YFINANCE_MAX_HOURLY_DAYS, fetch_binance, fetch_yfinance
from market_data.store import build_payload, upsert
from market_data.transform import normalize, resample_4h
from market_data.validate import validate

logger = logging.getLogger("market_data.run")

VALID_TIMEFRAMES = ("4H", "1D", "1W")

# Extra calendar days fetched prior to --since-days so 50-bar indicators (SMA_50) warm up cleanly.
_WARMUP_DAYS: dict[str, int] = {
    "4H": 15,
    "1D": 90,
    "1W": 400,
}


def _parse_timeframes(raw: str) -> list[str]:
    parts = [part.strip().upper() for part in raw.split(",") if part.strip()]
    if not parts:
        raise argparse.ArgumentTypeError("--timeframes must specify at least one timeframe")
    invalid = [tf for tf in parts if tf not in VALID_TIMEFRAMES]
    if invalid:
        raise argparse.ArgumentTypeError(
            f"Unsupported timeframe(s) {invalid}; choose from {', '.join(VALID_TIMEFRAMES)}"
        )
    # Preserve user order while deduplicating.
    return list(dict.fromkeys(parts))


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m market_data.run",
        description="Fetch, normalize, validate, enrich, and upsert OHLCV market bars.",
    )
    parser.add_argument(
        "--timeframes",
        type=_parse_timeframes,
        default=["1D", "4H", "1W"],
        help="Comma-separated list of timeframes (4H,1D,1W). Default: 1D,4H,1W",
    )
    parser.add_argument(
        "--since-days",
        type=int,
        default=30,
        help="Upsert bars within the last N days. Default: 30",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run fetch, transform, validation, and indicator calculation without writing to Supabase.",
    )
    parser.add_argument(
        "--symbols-file",
        type=Path,
        default=DEFAULT_SYMBOLS_PATH,
        help="Path to symbols.yaml configuration file.",
    )
    return parser


def _compute_fetch_start(now_utc: datetime, since_days: int, timeframe: str, source: str) -> datetime:
    total_days = since_days + _WARMUP_DAYS[timeframe]
    if source == "yfinance" and timeframe == "4H":
        total_days = min(total_days, YFINANCE_MAX_HOURLY_DAYS)
    return now_utc - timedelta(days=total_days)


def _fetch_and_prepare_bars(sym: Symbol, timeframe: str, fetch_start: datetime) -> pd.DataFrame:
    if sym.source == "yfinance":
        if timeframe == "4H":
            raw = fetch_yfinance(sym.provider_symbol, interval="1h", start=fetch_start)
            normalized = normalize(raw, source="yfinance")
            return resample_4h(normalized)
        yf_interval = "1d" if timeframe == "1D" else "1wk"
        raw = fetch_yfinance(sym.provider_symbol, interval=yf_interval, start=fetch_start)
        bars = normalize(raw, source="yfinance")
        # yfinance stamps daily/weekly bars at exchange-local midnight; store the session date at 00:00 UTC.
        bars["ts"] = bars["ts"].dt.tz_convert("UTC").dt.normalize()
        return bars.drop_duplicates(subset="ts", keep="last").reset_index(drop=True)

    if sym.source == "binance":
        binance_interval = {"4H": "4h", "1D": "1d", "1W": "1w"}[timeframe]
        raw = fetch_binance(sym.provider_symbol, interval=binance_interval, start=fetch_start)
        return normalize(raw, source="binance")

    raise ValueError(f"Unsupported source {sym.source!r} for {sym.symbol}")


def _process_symbol(
    sym: Symbol,
    timeframes: Sequence[str],
    *,
    since_days: int,
    dry_run: bool,
    now_utc: datetime,
) -> bool:
    cutoff = pd.Timestamp(now_utc - timedelta(days=since_days))
    any_tf_succeeded = False

    for tf in timeframes:
        try:
            fetch_start = _compute_fetch_start(now_utc, since_days, tf, sym.source)
            bars = _fetch_and_prepare_bars(sym, tf, fetch_start)
            clean_df, issues = validate(bars, timeframe=tf, asset_class=sym.asset_class)

            for issue in issues:
                logger.warning("[%s %s] %s", sym.symbol, tf, issue)

            if clean_df.empty:
                raise RuntimeError(f"No valid bars returned for {sym.symbol} ({sym.provider_symbol}) {tf}")

            enriched = add_indicators(clean_df)
            window = enriched.loc[enriched["ts"] >= cutoff].reset_index(drop=True)
            payload = build_payload(
                window,
                symbol=sym.symbol,
                asset_class=sym.asset_class,
                timeframe=tf,
                source=sym.source,
            )

            if dry_run:
                latest_ts = payload[-1]["ts"] if payload else "n/a"
                logger.info(
                    "[DRY-RUN] %s (%s) %s: %d bar(s) ready (latest=%s)",
                    sym.symbol,
                    sym.asset_class,
                    tf,
                    len(payload),
                    latest_ts,
                )
            elif payload:
                written = upsert(payload)
                logger.info(
                    "%s (%s) %s: upserted %d bar(s) (latest=%s)",
                    sym.symbol,
                    sym.asset_class,
                    tf,
                    written,
                    payload[-1]["ts"],
                )
            else:
                logger.info(
                    "%s (%s) %s: 0 bars since %s; nothing to upsert",
                    sym.symbol,
                    sym.asset_class,
                    tf,
                    cutoff.isoformat(),
                )

            any_tf_succeeded = True
        except Exception:  # noqa: BLE001
            logger.exception("Failed processing %s (%s) timeframe %s", sym.symbol, sym.provider_symbol, tf)

    return any_tf_succeeded


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if args.since_days <= 0:
        parser.error("--since-days must be a positive integer")

    symbols = load_symbols(args.symbols_file)
    now_utc = datetime.now(timezone.utc)

    logger.info(
        "Starting pipeline for %d symbol(s), timeframes=%s, since_days=%d, dry_run=%s",
        len(symbols),
        ",".join(args.timeframes),
        args.since_days,
        args.dry_run,
    )

    succeeded_symbols = 0
    for sym in symbols:
        try:
            if _process_symbol(
                sym,
                args.timeframes,
                since_days=args.since_days,
                dry_run=args.dry_run,
                now_utc=now_utc,
            ):
                succeeded_symbols += 1
        except Exception:  # noqa: BLE001
            logger.exception("Unhandled error processing symbol %s", sym.symbol)

    failed_symbols = len(symbols) - succeeded_symbols
    logger.info(
        "Pipeline finished: %d/%d symbol(s) succeeded, %d failed",
        succeeded_symbols,
        len(symbols),
        failed_symbols,
    )

    if succeeded_symbols == 0:
        logger.error("Every symbol failed; exiting with non-zero status")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
