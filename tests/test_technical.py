import json
from pathlib import Path
import tempfile
import unittest
import numpy as np
import pandas as pd
from technical import dow123
from trend_report import aggregate, analyze, load_bars
from lab import download, positions


def candles(values, step=900000):
    v = np.array(values,dtype=float)
    t = 1609459200000+np.arange(len(v))*step
    return pd.DataFrame(dict(open_time=t,close_time=t+step-1,open=v,
                             close=v,high=v+.1,low=v-.1,volume=1.))


class TechnicalTests(unittest.TestCase):
    def test_strategy_and_etf_routing(self):
        b=candles([10,8,12,10,14,11,9,12,10,8])
        signals=dow123(b,1)
        signals['higher_trend']=signals.dow123_signal
        np.testing.assert_array_equal(positions('dow123',None,signals,.6),signals.dow123_signal)
        with tempfile.TemporaryDirectory() as root:
            target=Path(root)/'etf'
            with self.assertRaises(ValueError): download('KORU',30,target)
            self.assertFalse(target.exists())

    def test_three_steps_and_mirror(self):
        b=candles([10,8,12,10,14,11,9,12,10,8])
        t=dow123(b,1)
        self.assertEqual(t.bearish_step.iloc[5],1)
        self.assertEqual(t.bearish_step.iloc[8],2)
        self.assertTrue((t.dow123_event.iloc[:9]==0).all())
        self.assertEqual(t.dow123_event.iloc[9],-1)
        mirror=candles(30-b.close.to_numpy())
        self.assertEqual(dow123(mirror,1).dow123_event.iloc[9],1)

    def test_no_future_leakage(self):
        b=candles(100+np.sin(np.arange(120)/3)*10+np.arange(120)*.02)
        full=dow123(b)
        for n in [10,31,60,119]:
            pd.testing.assert_frame_equal(full.iloc[:n],dow123(b.iloc[:n]))
        changed=b.copy()
        changed.loc[60:,['high','low','close','open']]*=2
        pd.testing.assert_frame_equal(full.iloc[:60],dow123(changed).iloc[:60])

    def test_invalidation(self):
        b=candles([10,8,12,10,14,11,9,15,10,8])
        self.assertTrue((dow123(b,1).dow123_event==0).all())

    def test_crypto_bucket_completeness(self):
        b=candles(np.full(40,100.))
        a=aggregate(b,'4h','binance_usdm')
        self.assertEqual(len(a),2)
        self.assertEqual(a.volume.tolist(),[16.,16.])
        with self.assertRaises(ValueError):
            aggregate(b.drop(index=20),'4h','binance_usdm')

    def test_etf_weekends_and_incomplete_month(self):
        dates=pd.bdate_range('2021-01-04','2021-02-05',tz='UTC')
        b=candles(np.full(len(dates),100.),86400000)
        b.open_time=dates.as_unit('ms').asi8
        b.close_time=b.open_time+86400000-1
        weekly=aggregate(b,'1w','us_etf')
        self.assertEqual(len(weekly),4)
        self.assertEqual(weekly.volume.tolist(),[5.]*4)
        # Jan starts before coverage; Feb has not finished.
        self.assertTrue(aggregate(b,'1mo','us_etf').empty)

    def test_report_and_validation(self):
        with tempfile.TemporaryDirectory() as root:
            p=Path(root)
            b=candles(100+np.sin(np.arange(400)/10))
            b.to_csv(p/'bars.csv',index=False)
            analyze('BTCUSDT',p/'bars.csv',p/'out',['4h'])
            report=json.loads((p/'out/report.json').read_text())
            self.assertEqual(report['timeframes']['4h']['bars'],25)
            self.assertTrue((p/'out/trends.html').exists())
            self.assertFalse(report['real_orders_enabled'])
            with self.assertRaises(FileExistsError):
                analyze('BTCUSDT',p/'bars.csv',p/'out',['4h'])
            b.loc[2,'high']=0
            b.to_csv(p/'bad.csv',index=False)
            with self.assertRaises(ValueError): load_bars(p/'bad.csv')

if __name__=='__main__': unittest.main()
