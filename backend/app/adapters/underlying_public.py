"""GET-only adapters for public, non-Binance underlying market data."""

import os
from datetime import date

import httpx


class UnderlyingDataError(RuntimeError):
    """A sanitized provider failure that never includes credentials or URLs."""


class MassiveStocksPublic:
    """Historical and recent US stock/ETF aggregates from Massive."""

    base_url = "https://api.massive.com"

    def __init__(self, api_key=None, transport=None):
        """Create a GET-only client using a private environment key.

        Args:
            api_key: Optional test-only key override; production reads the
                `MASSIVE_API_KEY` environment variable and never returns it.
            transport: Optional HTTPX transport for deterministic adapter tests.

        Returns:
            A client wrapper; constructing it performs no network request.
        """
        self._api_key = (api_key if api_key is not None else os.environ.get("MASSIVE_API_KEY", "")).strip()
        self._transport = transport

    def aggregates(self, symbol, interval, start_date, end_date):
        """Request one exact stock ticker's native OHLCV window.

        Args:
            symbol: Provider symbol bound by the server asset registry.
            interval: One of `15m`, `1h`, or `1d`.
            start_date: Inclusive session date in ISO `YYYY-MM-DD` format.
            end_date: Inclusive session date in ISO `YYYY-MM-DD` format.

        Returns:
            Provider aggregate objects in ascending time order.

        Raises:
            UnderlyingDataError: Credentials, network, provider or response fail.
        """
        if not self._api_key:
            raise UnderlyingDataError("MASSIVE_API_KEY未配置")
        resolution = {"15m": (15, "minute"), "1h": (1, "hour"), "1d": (1, "day")}.get(interval)
        if resolution is None:
            raise UnderlyingDataError("Massive不支持此请求周期")
        try:
            date.fromisoformat(start_date)
            date.fromisoformat(end_date)
        except ValueError:
            raise UnderlyingDataError("行情日期格式无效") from None
        multiplier, timespan = resolution
        path = f"/v2/aggs/ticker/{symbol}/range/{multiplier}/{timespan}/{start_date}/{end_date}"
        params = {"adjusted": "true", "sort": "asc", "limit": 50000, "apiKey": self._api_key}
        return self._get_results(path, params, "Massive")

    def _get_results(self, path, params, provider):
        """Fetch and validate a provider response without exposing request secrets.

        Args:
            path: Fixed API route created by the adapter, never client supplied.
            params: Provider query parameters, including a server-side key.
            provider: Constant provider label used for safe error messages.

        Returns:
            Result objects from a successful JSON response.
        """
        try:
            with httpx.Client(timeout=25, follow_redirects=False, transport=self._transport) as client:
                response = client.get(self.base_url + path, params=params)
            if response.status_code != 200:
                raise UnderlyingDataError(f"{provider}公开接口HTTP {response.status_code}")
            body = response.json()
            if not isinstance(body, dict) or body.get("status") not in {"OK", "DELAYED", "DELISTED"}:
                raise UnderlyingDataError(f"{provider}返回非成功行情状态")
            values = body.get("results")
            if not isinstance(values, list):
                raise UnderlyingDataError(f"{provider}行情结构无效")
            return values
        except UnderlyingDataError:
            raise
        except (httpx.HTTPError, ValueError, TypeError):
            raise UnderlyingDataError(f"{provider}公开连接或JSON解析失败") from None


class TwelveDataPublic:
    """Daily market bars for supported non-US exchanges such as XKRX."""

    base_url = "https://api.twelvedata.com/time_series"

    def __init__(self, api_key=None, transport=None):
        """Create a GET-only Twelve Data client with private server credentials.

        Args:
            api_key: Optional test-only key override; production reads
                `TWELVE_DATA_API_KEY` from the private service environment.
            transport: Optional HTTPX transport for deterministic tests.

        Returns:
            A client wrapper; no network request occurs during construction.
        """
        self._api_key = (api_key if api_key is not None else os.environ.get("TWELVE_DATA_API_KEY", "")).strip()
        self._transport = transport

    def daily_bars(self, symbol, mic_code, start_date, end_date):
        """Request daily OHLCV from the registered exchange and exact symbol.

        Args:
            symbol: Exchange-native symbol from the server asset registry.
            mic_code: Registered ISO market identifier, such as `XKRX`.
            start_date: Inclusive ISO date at the exchange.
            end_date: Inclusive ISO date at the exchange.

        Returns:
            Response metadata and daily value objects from Twelve Data.

        Raises:
            UnderlyingDataError: Credentials, network, plan or response fail.
        """
        if not self._api_key:
            raise UnderlyingDataError("TWELVE_DATA_API_KEY未配置")
        params = {
            "symbol": symbol, "mic_code": mic_code, "interval": "1day",
            "start_date": start_date, "end_date": end_date,
            "outputsize": 5000, "order": "ASC", "apikey": self._api_key,
        }
        try:
            with httpx.Client(timeout=25, follow_redirects=False, transport=self._transport) as client:
                response = client.get(self.base_url, params=params)
            if response.status_code != 200:
                raise UnderlyingDataError(f"Twelve Data公开接口HTTP {response.status_code}")
            body = response.json()
            if not isinstance(body, dict) or body.get("status") == "error" or not isinstance(body.get("values"), list):
                raise UnderlyingDataError("Twelve Data未返回可用日线；检查市场权限和配额")
            return body
        except UnderlyingDataError:
            raise
        except (httpx.HTTPError, ValueError, TypeError):
            raise UnderlyingDataError("Twelve Data公开连接或JSON解析失败") from None


class EIAOpenDataPublic:
    """Daily energy spot reference observations from the EIA Open Data API."""

    base_url = "https://api.eia.gov/v2"

    def __init__(self, api_key=None, transport=None):
        """Create a GET-only EIA client with a private server-side API key.

        Args:
            api_key: Optional test-only key override; production reads `EIA_API_KEY`.
            transport: Optional HTTPX transport used by deterministic tests.

        Returns:
            A client wrapper without opening a network connection.
        """
        self._api_key = (api_key if api_key is not None else os.environ.get("EIA_API_KEY", "")).strip()
        self._transport = transport

    def daily_observations(self, route, series, start_date, end_date, *, offset=0, length=5000):
        """Request one explicitly registered daily series without OHLC fabrication.

        Args:
            route: Allowlisted EIA route from the source registry.
            series: Exact EIA series code from the source registry.
            start_date: Inclusive observation date in ISO format.
            end_date: Inclusive observation date in ISO format.
            offset: Nonnegative provider pagination offset.
            length: Maximum requested rows, capped at EIA's JSON response limit.

        Returns:
            EIA daily data rows containing period and scalar value fields.

        Raises:
            UnderlyingDataError: Missing credentials, invalid response or API failure.
        """
        if not self._api_key:
            raise UnderlyingDataError("EIA_API_KEY未配置")
        if route not in {"petroleum/pri/spt", "natural-gas/pri/fut"} or series not in {"RWTC", "RNGWHHD"}:
            raise UnderlyingDataError("EIA序列未在本地来源白名单中")
        if offset < 0 or not 1 <= length <= 5000:
            raise UnderlyingDataError("EIA分页范围无效")
        params = {
            "api_key": self._api_key, "frequency": "daily", "data[0]": "value",
            "facets[series][]": series, "start": start_date, "end": end_date,
            "sort[0][column]": "period", "sort[0][direction]": "asc",
            "offset": offset, "length": length,
        }
        try:
            with httpx.Client(timeout=25, follow_redirects=False, transport=self._transport) as client:
                response = client.get(f"{self.base_url}/{route}/data/", params=params)
            if response.status_code != 200:
                raise UnderlyingDataError(f"EIA公开接口HTTP {response.status_code}")
            body = response.json()
            data = body.get("response", {}).get("data") if isinstance(body, dict) else None
            if not isinstance(data, list):
                raise UnderlyingDataError("EIA未返回可用日度参考值")
            return data
        except UnderlyingDataError:
            raise
        except (httpx.HTTPError, ValueError, TypeError):
            raise UnderlyingDataError("EIA公开连接或JSON解析失败") from None
