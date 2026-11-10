"""Pure OHLCV normalization and 1H -> 4H resampling transforms."""

from __future__ import annotations

import numpy as np
import pandas as pd

OHLCV_COLUMNS = ["ts", "open", "high", "low", "close", "volume"]
_PRICE_VOLUME_COLUMNS = ["open", "high", "low", "close", "volume"]
_TIMESTAMP_CANDIDATES = ("ts", "datetime", "date", "timestamp", "index")


def _empty_ohlcv() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ts": pd.Series(dtype="datetime64[ns, UTC]"),
            "open": pd.Series(dtype="float64"),
            "high": pd.Series(dtype="float64"),
            "low": pd.Series(dtype="float64"),
            "close": pd.Series(dtype="float64"),
            "volume": pd.Series(dtype="float64"),
        }
    )


def _normalize_yfinance(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return _empty_ohlcv()

    work = df.copy()
    if isinstance(work.columns, pd.MultiIndex):
        work.columns = work.columns.get_level_values(0)

    col_lookup = {str(col).strip().lower(): col for col in work.columns}
    has_ts_col = any(candidate in col_lookup for candidate in _TIMESTAMP_CANDIDATES)

    if not has_ts_col and (
        isinstance(work.index, pd.DatetimeIndex)
        or (work.index.name and str(work.index.name).strip().lower() in _TIMESTAMP_CANDIDATES)
    ):
        work = work.reset_index()
        col_lookup = {str(col).strip().lower(): col for col in work.columns}

    ts_col = next((col_lookup[c] for c in _TIMESTAMP_CANDIDATES if c in col_lookup), None)
    if ts_col is None:
        raise ValueError("yfinance DataFrame is missing a timestamp column or DatetimeIndex")

    missing = [name for name in _PRICE_VOLUME_COLUMNS if name not in col_lookup]
    if missing:
        raise ValueError(f"yfinance DataFrame is missing required OHLCV columns: {missing}")

    out = pd.DataFrame(
        {
            "ts": pd.to_datetime(work[ts_col], utc=True),
            "open": pd.to_numeric(work[col_lookup["open"]], errors="coerce").astype("float64"),
            "high": pd.to_numeric(work[col_lookup["high"]], errors="coerce").astype("float64"),
            "low": pd.to_numeric(work[col_lookup["low"]], errors="coerce").astype("float64"),
            "close": pd.to_numeric(work[col_lookup["close"]], errors="coerce").astype("float64"),
            "volume": pd.to_numeric(work[col_lookup["volume"]], errors="coerce").astype("float64"),
        }
    )
    return out.sort_values("ts", kind="mergesort").reset_index(drop=True)


def _normalize_binance(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return _empty_ohlcv()

    work = df.copy()
    col_lookup = {str(col).strip().lower(): col for col in work.columns}

    if "open_time" in col_lookup or "opentime" in col_lookup:
        ts_key = col_lookup.get("open_time", col_lookup.get("opentime"))
        missing = [name for name in _PRICE_VOLUME_COLUMNS if name not in col_lookup]
        if missing:
            raise ValueError(f"Binance DataFrame is missing required OHLCV columns: {missing}")
        raw_ts = work[ts_key]
        raw_open = work[col_lookup["open"]]
        raw_high = work[col_lookup["high"]]
        raw_low = work[col_lookup["low"]]
        raw_close = work[col_lookup["close"]]
        raw_volume = work[col_lookup["volume"]]
    elif {0, 1, 2, 3, 4, 5}.issubset(set(work.columns)):
        raw_ts = work[0]
        raw_open = work[1]
        raw_high = work[2]
        raw_low = work[3]
        raw_close = work[4]
        raw_volume = work[5]
    else:
        raise ValueError("Binance DataFrame does not match expected kline schema")

    open_ms = pd.to_numeric(raw_ts, errors="raise")
    out = pd.DataFrame(
        {
            "ts": pd.to_datetime(open_ms, unit="ms", utc=True),
            "open": pd.to_numeric(raw_open, errors="coerce").astype("float64"),
            "high": pd.to_numeric(raw_high, errors="coerce").astype("float64"),
            "low": pd.to_numeric(raw_low, errors="coerce").astype("float64"),
            "close": pd.to_numeric(raw_close, errors="coerce").astype("float64"),
            "volume": pd.to_numeric(raw_volume, errors="coerce").astype("float64"),
        }
    )
    return out.sort_values("ts", kind="mergesort").reset_index(drop=True)


def normalize(df: pd.DataFrame, source: str) -> pd.DataFrame:
    """Normalize raw source DataFrame to UTC OHLCV schema [ts, open, high, low, close, volume]."""
    src = source.strip().lower()
    if src == "yfinance":
        return _normalize_yfinance(df)
    if src == "binance":
        return _normalize_binance(df)
    raise ValueError(f"Unsupported source {source!r}; expected 'yfinance' or 'binance'")


def resample_4h(df_1h: pd.DataFrame) -> pd.DataFrame:
    """Resample normalized 1H bars into 4H bars anchored at 00:00 UTC, dropping an incomplete last bar."""
    missing = [col for col in OHLCV_COLUMNS if col not in df_1h.columns]
    if missing:
        raise ValueError(f"df_1h is missing required normalized columns: {missing}")

    if df_1h.empty:
        return _empty_ohlcv()

    valid = df_1h.dropna(subset=["ts", "open", "high", "low", "close"]).copy()
    if valid.empty:
        return _empty_ohlcv()

    valid["ts"] = pd.to_datetime(valid["ts"], utc=True)
    valid = valid.sort_values("ts", kind="mergesort").drop_duplicates(subset=["ts"], keep="last")

    indexed = valid.set_index("ts")
    resampler = indexed.resample("4h", origin="start_day", label="left", closed="left")

    bars = resampler.agg(
        {
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        }
    )
    bar_counts = resampler["close"].count()
    vol_counts = resampler["volume"].count()

    non_empty_mask = bar_counts > 0
    bars = bars.loc[non_empty_mask].copy()
    bar_counts = bar_counts.loc[non_empty_mask]
    vol_counts = vol_counts.loc[non_empty_mask]

    if bars.empty:
        return _empty_ohlcv()

    bars["volume"] = np.where(vol_counts > 0, bars["volume"], np.nan)

    last_bucket_ts = bars.index[-1]
    last_count = int(bar_counts.iloc[-1])
    max_input_ts = valid["ts"].iloc[-1]

    if last_count < 4 or max_input_ts < last_bucket_ts + pd.Timedelta(hours=3):
        bars = bars.iloc[:-1]

    if bars.empty:
        return _empty_ohlcv()

    out = bars.reset_index().rename(columns={"index": "ts"})
    return out[OHLCV_COLUMNS].reset_index(drop=True)
