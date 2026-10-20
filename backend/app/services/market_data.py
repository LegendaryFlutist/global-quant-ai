import csv
import uuid
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
import pyarrow as pa
from sqlalchemy import select, text
from app.core.config import DATA_ROOT
from app.domains.models import MarketData, utcnow
from app.adapters.public_binance import INTERVALS
from app.storage.duckdb_query import query_parquet_rows
from app.storage.parquet_files import sha256_file, write_parquet_immutable

MINUTES = {'1m':1,'3m':3,'5m':5,'15m':15,'30m':30,'1h':60,'2h':120,'4h':240,'6h':360,'12h':720,'1d':1440,'1w':10080}
FIELDS = ('open', 'high', 'low', 'close', 'volume')
DECIMAL = pa.decimal128(34, 12)
SCHEMA = pa.schema([('open_time',pa.int64()), *[(x, DECIMAL) for x in FIELDS], ('close_time',pa.int64())])

def normalize(rows: list[dict], now_ms: int, interval: str = '15m', *, max_age_days: int = 90, provider_native: bool = False) -> list[dict]:
    """校验OHLC、Decimal数值和时间边界，去重并去掉未来/过期K线。

    Args:
        rows: 原始K线/记录列表；字段契约由当前领域函数校验。
        now_ms: 用于判断已收盘/新鲜度的UTC当前毫秒时间戳。
        interval: 精确K线周期名称，月线1M区别于分钟。
        max_age_days: 保留窗口天数；公开历史采集可大于默认90天。
        provider_native: 允许有明确交易所来源的原生小时K线采用当地盘中偏移。

    Returns:
        按时间排序的已收盘真实记录；冲突或非法数值抛出ValueError。
    """
    if interval not in INTERVALS:
        raise ValueError('不支持的周期')
    clean = {}
    for raw in rows:
        try:
            row = {'open_time':int(raw['open_time']), 'close_time':int(raw['close_time'])}
            row.update({k:Decimal(str(raw[k])) for k in FIELDS})
        except (KeyError, ValueError, InvalidOperation):
            raise ValueError('K线字段无效') from None
        if not all(row[k].is_finite() for k in FIELDS) or min(row[k] for k in FIELDS[:4]) <= 0 or row['volume'] < 0:
            raise ValueError('K线包含非法价格或数量')
        if row['high'] < max(row['open'],row['close'],row['low']) or row['low'] > min(row['open'],row['close'],row['high']):
            raise ValueError('OHLC 范围无效')
        if row['close_time'] < row['open_time']:
            raise ValueError('K线结束时间无效')
        if interval in MINUTES:
            step = MINUTES[interval]*60000
            if row['close_time'] != row['open_time']+step-1 or (interval != '1w' and row['open_time'] % step and not provider_native):
                raise ValueError('K线周期与时间戳不匹配')
        # Never import a currently forming candle. Keep at most the last 90 days of supplied data.
        if row['close_time'] >= now_ms:
            continue
        previous = clean.get(row['open_time'])
        if previous and previous != row:
            raise ValueError('同一时间存在冲突K线')
        clean[row['open_time']] = row
    ordered = sorted(clean.values(), key=lambda x:x['open_time'])
    if not ordered:
        raise ValueError('没有已收盘的真实行情')
    if not isinstance(max_age_days, int) or not 1 <= max_age_days <= 3650:
        raise ValueError('行情保留范围必须在1到3650天之间')
    cutoff = max(now_ms, ordered[-1]['close_time']) - max_age_days*86400000
    return [r for r in ordered if r['open_time'] >= cutoff]

def save_dataset(db, asset_id: str, interval: str, rows: list[dict], source: dict, now_ms: int | None = None, *, max_age_days: int = 90):
    """以资产周期事务锁写不可变Parquet并原子更新元数据指针。

    Args:
        db: 本次调用的SQLAlchemy数据库会话。
        asset_id: 带市场命名空间的精确产品ID。
        interval: 精确K线周期名称，月线1M区别于分钟。
        rows: 原始K线/记录列表；字段契约由当前领域函数校验。
        source: 已知来源元数据，或对齐函数中的源K线表。
        now_ms: 用于判断已收盘/新鲜度的UTC当前毫秒时间戳。
        max_age_days: 保留本地行情历史的天数，默认兼容原90日缓存策略。

    Returns:
        行数、SHA256和截止时间；拒绝用旧快照覆盖新数据。
    """
    now_ms = now_ms or int(datetime.now(timezone.utc).timestamp()*1000)
    if db.bind.dialect.name == 'postgresql':
        db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:key))'), {'key':asset_id+interval})
    rows = normalize(rows, now_ms, interval, max_age_days=max_age_days,
                     provider_native=source.get('timestamp_alignment') == 'exchange_session')
    if not rows:
        raise ValueError('行情已超出最近90天，请更新数据')
    metadata = db.scalar(select(MarketData).where(MarketData.asset_id==asset_id, MarketData.interval==interval))
    # Never replace a newer snapshot with an older one.
    if metadata and rows[-1]['close_time'] < metadata.last_close_ms:
        return {'rows':metadata.row_count, 'status':'SKIPPED_OLDER', 'asset_id':asset_id}
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    filename = f'{uuid.uuid4().hex}.parquet'
    target = DATA_ROOT / filename
    _, sha = write_parquet_immutable(pa.Table.from_pylist(rows,schema=SCHEMA),target)
    values = dict(path=filename,row_count=len(rows),first_open_ms=rows[0]['open_time'],last_close_ms=rows[-1]['close_time'],sha256=sha,source=source,updated_at=utcnow())
    if metadata is None:
        db.add(MarketData(asset_id=asset_id,interval=interval,**values))
    else:
        for key,value in values.items():
            setattr(metadata,key,value)
    db.commit()
    # Immutable files: an interrupted transaction can leave an orphan, but cannot corrupt active data.
    return {'rows':len(rows),'asset_id':asset_id,'interval':interval,'sha256':sha,'last_close_ms':rows[-1]['close_time']}

def read_dataset(metadata: MarketData):
    """校验受控文件路径及SHA256后读取原始Decimal行情。

    Args:
        metadata: 指向不可变Parquet和SHA256的行情元数据记录。

    Returns:
        记录列表；文件缺失或损坏立即拒绝读取。
    """
    if metadata.path.endswith('.json'):
        from app.storage.market_data_segments import read_underlying_segments
        return read_underlying_segments(metadata)
    path = DATA_ROOT / metadata.path
    if path.parent != DATA_ROOT or not path.is_file():
        raise ValueError('历史数据文件不存在')
    if sha256_file(path) != metadata.sha256:
        raise ValueError('历史文件校验失败，停止读取')
    return query_parquet_rows([path], order_column='open_time')

def bucket_bounds(ms: int, interval: str):
    """计算UTC固定周期、周一周线或自然月的聚合边界。

    Args:
        ms: UTC毫秒时间戳。
        interval: 精确K线周期名称，月线1M区别于分钟。

    Returns:
        左闭右开的起止毫秒二元组。
    """
    dt = datetime.fromtimestamp(ms/1000, timezone.utc)
    if interval == '1M':
        start = datetime(dt.year,dt.month,1,tzinfo=timezone.utc)
        end = datetime(dt.year+int(dt.month==12),dt.month%12+1,1,tzinfo=timezone.utc)
        return int(start.timestamp()*1000),int(end.timestamp()*1000)
    if interval == '1w':
        start = dt.replace(hour=0,minute=0,second=0,microsecond=0)-timedelta(days=dt.weekday())
        return int(start.timestamp()*1000),int((start+timedelta(days=7)).timestamp()*1000)
    step=MINUTES[interval]*60000
    return (ms//step)*step,(ms//step+1)*step

def aggregate(rows: list[dict], source_interval: str, target: str):
    """聚合严格覆盖完整桶的真实K线，排除缺失和边界未完成周期。

    Args:
        rows: 原始K线/记录列表；字段契约由当前领域函数校验。
        source_interval: 输入数据的原生周期名称。
        target: 报告目标目录或聚合目标周期；具体用途由函数名说明。

    Returns:
        聚合记录与被排除桶数；不允许向小周期插值。
    """
    if target == source_interval:
        return rows, 0
    base = MINUTES.get(source_interval)
    if target not in INTERVALS or base is None or (target in MINUTES and (MINUTES[target]<base or MINUTES[target]%base)):
        raise ValueError(f'现有{source_interval}数据不能还原{target}，需要原生行情导入')
    groups=defaultdict(list)
    for row in rows:
        start,end=bucket_bounds(row['open_time'],target)
        groups[(start,end)].append(row)
    output=[]; excluded=0
    for (start,end), group in sorted(groups.items()):
        expected=(end-start)//(base*60000)
        if len(group)!=expected or group[0]['open_time']!=start or group[-1]['close_time']!=end-1:
            excluded+=1
            continue
        output.append(dict(open_time=start,close_time=end-1,open=group[0]['open'],high=max(r['high'] for r in group),low=min(r['low'] for r in group),close=group[-1]['close'],volume=sum(r['volume'] for r in group)))
    return output,excluded

def candles(db, asset_id: str, interval: str, limit: int = 800, end_ms: int | None = None):
    """选择直接或可合法聚合的数据源并按截止时间与条数输出图表K线。

    Args:
        db: 本次调用的SQLAlchemy数据库会话。
        asset_id: 带市场命名空间的精确产品ID。
        interval: 精确K线周期名称，月线1M区别于分钟。
        limit: 最多返回的已收盘K线条数。
        end_ms: 可选的历史观察截止毫秒时间；不返回结束晚于它的K线。

    Returns:
        价格为十进制字符串的行情响应，包含来源和文件指纹。
    """
    sources=list(db.scalars(select(MarketData).where(MarketData.asset_id==asset_id)))
    direct=next((s for s in sources if s.interval==interval),None)
    chosen=direct or next((s for s in sources if s.interval in MINUTES and (interval=='1M' or (interval in MINUTES and MINUTES[s.interval]<=MINUTES[interval] and MINUTES[interval]%MINUTES[s.interval]==0))),None)
    if chosen is None:
        raise ValueError('该周期暂无原生或可合法聚合的数据；禁止从大周期伪造小周期')
    rows,excluded=aggregate(read_dataset(chosen),chosen.interval,interval)
    if end_ms is not None:
        rows=[r for r in rows if r['close_time']<=end_ms]
    return {'asset_id':asset_id,'interval':interval,'source_interval':chosen.interval,'candles':[{**r,**{k:str(r[k]) for k in FIELDS}} for r in rows[-limit:]],'available_count':len(rows),'excluded_incomplete_buckets':excluded,'source':chosen.source,'as_of_ms':chosen.last_close_ms,'dataset_sha256':chosen.sha256,'research_only':True}
