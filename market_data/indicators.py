"""Pure-pandas technical indicators: SMA(20), SMA(50), EMA(20), Wilder RSI(14), Wilder ATR(14)."""

from __future__ import annotations

import numpy as np
import pandas as pd

INDICATOR_COLUMNS = ["sma_20", "sma_50", "ema_20", "rsi_14", "atr_14"]


def _wilder_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)

    avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()

    safe_loss = avg_loss.where(avg_loss != 0.0, np.nan)
    rs = avg_gain / safe_loss
    rsi = 100.0 - (100.0 / (1.0 + rs))

    ready = avg_gain.notna() & avg_loss.notna()
    zero_loss = ready & (avg_loss == 0.0)
    rsi = rsi.mask(zero_loss & (avg_gain > 0.0), 100.0)
    rsi = rsi.mask(zero_loss & (avg_gain == 0.0), 50.0)
    return rsi.astype("float64")


def _wilder_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean().astype("float64")


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Return a copy of ``df`` with sma_20, sma_50, ema_20, rsi_14, and atr_14 columns added."""
    required = ("open", "high", "low", "close")
    missing = [col for col in required if col not in df.columns]
    if missing:
        raise ValueError(f"DataFrame is missing required OHLC columns: {missing}")

    out = df.copy()
    if out.empty:
        for col in INDICATOR_COLUMNS:
            out[col] = pd.Series(dtype="float64")
        return out

    close = out["close"].astype("float64")
    high = out["high"].astype("float64")
    low = out["low"].astype("float64")

    out["sma_20"] = close.rolling(window=20, min_periods=20).mean()
    out["sma_50"] = close.rolling(window=50, min_periods=50).mean()
    out["ema_20"] = close.ewm(span=20, min_periods=20, adjust=False).mean()
    out["rsi_14"] = _wilder_rsi(close, period=14)
    out["atr_14"] = _wilder_atr(high, low, close, period=14)

    return out
