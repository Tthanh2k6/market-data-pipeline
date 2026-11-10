"""Unit tests for OHLCV row validation and gap reporting."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from market_data.validate import validate


def _make_bars(rows: list[tuple[str, float, float, float, float, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "ts": pd.Timestamp(ts, tz="UTC") if ts else pd.NaT,
                "open": o,
                "high": h,
                "low": l,
                "close": c,
                "volume": v,
            }
            for ts, o, h, l, c, v in rows
        ]
    )


def test_validate_drops_duplicate_timestamps_keeping_last() -> None:
    df = _make_bars(
        [
            ("2024-01-02 00:00:00", 100.0, 105.0, 99.0, 101.0, 1000.0),
            ("2024-01-02 00:00:00", 100.0, 106.0, 98.0, 104.0, 1200.0),
            ("2024-01-03 00:00:00", 104.0, 108.0, 103.0, 107.0, 900.0),
        ]
    )

    clean, issues = validate(df, timeframe="1D")
    assert len(clean) == 2
    assert clean.iloc[0]["close"] == pytest.approx(104.0)
    assert any("duplicate timestamp" in msg for msg in issues)


def test_validate_drops_nan_ohlc_and_allows_nan_volume() -> None:
    df = _make_bars(
        [
            ("2024-01-02 00:00:00", 100.0, 105.0, 99.0, 102.0, np.nan),
            ("2024-01-03 00:00:00", 102.0, np.nan, 100.0, 103.0, 500.0),
            ("", 103.0, 106.0, 101.0, 104.0, 600.0),
        ]
    )

    clean, issues = validate(df, timeframe="1D")
    assert len(clean) == 1
    assert pd.isna(clean.iloc[0]["volume"])
    assert any("Dropped 2 row(s) with NaN OHLC or timestamp" in msg for msg in issues)


def test_validate_drops_invalid_ohlc_bounds() -> None:
    df = _make_bars(
        [
            # Valid bar
            ("2024-01-02 00:00:00", 100.0, 105.0, 99.0, 103.0, 1000.0),
            # Invalid: high (102) < max(open=100, close=104)
            ("2024-01-03 00:00:00", 100.0, 102.0, 99.0, 104.0, 1000.0),
            # Invalid: low (101) > min(open=100, close=103)
            ("2024-01-04 00:00:00", 100.0, 105.0, 101.0, 103.0, 1000.0),
        ]
    )

    clean, issues = validate(df, timeframe="1D")
    assert len(clean) == 1
    assert clean.iloc[0]["ts"] == pd.Timestamp("2024-01-02 00:00:00", tz="UTC")
    assert any("Dropped 2 row(s) with invalid OHLC bounds" in msg for msg in issues)


def test_validate_drops_negative_volume() -> None:
    df = _make_bars(
        [
            ("2024-01-02 00:00:00", 100.0, 105.0, 99.0, 102.0, 500.0),
            ("2024-01-03 00:00:00", 102.0, 106.0, 101.0, 104.0, -10.0),
        ]
    )

    clean, issues = validate(df, timeframe="1D")
    assert len(clean) == 1
    assert any("Dropped 1 row(s) with negative volume" in msg for msg in issues)


def test_validate_gap_reporting_excludes_weekends_for_non_crypto() -> None:
    # Friday 20:00 UTC -> Monday 00:00 UTC is 52 wall-clock hours,
    # but only 4 non-weekend hours (<= 3 * 4H = 12h).
    weekend_span = _make_bars(
        [
            ("2024-01-05 20:00:00", 1.10, 1.11, 1.09, 1.10, 0.0),
            ("2024-01-08 00:00:00", 1.10, 1.12, 1.09, 1.11, 0.0),
        ]
    )

    clean_fx, issues_fx = validate(weekend_span, timeframe="4H", asset_class="forex")
    assert len(clean_fx) == 2
    assert issues_fx == []

    # For crypto (24/7), the same 52-hour span exceeds 3 * 4H and is reported without dropping rows.
    clean_crypto, issues_crypto = validate(weekend_span, timeframe="4H", asset_class="crypto")
    assert len(clean_crypto) == 2
    assert len(issues_crypto) == 1
    assert "Gap > 3x 4H" in issues_crypto[0]


def test_validate_reports_large_weekday_gap_without_failing() -> None:
    # Monday 2024-01-08 -> Monday 2024-01-15 has 5 weekday days (> 3 * 1D).
    df = _make_bars(
        [
            ("2024-01-08 00:00:00", 100.0, 102.0, 99.0, 101.0, 1000.0),
            ("2024-01-15 00:00:00", 101.0, 105.0, 100.0, 104.0, 1100.0),
        ]
    )

    clean, issues = validate(df, timeframe="1D", asset_class="indices")
    assert len(clean) == 2
    assert len(issues) == 1
    assert "Gap > 3x 1D" in issues[0]


def test_validate_rejects_unsupported_timeframe() -> None:
    df = _make_bars([("2024-01-02 00:00:00", 100.0, 101.0, 99.0, 100.5, 10.0)])
    with pytest.raises(ValueError, match="Unsupported timeframe"):
        validate(df, timeframe="5M")


def test_overnight_close_is_not_reported_as_gap_for_non_crypto_4h():
    import pandas as pd
    from market_data.validate import validate
    ts = pd.to_datetime(["2026-11-03T12:00Z", "2026-11-03T16:00Z", "2026-11-04T12:00Z"], utc=True)
    df = pd.DataFrame({"ts": ts, "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 10.0})
    _, issues = validate(df, timeframe="4H", asset_class="indices")
    assert not any("Gap" in i for i in issues)
    _, issues = validate(df, timeframe="4H", asset_class="crypto")
    assert any("Gap" in i for i in issues)
