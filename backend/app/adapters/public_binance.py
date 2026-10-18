"""Public GET endpoints only; this adapter has no key, signing or order methods."""
import httpx

INTERVALS = ['1m','3m','5m','15m','30m','1h','2h','4h','6h','12h','1d','1w','1M']
class PublicDataError(RuntimeError):
    pass

class BinancePublic:
    base_url = 'https://fapi.binance.com'
    def get(self, path: str, params: dict | None = None):
        try:
            with httpx.Client(timeout=15, follow_redirects=False) as client:
                response = client.get(self.base_url + path, params=params)
                if response.status_code != 200:
                    raise PublicDataError(f'Binance 公开接口 HTTP {response.status_code}；保留原有缓存，不更换数据来源')
                return response.json()
        except httpx.HTTPError as exc:
            reason='DNS 解析失败' if 'Name or service not known' in str(exc) else 'TLS 证书验证失败' if 'CERTIFICATE_VERIFY_FAILED' in str(exc) else type(exc).__name__
            raise PublicDataError(f'Binance 公开连接失败：{reason}') from None
    def exchange_info(self):
        return self.get('/fapi/v1/exchangeInfo')
    def klines(self, symbol: str, interval: str, start: int, end: int):
        return self.get('/fapi/v1/klines', {'symbol':symbol, 'interval':interval, 'startTime':start, 'endTime':end, 'limit':1000})
