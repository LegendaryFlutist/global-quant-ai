"""行情导入与精确合约核验；与队列生命周期分离，不支持交易接口。"""
import csv
import json
import time
from sqlalchemy import select, func
from app.core.config import RESEARCH_ROOT
from app.domains.models import Asset, Provider, MarketData, utcnow
from app.adapters.public_binance import BinancePublic, PublicDataError
from app.services.market_data import save_dataset, read_dataset

def import_local(db,asset_id: str):
    """从受控真实行情清单导入本机15分钟快照并保存来源。

    Args:
        db: 本次调用的SQLAlchemy数据库会话。
        asset_id: 带市场命名空间的精确产品ID。

    Returns:
        持久数据集摘要；路径越界或没有来源时拒绝。
    """
    asset=db.get(Asset,asset_id)
    manifest=json.loads((RESEARCH_ROOT/'data/priority_analysis_latest.json').read_text())
    relative=manifest['directories'].get(asset.symbol)
    if not relative: raise ValueError('该资产没有可导入的已验证本地行情')
    directory=(RESEARCH_ROOT/relative).resolve()
    if not directory.is_relative_to((RESEARCH_ROOT/'data').resolve()): raise ValueError('非法数据路径')
    with (directory/'bars.csv').open() as f: rows=list(csv.DictReader(f))
    provenance=json.loads((directory/'source.json').read_text())
    result=save_dataset(db,asset_id,'15m',rows,{'provider':'binance_connected_public','mode':'historical_snapshot','source_file':str(directory/'bars.csv'),'provenance':provenance})
    provider=db.get(Provider,'research_snapshot');provider.status='CONNECTED';provider.detail={'last_success_at':utcnow().isoformat(),'mode':'historical_snapshot','last_asset':asset.symbol};db.commit()
    return result

def verify_assets(db):
    """用公开exchangeInfo核验精确合约代码及交易状态。

    Args:
        db: 本次调用的SQLAlchemy数据库会话。

    Returns:
        已核验数量；失败保留快照并明确记录供应商不可用。
    """
    provider=db.get(Provider,'binance_public')
    try:
        info=BinancePublic().exchange_info()
    except PublicDataError as exc:
        provider.status='UNAVAILABLE';provider.detail={**provider.detail,'last_attempt_at':utcnow().isoformat(),'error':str(exc)};db.commit()
        snapshot_count=db.scalar(select(func.count()).select_from(Asset).where(
            Asset.venue=='BINANCE_USDM', Asset.verification=='VERIFIED_SNAPSHOT')) or 0
        if snapshot_count:
            return {'verified_assets':snapshot_count,'verification_mode':'VERIFIED_LOCAL_SNAPSHOT',
                    'live_refresh':False,'warning':str(exc)}
        raise
    indexed={s['symbol']:s for s in info['symbols']}
    verified=0
    for asset in db.scalars(select(Asset).where(Asset.venue=='BINANCE_USDM')):
        symbol=indexed.get(asset.symbol)
        asset.verification='VERIFIED' if symbol and symbol.get('status')=='TRADING' else 'UNAVAILABLE'
        asset.specification=symbol or {};asset.verified_at=utcnow();verified+=int(asset.verification=='VERIFIED')
    provider.status='CONNECTED';provider.detail={'last_success_at':utcnow().isoformat(),'server_time':info.get('serverTime'),'verified_assets':verified};db.commit()
    return {'verified_assets':verified}

def fetch_history(db,params):
    """分页采集最近最多90天真实原生K线并逐页持久化。

    Args:
        db: 本次调用的SQLAlchemy数据库会话。
        params: 公开请求查询参数或当前采集参数字典；不含账户凭据。

    Returns:
        资产、周期、行数与分页数；重试从已保存尾部继续，不伪造数据。
    """
    asset=db.get(Asset,params['asset_id'])
    if not asset or asset.venue!='BINANCE_USDM' or asset.verification not in ('VERIFIED','VERIFIED_SNAPSHOT'):
        raise ValueError('资产未经 Binance 精确代码核验，禁止请求推定合约')
    interval=params['interval'];now=int(time.time()*1000);cursor=now-params.get('days',90)*86400000
    existing=db.scalar(select(MarketData).where(MarketData.asset_id==asset.id,MarketData.interval==interval))
    rows=read_dataset(existing) if existing else []
    if rows:cursor=max(cursor,rows[-1]['open_time'])
    adapter=BinancePublic()
    # Each page is checkpointed. A restarted/retried job resumes from the last persisted candle.
    pages=0
    while cursor<now:
        batch=adapter.klines(asset.symbol,interval,cursor,now)
        if not batch:break
        parsed=[dict(zip(('open_time','open','high','low','close','volume','close_time'),r[:7])) for r in batch]
        closed=[r for r in parsed if int(r['close_time'])<now]
        merged={r['open_time']:r for r in rows}
        merged.update({int(r['open_time']):r for r in closed});rows=list(merged.values())
        if closed:save_dataset(db,asset.id,interval,rows,{'provider':'binance_public_rest','mode':'public_history','retrieved_at':utcnow().isoformat()},now)
        following=int(batch[-1][6])+1
        if following<=cursor:raise ValueError('分页游标未前进，已停止')
        cursor=following;pages+=1
        if len(batch)<1000:break
        if pages>140:raise ValueError('分页达到上限，保留已下载进度')
        time.sleep(.25)
    if not rows:raise ValueError('公开接口没有返回已收盘K线，未生成数据')
    return {'asset_id':asset.id,'interval':interval,'rows':len(rows),'pages':pages}
