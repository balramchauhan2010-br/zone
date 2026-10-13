"""Provider protocol and normalized provider errors."""
from __future__ import annotations

from typing import Any, Protocol

import pandas as pd


class ProviderError(RuntimeError):
    def __init__(self, message: str, code: str = "PROVIDER_ERROR", retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class MarketDataProvider(Protocol):
    name: str

    def status(self) -> dict[str, Any]: ...

    def get_quotes(self, instruments: list[dict[str, str]]) -> dict[str, Any]: ...

    def get_candles(
        self,
        instrument: dict[str, Any],
        from_dt: str,
        to_dt: str,
        intraday: bool = True,
        interval: str = "1",
        include_oi: bool = False,
    ) -> pd.DataFrame: ...
