"""Unit tests for pure-pandas technical indicators."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from market_data.indicators import INDICATOR_COLUMNS, add_indicators


def test_sma_and_ema_on_known_linear_series() -> None:
    closes = np.arange(1.0, 61.0)  # 1..60
    df = pd.DataFrame(
        {
            "ts": pd.date_range("2024-01-01", periods=60, freq="1D", tz="UTC"),
            "open": closes,
            "high": closes + 1.0,
            "low": closes - 1.0,
            "close": closes,
            "volume": np.full(60, 100.0),
        }
    )

    out = add_indicators(df)

    # Input DataFrame must not be mutated.
    for col in INDICATOR_COLUMNS:
        assert col not in df.columns
        assert col in out.columns

    assert out["sma_20"].iloc[:19].isna().all()
    assert out["sma_20"].iloc[19] == pytest.approx(10.5)  # mean(1..20)
    assert out["sma_20"].iloc[59] == pytest.approx(50.5)  # mean(41..60)

    assert out["sma_50"].iloc[:49].isna().all()
    assert out["sma_50"].iloc[49] == pytest.approx(25.5)  # mean(1..50)
    assert out["sma_50"].iloc[59] == pytest.approx(35.5)  # mean(11..60)

    assert out["ema_20"].iloc[:19].isna().all()
    assert out["ema_20"].iloc[19:].notna().all()


def test_rsi_14_bounds_and_monotonic_extremes() -> None:
    idx = np.arange(80, dtype=float)
    oscillating_close = 100.0 + 5.0 * np.sin(idx * 0.6) + 0.15 * idx
    df = pd.DataFrame(
        {
            "open": oscillating_close - 0.5,
            "high": oscillating_close + 1.5,
            "low": oscillating_close - 1.5,
            "close": oscillating_close,
        }
    )

    out = add_indicators(df)
    rsi = out["rsi_14"].dropna()
    assert len(rsi) == 80 - 14
    assert ((rsi >= 0.0) & (rsi <= 100.0)).all()
    assert ((rsi > 0.0) & (rsi < 100.0)).all()

    # Strictly rising series -> RSI = 100 after warmup.
    up_close = np.arange(1.0, 31.0)
    up_out = add_indicators(
        pd.DataFrame({"open": up_close, "high": up_close, "low": up_close, "close": up_close})
    )
    assert np.allclose(up_out["rsi_14"].dropna(), 100.0)

    # Strictly falling series -> RSI = 0 after warmup.
    down_close = np.arange(100.0, 70.0, -1.0)
    down_out = add_indicators(
        pd.DataFrame(
            {"open": down_close, "high": down_close, "low": down_close, "close": down_close}
        )
    )
    assert np.allclose(down_out["rsi_14"].dropna(), 0.0)

    # Flat series -> RSI = 50 after warmup.
    flat_close = np.full(30, 50.0)
    flat_out = add_indicators(
        pd.DataFrame(
            {"open": flat_close, "high": flat_close, "low": flat_close, "close": flat_close}
        )
    )
    assert np.allclose(flat_out["rsi_14"].dropna(), 50.0)


def test_atr_14_is_positive_and_matches_constant_true_range() -> None:
    # Constant bar range of 4.0 around a flat close of 100.0 -> TR = 4.0 on every bar.
    n = 30
    const_df = pd.DataFrame(
        {
            "open": np.full(n, 100.0),
            "high": np.full(n, 102.0),
            "low": np.full(n, 98.0),
            "close": np.full(n, 100.0),
        }
    )
    const_out = add_indicators(const_df)
    atr_const = const_out["atr_14"].dropna()
    assert len(atr_const) == n - 13
    assert np.allclose(atr_const, 4.0)

    # Volatile series with gaps -> ATR strictly positive after warmup.
    idx = np.arange(40, dtype=float)
    base = 200.0 + 3.0 * np.cos(idx * 0.5)
    vol_df = pd.DataFrame(
        {
            "open": base,
            "high": base + 2.5,
            "low": base - 2.0,
            "close": base + 0.5,
        }
    )
    vol_out = add_indicators(vol_df)
    atr_vol = vol_out["atr_14"].dropna()
    assert len(atr_vol) > 0
    assert (atr_vol > 0.0).all()


def test_add_indicators_empty_and_missing_columns() -> None:
    empty_df = pd.DataFrame(columns=["ts", "open", "high", "low", "close", "volume"])
    empty_out = add_indicators(empty_df)
    assert empty_out.empty
    for col in INDICATOR_COLUMNS:
        assert col in empty_out.columns

    with pytest.raises(ValueError, match="missing required OHLC columns"):
        add_indicators(pd.DataFrame({"close": [1.0, 2.0]}))
