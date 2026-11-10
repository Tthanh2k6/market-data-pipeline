"""Pure OHLCV validation and gap reporting."""

from __future__ import annotations

import pandas as pd

TIMEFRAME_DELTAS: dict[str, pd.Timedelta] = {
    "1H": pd.Timedelta(hours=1),
    "4H": pd.Timedelta(hours=4),
    "1D": pd.Timedelta(days=1),
    "1W": pd.Timedelta(days=7),
}

_REQUIRED_COLUMNS = ("ts", "open", "high", "low", "close", "volume")
_MONDAY_ANCHOR = pd.Timestamp("1970-01-05 00:00:00", tz="UTC")
_WEEK_SECONDS = 7 * 86400.0
_WEEKDAY_SECONDS = 5 * 86400.0


def _weekday_seconds_since_anchor(ts: pd.Timestamp) -> float:
    """Elapsed non-weekend seconds (Mon 00:00 UTC .. Sat 00:00 UTC) since Monday anchor."""
    utc_ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
    elapsed = (utc_ts - _MONDAY_ANCHOR).total_seconds()
    weeks, rem = divmod(elapsed, _WEEK_SECONDS)
    return weeks * _WEEKDAY_SECONDS + min(rem, _WEEKDAY_SECONDS)


def _effective_gap(t0: pd.Timestamp, t1: pd.Timestamp, *, exclude_weekends: bool) -> pd.Timedelta:
    if not exclude_weekends:
        return t1 - t0
    secs = _weekday_seconds_since_anchor(t1) - _weekday_seconds_since_anchor(t0)
    return pd.Timedelta(seconds=max(0.0, secs))


def validate(
    df: pd.DataFrame,
    timeframe: str,
    asset_class: str | None = None,
) -> tuple[pd.DataFrame, list[str]]:
    """Clean invalid OHLCV rows and report anomalies (including >3x timeframe gaps).

    Gaps are reported in ``issues`` only and never cause validation to fail or drop rows.
    """
    tf_key = timeframe.strip().upper()
    tf_delta = TIMEFRAME_DELTAS.get(tf_key)
    if tf_delta is None:
        raise ValueError(
            f"Unsupported timeframe {timeframe!r}; expected one of {sorted(TIMEFRAME_DELTAS)}"
        )

    missing = [col for col in _REQUIRED_COLUMNS if col not in df.columns]
    if missing:
        raise ValueError(f"DataFrame is missing required columns for validation: {missing}")

    issues: list[str] = []
    if df.empty:
        return df.copy(), issues

    clean = df.copy()

    # 1. Duplicate timestamps (keep latest entry for a timestamp).
    dup_mask = clean["ts"].notna() & clean.duplicated(subset=["ts"], keep="last")
    dup_count = int(dup_mask.sum())
    if dup_count > 0:
        issues.append(f"Dropped {dup_count} duplicate timestamp row(s)")
        clean = clean.loc[~dup_mask].copy()

    # 2. Missing timestamp or NaN OHLC values.
    nan_mask = clean[["ts", "open", "high", "low", "close"]].isna().any(axis=1)
    nan_count = int(nan_mask.sum())
    if nan_count > 0:
        issues.append(f"Dropped {nan_count} row(s) with NaN OHLC or timestamp")
        clean = clean.loc[~nan_mask].copy()

    if clean.empty:
        return clean.reset_index(drop=True), issues

    # 3. Impossible OHLC bounds and negative volume.
    bar_max_oc = clean[["open", "close"]].max(axis=1)
    bar_min_oc = clean[["open", "close"]].min(axis=1)
    bad_ohlc_mask = (clean["high"] < bar_max_oc) | (clean["low"] > bar_min_oc)
    bad_ohlc_count = int(bad_ohlc_mask.sum())
    if bad_ohlc_count > 0:
        issues.append(
            f"Dropped {bad_ohlc_count} row(s) with invalid OHLC bounds "
            "(high < max(open, close) or low > min(open, close))"
        )

    neg_vol_mask = clean["volume"].notna() & (clean["volume"] < 0)
    neg_vol_count = int(neg_vol_mask.sum())
    if neg_vol_count > 0:
        issues.append(f"Dropped {neg_vol_count} row(s) with negative volume")

    drop_mask = bad_ohlc_mask | neg_vol_mask
    if drop_mask.any():
        clean = clean.loc[~drop_mask].copy()

    clean["ts"] = pd.to_datetime(clean["ts"], utc=True)
    clean = clean.sort_values("ts", kind="mergesort").reset_index(drop=True)

    # 4. Report gaps larger than 3x timeframe (exclude Sat-Sun UTC for non-crypto sub-weekly bars).
    if len(clean) >= 2:
        is_crypto = (asset_class or "").strip().lower() == "crypto"
        exclude_weekends = (not is_crypto) and (tf_key != "1W")
        gap_threshold = 3 * tf_delta
        if not is_crypto and tf_key == "4H":
            # Exchanges close overnight; only a missing trading day counts as a gap.
            gap_threshold = max(gap_threshold, pd.Timedelta(hours=24))

        timestamps = clean["ts"].tolist()
        for prev_ts, curr_ts in zip(timestamps[:-1], timestamps[1:]):
            eff_gap = _effective_gap(prev_ts, curr_ts, exclude_weekends=exclude_weekends)
            if eff_gap > gap_threshold:
                issues.append(
                    f"Gap > 3x {tf_key} between {prev_ts.isoformat()} and "
                    f"{curr_ts.isoformat()} (effective={eff_gap})"
                )

    return clean, issues
