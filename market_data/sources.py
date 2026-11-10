"""Network data fetchers for Yahoo Finance (yfinance) and Binance public klines."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
import logging
import os
import time
from typing import TypeVar

import pandas as pd
import requests
import yfinance as yf

logger = logging.getLogger(__name__)

T = TypeVar("T")

DEFAULT_TIMEOUT_SECONDS = 20.0
MAX_RETRIES = 3
BASE_BACKOFF_SECONDS = 1.0
YFINANCE_MAX_HOURLY_DAYS = 729
BINANCE_LIMIT = 1000
BINANCE_MAX_PAGES = 50

BINANCE_PRIMARY_URL = "https://api.binance.com/api/v3/klines"
BINANCE_FALLBACK_URL = "https://data-api.binance.vision/api/v3/klines"

BINANCE_KLINE_COLUMNS = [
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_asset_volume",
    "number_of_trades",
    "taker_buy_base_volume",
    "taker_buy_quote_volume",
    "ignore",
]

_YFINANCE_INTERVAL_MAP = {
    "1h": "1h",
    "60m": "60m",
    "1d": "1d",
    "1wk": "1wk",
    "1w": "1wk",
}

_BINANCE_INTERVAL_MAP = {
    "1h": "1h",
    "4h": "4h",
    "1d": "1d",
    "1w": "1w",
}


def _to_utc_datetime(value: datetime | pd.Timestamp | str) -> datetime:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    else:
        ts = ts.tz_convert("UTC")
    return ts.to_pydatetime()


def _with_retries(
    operation: Callable[[], T],
    *,
    description: str,
    tries: int = MAX_RETRIES,
    base_delay: float = BASE_BACKOFF_SECONDS,
) -> T:
    last_exc: Exception | None = None
    for attempt in range(1, tries + 1):
        try:
            return operation()
        except ValueError:
            raise
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt >= tries:
                break
            delay = base_delay * (2 ** (attempt - 1))
            logger.warning(
                "%s failed on attempt %d/%d (%s); retrying in %.1fs",
                description,
                attempt,
                tries,
                exc,
                delay,
            )
            time.sleep(delay)
    assert last_exc is not None
    raise last_exc


def fetch_yfinance(
    provider_symbol: str,
    interval: str,
    start: datetime | pd.Timestamp | str,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> pd.DataFrame:
    """Fetch OHLCV history from Yahoo Finance via yfinance with retries and timeout."""
    yf_interval = _YFINANCE_INTERVAL_MAP.get(interval.strip().lower())
    if yf_interval is None:
        raise ValueError(
            f"Unsupported yfinance interval {interval!r}; expected one of {sorted(_YFINANCE_INTERVAL_MAP)}"
        )

    start_dt = _to_utc_datetime(start)
    if yf_interval in ("1h", "60m"):
        earliest_hourly = datetime.now(timezone.utc) - timedelta(days=YFINANCE_MAX_HOURLY_DAYS)
        if start_dt < earliest_hourly:
            logger.warning(
                "Clamping yfinance %s start for %s from %s to %s (730-day hourly limit)",
                yf_interval,
                provider_symbol,
                start_dt.isoformat(),
                earliest_hourly.isoformat(),
            )
            start_dt = earliest_hourly

    def _call() -> pd.DataFrame:
        ticker = yf.Ticker(provider_symbol)
        df = ticker.history(
            start=start_dt,
            interval=yf_interval,
            auto_adjust=False,
            actions=False,
            timeout=timeout,
        )
        if not isinstance(df, pd.DataFrame):
            raise RuntimeError(f"Unexpected response type from yfinance for {provider_symbol}: {type(df)!r}")
        return df

    return _with_retries(_call, description=f"yfinance {provider_symbol} ({yf_interval})")


def _request_binance_page(
    session: requests.Session,
    endpoint: str,
    params: dict[str, str | int],
    timeout: float,
) -> list[list[object]]:
    resp = session.get(endpoint, params=params, timeout=timeout)
    if resp.status_code == 451 and endpoint == BINANCE_PRIMARY_URL:
        logger.info("Binance primary endpoint returned HTTP 451; using public market-data mirror")
        resp = session.get(BINANCE_FALLBACK_URL, params=params, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, list):
        raise RuntimeError(f"Unexpected Binance klines payload: {type(data)!r}")
    return data


def fetch_binance(
    symbol: str,
    interval: str,
    start: datetime | pd.Timestamp | str,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> pd.DataFrame:
    """Fetch closed OHLCV klines from Binance public REST API with retries and pagination."""
    b_interval = _BINANCE_INTERVAL_MAP.get(interval.strip().lower())
    if b_interval is None:
        raise ValueError(
            f"Unsupported Binance interval {interval!r}; expected one of {sorted(_BINANCE_INTERVAL_MAP)}"
        )

    start_dt = _to_utc_datetime(start)
    cursor_ms = int(start_dt.timestamp() * 1000)
    endpoint = os.environ.get("BINANCE_KLINES_URL", BINANCE_PRIMARY_URL).strip() or BINANCE_PRIMARY_URL

    all_rows: list[list[object]] = []
    with requests.Session() as session:
        for _ in range(BINANCE_MAX_PAGES):
            params: dict[str, str | int] = {
                "symbol": symbol,
                "interval": b_interval,
                "startTime": cursor_ms,
                "limit": BINANCE_LIMIT,
            }
            batch = _with_retries(
                lambda p=params: _request_binance_page(session, endpoint, p, timeout),
                description=f"binance {symbol} ({b_interval})",
            )
            if not batch:
                break

            all_rows.extend(batch)
            last_open_ms = int(batch[-1][0])  # type: ignore[arg-type]
            if len(batch) < BINANCE_LIMIT or last_open_ms < cursor_ms:
                break
            cursor_ms = last_open_ms + 1

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    closed_rows = [row for row in all_rows if int(row[6]) <= now_ms]  # type: ignore[arg-type]

    if not closed_rows:
        return pd.DataFrame(columns=BINANCE_KLINE_COLUMNS)

    return pd.DataFrame(closed_rows, columns=BINANCE_KLINE_COLUMNS)
