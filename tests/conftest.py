"""Pytest configuration: repository import path and offline network guard."""

from __future__ import annotations

from pathlib import Path
import sys

import pytest
import requests.adapters

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(autouse=True)
def _disallow_http_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    def _blocked_send(*args: object, **kwargs: object) -> None:
        raise AssertionError("Outbound HTTP calls are forbidden in unit tests")

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", _blocked_send)


@pytest.fixture()
def fixtures_dir() -> Path:
    return FIXTURES_DIR
