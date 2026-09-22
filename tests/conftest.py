from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def enable_insecure_test_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BIFURCATE_INSECURE_DEV_MODE", "1")
