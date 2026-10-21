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


class SinaChinaIndicesPublic:
    """Read daily A-share index bars from Sina's unauthenticated public endpoint."""

    base_url = "https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData"

    def __init__(self, transport=None):
        """Create a GET-only Sina client without credentials or eager I/O.

        Args:
            transport: Optional HTTPX transport for deterministic tests.

        Returns:
            A public-data client; no network connection is opened at construction.
        """
        self._transport = transport

    def daily_bars(self, symbol, start_date, end_date):
        """Request the most recent native daily bars for a registered index.

        Args:
            symbol: Exact Sina code from the server-owned asset registry.
            start_date: Inclusive ISO session date requested by the ingestion job.
            end_date: Inclusive ISO session date requested by the ingestion job.

        Returns:
            Sina JSON objects with date and OHLCV fields; callers enforce date bounds.

        Raises:
            UnderlyingDataError: HTTP, network, or response-shape failure occurs.
        """
        if not symbol.startswith(("sh", "sz")) or not symbol[2:].isdigit():
            raise UnderlyingDataError("新浪指数代码不在服务端白名单格式内")
        params = {"symbol": symbol, "scale": "240", "ma": "no", "datalen": "1023"}
        try:
            with httpx.Client(timeout=20, follow_redirects=False, transport=self._transport,
                              headers={"Referer": "https://finance.sina.com.cn/", "User-Agent": "Mozilla/5.0"}) as client:
                response = client.get(self.base_url, params=params)
            if response.status_code != 200:
                raise UnderlyingDataError(f"新浪公开行情HTTP {response.status_code}")
            body = response.json()
            if not isinstance(body, list):
                raise UnderlyingDataError("新浪未返回可用指数日线")
            return body
        except UnderlyingDataError:
            raise
        except httpx.HTTPError as exc:
            raise UnderlyingDataError(f"新浪公开行情网络失败({type(exc).__name__})") from None
        except (ValueError, TypeError) as exc:
            raise UnderlyingDataError(f"新浪公开行情响应解析失败({type(exc).__name__})") from None


class TencentChinaIndicesPublic:
    """Read daily A-share index bars from Tencent's unauthenticated fallback endpoint."""

    base_url = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"

    def __init__(self, transport=None):
        """Create a GET-only Tencent client without credentials or eager I/O.

        Args:
            transport: Optional HTTPX transport for deterministic tests.

        Returns:
            A public-data client; no network connection is opened at construction.
        """
        self._transport = transport

    def daily_bars(self, symbol, start_date, end_date):
        """Request recent daily index bars for a registered Sina-compatible symbol.

        Args:
            symbol: Exact `sh`/`sz` provider symbol from the server-owned registry.
            start_date: Inclusive ISO session date requested by ingestion.
            end_date: Inclusive ISO session date requested by ingestion.

        Returns:
            A normalized list of OHLCV dictionaries in source order.

        Raises:
            UnderlyingDataError: HTTP, network, or response-shape failure occurs.
        """
        if not symbol.startswith(("sh", "sz")) or not symbol[2:].isdigit():
            raise UnderlyingDataError("腾讯指数代码不在服务端白名单格式内")
        params = {"param": f"{symbol},day,,,1023"}
        try:
            with httpx.Client(timeout=20, follow_redirects=False, transport=self._transport) as client:
                response = client.get(self.base_url, params=params)
            if response.status_code != 200:
                raise UnderlyingDataError(f"腾讯备用行情HTTP {response.status_code}")
            body = response.json()
            ticker = body.get("data", {}).get(symbol, {}) if isinstance(body, dict) else {}
            rows = ticker.get("day") if isinstance(ticker, dict) else None
            if not isinstance(rows, list):
                raise UnderlyingDataError("腾讯备用源未返回可用指数日线")
            normalized = []
            for row in rows:
                if not isinstance(row, list) or len(row) < 6:
                    raise UnderlyingDataError("腾讯备用源指数日线结构无效")
                day, open_value, close_value, high_value, low_value, volume = row[:6]
                normalized.append({"day": day, "open": open_value, "high": high_value,
                                   "low": low_value, "close": close_value, "volume": volume})
            return normalized
        except UnderlyingDataError:
            raise
        except httpx.HTTPError as exc:
            raise UnderlyingDataError(f"腾讯备用行情网络失败({type(exc).__name__})") from None
        except (ValueError, TypeError, AttributeError) as exc:
            raise UnderlyingDataError(f"腾讯备用行情响应解析失败({type(exc).__name__})") from None


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
