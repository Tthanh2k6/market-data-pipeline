"""Unit tests for PostgREST payload building and offline upsert batching."""

from __future__ import annotations

import json
from typing import Any

import numpy as np
import pandas as pd
import pytest
import requests

from market_data.indicators import add_indicators
from market_data.store import build_payload, upsert


def test_build_payload_formats_utc_timestamps_and_nulls_non_finite_floats() -> None:
    df = pd.DataFrame(
        {
            "ts": [
                pd.Timestamp("2024-01-01 19:00:00-05:00"),
                pd.Timestamp("2024-01-01 23:00:00-05:00"),
            ],
            "open": [100.0, 102.5],
            "high": [105.0, 106.0],
            "low": [99.0, 101.0],
            "close": [102.0, 105.0],
            "volume": [1500.0, np.nan],
            "sma_20": [np.nan, 101.25],
            "sma_50": [np.nan, np.inf],
            "ema_20": [100.5, 101.8],
            "rsi_14": [np.nan, 58.4],
            "atr_14": [-np.inf, 3.2],
        }
    )

    payload = build_payload(
        df,
        symbol="SPX",
        asset_class="indices",
        timeframe="4H",
        source="yfinance",
    )

    assert len(payload) == 2
    first, second = payload

    assert first["symbol"] == "SPX"
    assert first["asset_class"] == "indices"
    assert first["timeframe"] == "4H"
    assert first["source"] == "yfinance"
    assert first["ts"] == "2024-01-02T00:00:00+00:00"
    assert first["open"] == pytest.approx(100.0)
    assert first["sma_20"] is None
    assert first["rsi_14"] is None
    assert first["atr_14"] is None

    assert second["ts"] == "2024-01-02T04:00:00+00:00"
    assert second["volume"] is None
    assert second["sma_20"] == pytest.approx(101.25)
    assert second["sma_50"] is None
    assert second["rsi_14"] == pytest.approx(58.4)
    assert second["atr_14"] == pytest.approx(3.2)

    # Must serialize strictly as RFC-compliant JSON with allow_nan=False.
    encoded = json.dumps(payload, allow_nan=False)
    assert isinstance(encoded, str)


def test_build_payload_localizes_naive_timestamps_and_handles_empty_input() -> None:
    assert build_payload(pd.DataFrame(), "BTCUSDT", "crypto", "1D", "binance") == []

    bars = pd.DataFrame(
        {
            "ts": pd.date_range("2024-01-01", periods=5, freq="1D"),
            "open": [40000.0, 40500.0, 41000.0, 40800.0, 41200.0],
            "high": [40600.0, 41200.0, 41500.0, 41300.0, 41800.0],
            "low": [39800.0, 40200.0, 40700.0, 40500.0, 41000.0],
            "close": [40500.0, 41000.0, 40800.0, 41200.0, 41600.0],
            "volume": [100.0, 120.0, 110.0, 95.0, 130.0],
        }
    )
    enriched = add_indicators(bars)
    rows = build_payload(enriched, "BTCUSDT", "crypto", "1D", "binance")
    assert len(rows) == 5
    assert rows[0]["ts"] == "2024-01-01T00:00:00+00:00"
    # With only 5 bars, 14/20/50-period indicators are still in warmup -> None in JSON payload.
    assert rows[-1]["sma_20"] is None
    assert rows[-1]["rsi_14"] is None

    with pytest.raises(ValueError, match="missing required columns"):
        build_payload(
            pd.DataFrame({"ts": [pd.Timestamp("2024-01-01", tz="UTC")]}),
            "SPX",
            "indices",
            "1D",
            "yfinance",
        )


def test_upsert_requires_env_and_batches_in_chunks_of_500(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)

    with pytest.raises(RuntimeError, match="SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY"):
        upsert([])

    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co/")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-service-role-key")

    assert upsert([]) == 0

    calls: list[dict[str, Any]] = []

    class _DummyResponse:
        status_code = 201

        def raise_for_status(self) -> None:
            return None

    def _fake_post(
        self: requests.Session,
        url: str,
        *,
        headers: dict[str, str],
        data: str,
        timeout: float,
    ) -> _DummyResponse:
        calls.append(
            {
                "url": url,
                "headers": headers,
                "rows": json.loads(data),
                "timeout": timeout,
            }
        )
        return _DummyResponse()

    monkeypatch.setattr(requests.Session, "post", _fake_post)

    sample_rows = [
        {
            "symbol": "BTCUSDT",
            "asset_class": "crypto",
            "timeframe": "4H",
            "ts": f"2024-01-01T00:00:{i % 60:02d}+00:00",
            "open": 42000.0,
            "high": 42100.0,
            "low": 41900.0,
            "close": 42050.0,
            "volume": 10.0,
            "sma_20": None,
            "sma_50": None,
            "ema_20": None,
            "rsi_14": None,
            "atr_14": None,
            "source": "binance",
        }
        for i in range(505)
    ]

    written = upsert(sample_rows)
    assert written == 505
    assert len(calls) == 2
    assert len(calls[0]["rows"]) == 500
    assert len(calls[1]["rows"]) == 5
    assert calls[0]["url"] == "https://example.supabase.co/rest/v1/market_bars?on_conflict=symbol,timeframe,ts"
    assert calls[0]["headers"]["apikey"] == "test-service-role-key"
    assert calls[0]["headers"]["Authorization"] == "Bearer test-service-role-key"
    assert calls[0]["headers"]["Prefer"] == "resolution=merge-duplicates,return=minimal"
