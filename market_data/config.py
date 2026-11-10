"""Load and validate symbol configuration from symbols.yaml."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import yaml

VALID_SOURCES = frozenset({"yfinance", "binance"})
DEFAULT_SYMBOLS_PATH = Path(__file__).resolve().parent.parent / "symbols.yaml"


@dataclass(frozen=True, slots=True)
class Symbol:
    """Configured market instrument."""

    asset_class: str
    symbol: str
    source: str
    provider_symbol: str


def load_symbols(path: str | Path = DEFAULT_SYMBOLS_PATH) -> list[Symbol]:
    """Parse symbols YAML file into a list of validated Symbol objects."""
    config_path = Path(path)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))

    if isinstance(raw, dict):
        entries = raw.get("symbols")
    elif isinstance(raw, list):
        entries = raw
    else:
        raise ValueError(f"Invalid symbols configuration in {config_path}: expected mapping or list")

    if not isinstance(entries, list) or not entries:
        raise ValueError(f"Invalid symbols configuration in {config_path}: 'symbols' must be a non-empty list")

    symbols: list[Symbol] = []
    seen: set[str] = set()

    for idx, item in enumerate(entries):
        if not isinstance(item, dict):
            raise ValueError(f"Entry #{idx} in {config_path} must be a mapping")

        fields: dict[str, str] = {}
        for key in ("asset_class", "symbol", "source", "provider_symbol"):
            val = item.get(key)
            if not isinstance(val, str) or not val.strip():
                raise ValueError(f"Entry #{idx} in {config_path} has missing or invalid '{key}'")
            fields[key] = val.strip()

        source = fields["source"].lower()
        if source not in VALID_SOURCES:
            raise ValueError(
                f"Entry #{idx} ({fields['symbol']}) has unsupported source {fields['source']!r}; "
                f"expected one of {sorted(VALID_SOURCES)}"
            )

        if fields["symbol"] in seen:
            raise ValueError(f"Duplicate symbol {fields['symbol']!r} in {config_path}")
        seen.add(fields["symbol"])

        symbols.append(
            Symbol(
                asset_class=fields["asset_class"],
                symbol=fields["symbol"],
                source=source,
                provider_symbol=fields["provider_symbol"],
            )
        )

    return symbols
