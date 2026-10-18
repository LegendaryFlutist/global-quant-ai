import json
from pathlib import Path
import tempfile
import unittest
import numpy as np
import pandas as pd
from market_structure import structure_features,wave_candidate,impulse,analysis_signals
from structure_report import spectral_cycle,analyze_market,support_resistance


def candles(n=640):
    i=np.arange(n);c=100+.01*i+3*np.sin(i/8);o=np.r_[c[0],c[:-1]]
    t=1609459200000+i*900000
    return pd.DataFrame(dict(open_time=t,close_time=t+899999,open=o,high=np.maximum(o,c)+.2+.03*np.sin(i/3),low=np.minimum(o,c)-.2-.02*np.cos(i/5),close=c,volume=10+i%17))


class MarketStructureTests(unittest.TestCase):
    def test_structure_and_pivots_are_prefix_causal(self):
        b=candles();full=structure_features(b)
        for n in (87,188,517):
            pd.testing.assert_frame_equal(full.iloc[:n],structure_features(b.iloc[:n]))
        _,_,pivots,_=structure_features(b,return_details=True)
        self.assertTrue(pivots)
        self.assertTrue(all(p['confirmed_at']>=p['time']+2*900000 for p in pivots))

    def test_impulse_rules_and_ambiguity(self):
        def points(values):return [dict(price=p,kind='low' if i%2==0 else 'high',index=i,confirmed_at=i+2) for i,p in enumerate(values)]
        p=points([100,110,105,125,115,135])
        self.assertEqual(impulse(p),1)
        self.assertEqual(wave_candidate(p)['status'],'FIVE_WAVE_CANDIDATE')
        inverse=[dict(x,price=250-x['price'],kind='high' if x['kind']=='low' else 'low') for x in p]
        self.assertEqual(impulse(inverse),-1)
        for values in ([100,110,99,125,115,135],[100,110,105,125,109,135],[100,120,110,125,121,145]):self.assertIsNone(impulse(points(values)))
        self.assertEqual(wave_candidate([])['status'],'AMBIGUOUS')
        self.assertEqual(wave_candidate(p+points([120,128,112]))['status'],'FIVE_PLUS_ABC_CANDIDATE')

    def test_dow_rule_is_independent_of_candlestick_vote_and_filters_context(self):
        f=pd.DataFrame(dict(analysis_dow_event=[1,-1,1,1],higher_trend=[1,-1,-1,1],analysis_trend=[0,0,0,-1],swing_range_pct=[.1]*4))
        signal=analysis_signals(f,swing_min_range=.02,swing_max_range=.5)
        self.assertEqual(signal.direction.tolist(),[1,-1,0,0])
        self.assertEqual(signal.pattern.iloc[0],'DOW123_CONFIRMED')

    def test_cycle_is_historical_description_and_levels_are_on_correct_sides(self):
        b=candles(128);b['close']=100*np.exp(.02*np.sin(2*np.pi*np.arange(128)/16))
        cycle=spectral_cycle(b,'1h')
        self.assertEqual(cycle['status'],'EXPLORATORY');self.assertAlmostEqual(cycle['period_bars'],16)
        raw=structure_features(candles(),return_details=True)[2]
        levels=support_resistance(candles(),raw);close=candles().close.iloc[-1]
        self.assertTrue(all(p['center']<close for p in levels['support']))
        self.assertTrue(all(p['center']>close for p in levels['resistance']))

    def test_multi_report_has_candles_zones_scenarios_and_insufficient_monthly_data(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);source=root/'data';source.mkdir();b=candles()
            b.to_csv(source/'bars.csv',index=False);(source/'source.json').write_text(json.dumps(dict(symbol='KORUUSDT',synthetic=False)))
            report=analyze_market('KORUUSDT',source,root/'analysis')
            self.assertEqual(len(report['timeframes']),8)
            self.assertEqual(report['timeframes']['1mo']['status'],'INSUFFICIENT_DATA')
            text=(root/'analysis/index.html').read_text()
            for word in ['支撑','压力','条件情景','波浪','3倍','候选','CSV','<rect','clip_15m']:self.assertIn(word,text)
            self.assertTrue((root/'analysis/4h.svg').is_file())
            json.loads((root/'analysis/report.json').read_text())

if __name__=='__main__':unittest.main()
