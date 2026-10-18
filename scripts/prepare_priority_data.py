"""Validate connector snapshots and declare evaluation boundaries before fitting."""
import hashlib
import json
from pathlib import Path
import sys
from datetime import datetime,timezone
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
from features import parse_klines
from lab import dataset
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
NAMES={'KORUUSDT':'韩国MSCI指数每日3倍做多ETF永续','CLUSDT':'原油永续','NVDAUSDT':'英伟达股票永续'}

def prepare(root=ROOT):
    raw=root/'data/priority_raw_20261018'
    info=json.loads((root/'data/priority_contracts_20261018.json').read_text())
    symbols={x['symbol']:x for x in info['symbols']}
    baseline=json.loads((root/'runs/btcusdt_1h_15m_20261017/report.json').read_text())
    cut=int(baseline['holdout_period_ms'][0])
    for symbol,name in NAMES.items():
        out=root/'data'/(symbol.lower()+'_90d_connector')
        if out.exists():raise ValueError('Preserve existing source directory: '+str(out))
        tables={}
        for kind in ('bars','marks','funding'):
            rows=[]
            for path in sorted(raw.glob(symbol+'_'+kind+'_*.json')):rows.extend(json.loads(path.read_text()))
            tables[kind]=pd.DataFrame(rows) if kind=='funding' else parse_klines(rows)
        bars,marks,funding=tables['bars'],tables['marks'],tables['funding']
        for column in ('fundingTime','fundingRate','markPrice'):funding[column]=pd.to_numeric(funding[column],errors='raise')
        dataset(bars,funding)
        if not np.array_equal(bars.open_time,marks.open_time) or not np.array_equal(bars.close_time,marks.close_time):raise ValueError('Mark candle mismatch')
        if len(bars)!=8640:raise ValueError('Snapshot must have complete 90d coverage')
        out.mkdir(parents=True)
        columns=['open_time','close_time','open','high','low','close','volume']
        bars[columns].to_csv(out/'bars.csv',index=False);marks[columns].to_csv(out/'mark_bars.csv',index=False)
        funding.to_csv(out/'funding.csv',index=False)
        digest=hashlib.sha256((out/'bars.csv').read_bytes()+(out/'funding.csv').read_bytes()).hexdigest()
        source=dict(symbol=symbol,name=name,start=int(bars.open_time.iloc[0]),end=int(bars.close_time.iloc[-1]),synthetic=False,
            provider='Binance USD-M public market-data connector',contract=symbols[symbol],
            raw_snapshot='data/priority_raw_20261018',retrieved_at=datetime.now(timezone.utc).isoformat(),
            funding_events=len(funding),bars=len(bars),history_days=90,
            note='TradFi perpetual contract prices, not substituted equity/commodity spot prices; no API account keys used')
        (out/'source.json').write_text(json.dumps(source,ensure_ascii=False,indent=2))
        plan=dict(dataset_sha256=digest,holdout_period_ms=[cut,int(bars.open_time.iloc[-1])],
            declared_before_model_training=True,source='fixed original BTC validation/holdout calendar',
            purpose='Evaluation boundary only; not an earlier model result',synthetic=False)
        (out/'evaluation_plan.json').write_text(json.dumps(plan,indent=2))
        print(symbol,len(bars),'bars',len(funding),'funding events',out)

if __name__=='__main__':prepare()
