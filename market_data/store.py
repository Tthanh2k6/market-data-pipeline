"""PostgREST payload builder and batched Supabase upsert writer."""

from __future__ import annotations

from collections.abc import Sequence
import json
import logging
import math
import os
import time
from typing import Any

import pandas as pd
import requests

logger = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 500
DEFAULT_TIMEOUT_SECONDS = 30.0
MAX_RETRIES = 3
BASE_BACKOFF_SECONDS = 1.0
_RETRYABLE_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})

_NUMERIC_FIELDS = (
    "open",
    "high",
    "low",
    "close",
    "volume",
    "sma_20",
    "sma_50",
    "ema_20",
    "rsi_14",
    "atr_14",
)


def _clean_float(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    num = float(value)
    if not math.isfinite(num):
        return None
    return num


def _format_utc_ts(value: Any) -> str:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    else:
        ts = ts.tz_convert("UTC")
    return ts.isoformat()


def build_payload(
    df: pd.DataFrame,
    symbol: str,
    asset_class: str,
    timeframe: str,
    source: str,
) -> list[dict[str, Any]]:
    """Convert an enriched OHLCV DataFrame into JSON-safe PostgREST row dicts (pure, no I/O)."""
    if df.empty:
        return []

    required = ("ts", "open", "high", "low", "close", "volume")
    missing = [col for col in required if col not in df.columns]
    if missing:
        raise ValueError(f"DataFrame is missing required columns for payload: {missing}")

    rows: list[dict[str, Any]] = []
    for record in df.to_dict(orient="records"):
        row: dict[str, Any] = {
            "symbol": symbol,
            "asset_class": asset_class,
            "timeframe": timeframe,
            "ts": _format_utc_ts(record["ts"]),
            "source": source,
        }
        for col in _NUMERIC_FIELDS:
            row[col] = _clean_float(record.get(col))
        rows.append(row)

    return rows


def _post_batch(
    session: requests.Session,
    url: str,
    headers: dict[str, str],
    body: str,
    timeout: float,
) -> None:
    last_exc: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.post(url, headers=headers, data=body, timeout=timeout)
            if resp.status_code in _RETRYABLE_STATUS_CODES and attempt < MAX_RETRIES:
                delay = BASE_BACKOFF_SECONDS * (2 ** (attempt - 1))
                logger.warning(
                    "Supabase PostgREST returned HTTP %d on attempt %d/%d; retrying in %.1fs",
                    resp.status_code,
                    attempt,
                    MAX_RETRIES,
                    delay,
                )
                time.sleep(delay)
                continue
            resp.raise_for_status()
            return
        except requests.RequestException as exc:
            last_exc = exc
            status = getattr(exc.response, "status_code", None)
            if status is not None and status not in _RETRYABLE_STATUS_CODES:
                raise
            if attempt >= MAX_RETRIES:
                break
            delay = BASE_BACKOFF_SECONDS * (2 ** (attempt - 1))
            logger.warning(
                "Supabase upsert request failed on attempt %d/%d (%s); retrying in %.1fs",
                attempt,
                MAX_RETRIES,
                type(exc).__name__,
                delay,
            )
            time.sleep(delay)

    assert last_exc is not None
    raise last_exc


def upsert(
    rows: Sequence[dict[str, Any]],
    table: str = "market_bars",
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> int:
    """Upsert rows into Supabase via PostgREST in batches of 500."""
    supabase_url = os.environ.get("SUPABASE_URL", "").strip()
    service_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not supabase_url or not service_key:
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY environment variables must be set"
        )

    if batch_size <= 0:
        raise ValueError("batch_size must be positive")

    if not rows:
        return 0

    url = f"{supabase_url.rstrip('/')}/rest/v1/{table}?on_conflict=symbol,timeframe,ts"
    headers = {
        "apikey": service_key,
        "Authorization": f"Bearer {service_key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }

    total_written = 0
    with requests.Session() as session:
        for offset in range(0, len(rows), batch_size):
            batch = list(rows[offset : offset + batch_size])
            body = json.dumps(batch, allow_nan=False)
            _post_batch(session, url, headers, body, timeout)
            total_written += len(batch)

    return total_written
