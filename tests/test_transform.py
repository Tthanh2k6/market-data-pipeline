"""Unit tests for pure OHLCV normalization and 1H -> 4H resampling."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from market_data.sources import BINANCE_KLINE_COLUMNS
from market_data.transform import OHLCV_COLUMNS, normalize, resample_4h


def test_normalize_yfinance_csv_fixture(fixtures_dir: Path) -> None:
    raw = pd.read_csv(fixtures_dir / "yfinance_1h.csv")
    out = normalize(raw, source="yfinance")

    assert list(out.columns) == OHLCV_COLUMNS
    assert len(out) == 10
    assert str(out["ts"].dt.tz) == "UTC"
    assert out["ts"].is_monotonic_increasing

    # 2024-01-01 19:00:00-05:00 == 2024-01-02 00:00:00+00:00
    assert out["ts"].iloc[0] == pd.Timestamp("2024-01-02 00:00:00", tz="UTC")
    assert out["open"].iloc[0] == pytest.approx(100.0)
    assert out["high"].iloc[0] == pytest.approx(105.0)
    assert out["low"].iloc[0] == pytest.approx(99.0)
    assert out["close"].iloc[0] == pytest.approx(102.0)
    assert out["volume"].iloc[0] == pytest.approx(1000.0)


def test_normalize_yfinance_datetime_index_and_multiindex(fixtures_dir: Path) -> None:
    raw = pd.read_csv(fixtures_dir / "yfinance_1h.csv")
    indexed = raw.set_index(pd.to_datetime(raw["Datetime"])).drop(columns=["Datetime"])
    indexed.index.name = "Datetime"

    multi = indexed.copy()
    multi.columns = pd.MultiIndex.from_tuples([(col, "^GSPC") for col in multi.columns])

    out_idx = normalize(indexed, source="yfinance")
    out_multi = normalize(multi, source="yfinance")

    assert list(out_idx.columns) == OHLCV_COLUMNS
    assert list(out_idx["ts"]) == list(out_multi["ts"])
    pd.testing.assert_frame_equal(
        out_idx.drop(columns=["ts"]),
        out_multi.drop(columns=["ts"]),
    )


def test_normalize_binance_json_fixture(fixtures_dir: Path) -> None:
    raw_rows = json.loads((fixtures_dir / "binance_klines.json").read_text(encoding="utf-8"))

    named_df = pd.DataFrame(raw_rows, columns=BINANCE_KLINE_COLUMNS)
    positional_df = pd.DataFrame(raw_rows)

    out_named = normalize(named_df, source="binance")
    out_pos = normalize(positional_df, source="binance")

    assert list(out_named.columns) == OHLCV_COLUMNS
    assert len(out_named) == 4
    assert str(out_named["ts"].dt.tz) == "UTC"
    assert out_named["ts"].iloc[0] == pd.Timestamp("2024-01-01 00:00:00", tz="UTC")
    assert out_named["ts"].iloc[-1] == pd.Timestamp("2024-01-01 12:00:00", tz="UTC")

    assert out_named["open"].iloc[0] == pytest.approx(42283.58)
    assert out_named["high"].iloc[0] == pytest.approx(42554.57)
    assert out_named["low"].iloc[0] == pytest.approx(42180.00)
    assert out_named["close"].iloc[0] == pytest.approx(42475.23)
    assert out_named["volume"].iloc[0] == pytest.approx(4521.125)

    assert list(out_named["ts"]) == list(out_pos["ts"])
    pd.testing.assert_frame_equal(
        out_named.drop(columns=["ts"]),
        out_pos.drop(columns=["ts"]),
    )


def test_normalize_empty_and_unsupported_source() -> None:
    empty_out = normalize(pd.DataFrame(), source="yfinance")
    assert empty_out.empty
    assert list(empty_out.columns) == OHLCV_COLUMNS
    assert str(empty_out["ts"].dt.tz) == "UTC"

    with pytest.raises(ValueError, match="Unsupported source"):
        normalize(pd.DataFrame(), source="unknown_provider")


def test_resample_4h_boundaries_and_drops_incomplete_last_bar(fixtures_dir: Path) -> None:
    raw = pd.read_csv(fixtures_dir / "yfinance_1h.csv")
    df_1h = normalize(raw, source="yfinance")

    # Fixture covers 2024-01-02 00:00..09:00 UTC:
    # - 00:00..03:00 UTC -> complete 4H bar
    # - 04:00..07:00 UTC -> complete 4H bar
    # - 08:00..09:00 UTC -> only 2 hourly bars (incomplete -> must be dropped)
    bars_4h = resample_4h(df_1h)

    assert list(bars_4h.columns) == OHLCV_COLUMNS
    assert len(bars_4h) == 2
    assert list(bars_4h["ts"]) == [
        pd.Timestamp("2024-01-02 00:00:00", tz="UTC"),
        pd.Timestamp("2024-01-02 04:00:00", tz="UTC"),
    ]

    first = bars_4h.iloc[0]
    assert first["open"] == pytest.approx(100.0)
    assert first["high"] == pytest.approx(108.0)
    assert first["low"] == pytest.approx(95.0)
    assert first["close"] == pytest.approx(103.0)
    assert first["volume"] == pytest.approx(5700.0)

    second = bars_4h.iloc[1]
    assert second["open"] == pytest.approx(103.0)
    assert second["high"] == pytest.approx(115.0)
    assert second["low"] == pytest.approx(102.0)
    assert second["close"] == pytest.approx(108.0)
    assert second["volume"] == pytest.approx(5400.0)


def test_resample_4h_anchored_at_start_day_utc() -> None:
    # Input starts at 02:00 UTC (mid-bucket). With origin="start_day", buckets anchor
    # at 00:00 and 04:00 UTC rather than 02:00 and 06:00 UTC.
    timestamps = pd.date_range("2024-01-03 02:00:00", periods=6, freq="1h", tz="UTC")
    df_1h = pd.DataFrame(
        {
            "ts": timestamps,
            "open": [10.0, 11.0, 20.0, 21.0, 22.0, 23.0],
            "high": [12.0, 13.0, 25.0, 26.0, 24.0, 27.0],
            "low": [9.0, 10.0, 19.0, 20.0, 18.0, 22.0],
            "close": [11.0, 12.0, 21.0, 22.0, 23.0, 26.0],
            "volume": [100.0, 100.0, 200.0, 300.0, 400.0, 500.0],
        }
    )

    bars_4h = resample_4h(df_1h)
    assert list(bars_4h["ts"]) == [
        pd.Timestamp("2024-01-03 00:00:00", tz="UTC"),
        pd.Timestamp("2024-01-03 04:00:00", tz="UTC"),
    ]
    assert bars_4h.iloc[1]["open"] == pytest.approx(20.0)
    assert bars_4h.iloc[1]["high"] == pytest.approx(27.0)
    assert bars_4h.iloc[1]["low"] == pytest.approx(18.0)
    assert bars_4h.iloc[1]["close"] == pytest.approx(26.0)
    assert bars_4h.iloc[1]["volume"] == pytest.approx(1400.0)


def test_resample_4h_single_incomplete_bucket_returns_empty() -> None:
    df_1h = pd.DataFrame(
        {
            "ts": pd.date_range("2024-01-03 00:00:00", periods=3, freq="1h", tz="UTC"),
            "open": [100.0, 101.0, 102.0],
            "high": [101.0, 102.0, 103.0],
            "low": [99.0, 100.0, 101.0],
            "close": [100.5, 101.5, 102.5],
            "volume": [10.0, 20.0, 30.0],
        }
    )
    out = resample_4h(df_1h)
    assert out.empty
    assert list(out.columns) == OHLCV_COLUMNS
