"""Public GET endpoints only; this adapter has no key, signing or order methods."""
import httpx

INTERVALS = ['1m','3m','5m','15m','30m','1h','2h','4h','6h','12h','1d','1w','1M']
class PublicDataError(RuntimeError):
    pass

class BinancePublic:
    base_url = 'https://fapi.binance.com'
    def get(self, path: str, params: dict | None = None):
        """从固定币安公开主机执行不带账户凭据的GET请求。

        Args:
            path: 输入/输出文件路径；具体读写方向见函数用途。
            params: 公开请求查询参数或当前采集参数字典；不含账户凭据。

        Returns:
            解析后的JSON；网络/证书/HTTP异常转换为PublicDataError。
        """
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
        """读取币安USDT合约公开交易规则和精确产品代码。

        Returns:
            交易所元数据字典；不把快照当作实时账户信息。
        """
        return self.get('/fapi/v1/exchangeInfo')
    def klines(self, symbol: str, interval: str, start: int, end: int):
        """读取指定合约、原生周期和时间范围的一页公开K线。

        Args:
            symbol: 精确产品代码；股票、ETF与相关永续不能混用。
            interval: 精确K线周期名称，月线1M区别于分钟。
            start: 查询/分页起点的UTC毫秒时间。
            end: 查询终点的UTC毫秒时间。

        Returns:
            最多1000根原始K线列表，分页由采集服务推进。
        """
        return self.get('/fapi/v1/klines', {'symbol':symbol, 'interval':interval, 'startTime':start, 'endTime':end, 'limit':1000})
