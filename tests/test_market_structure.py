import json
from pathlib import Path
import tempfile
import unittest
import numpy as np
import pandas as pd
from market_structure import structure_features,wave_candidate,impulse,analysis_signals,multitimeframe_structure
from structure_report import spectral_cycle,analyze_market,support_resistance


def candles(n=640,step_ms=900000):
    """构造当前技术分析或执行测试所需的合成OHLCV样本。

    Args:
        n: 合成样本或展示窗口的数量。
        step_ms: 每根K线的周期长度，单位毫秒。

    Returns:
        测试夹具、构造结果或断言辅助返回值；不影响真实数据。
    """
    i=np.arange(n);c=100+.01*i+3*np.sin(i/8);o=np.r_[c[0],c[:-1]]
    t=1609459200000+i*step_ms
    return pd.DataFrame(dict(open_time=t,close_time=t+step_ms-1,open=o,high=np.maximum(o,c)+.2+.03*np.sin(i/3),low=np.minimum(o,c)-.2-.02*np.cos(i/5),close=c,volume=10+i%17))


class MarketStructureTests(unittest.TestCase):
    def test_structure_and_pivots_are_prefix_causal(self):
        """验证结构特征与确认转折不会被未来样本回写。

        Returns:
            无返回值；任一断言失败会使测试失败。
        """
        b=candles();full=structure_features(b)
        for n in (87,188,517):
            pd.testing.assert_frame_equal(full.iloc[:n],structure_features(b.iloc[:n]))
        _,_,pivots,_=structure_features(b,return_details=True)
        self.assertTrue(pivots)
        self.assertTrue(all(p['confirmed_at']>=p['time']+2*900000 for p in pivots))

    def test_multitimeframe_events_are_confirmed_and_prefix_invariant(self):
        """验证跨周期结构事件因果确认，未来数据不会改写过去报告。

        Returns:
            无返回值；未来泄漏、事件缺确认时间或不支持趋势线时测试失败。
        """
        bars=candles(320);higher=candles(80,4*60*60*1000);cutoff=int(bars.close_time.iloc[219])+1
        digest='a'*64
        prefix=multitimeframe_structure({'15m':bars.iloc[:220],'4h':higher.iloc[:14]},cutoff,{'15m':digest,'4h':digest})
        extended=multitimeframe_structure({'15m':bars,'4h':higher},cutoff,{'15m':digest,'4h':digest})
        self.assertEqual(prefix,extended)
        self.assertEqual(prefix['schema_version'],1)
        self.assertEqual(prefix['timeframes']['15m']['bars'],220)
        self.assertIn(prefix['timeframes']['15m']['trend_lines']['support']['state'],('HOLDING','BROKEN','UNAVAILABLE'))
        self.assertTrue(all(event['confirmed_at']<cutoff for event in prefix['events']))
        self.assertTrue(all(event['source_sha256']==digest for event in prefix['events']))

    def test_bullish_price_rsi_divergence_waits_for_right_pivot_close(self):
        """验证价格更低低点与RSI更高低点只在右侧K线收盘后确认。

        Returns:
            无返回值；背离方向或确认时点错误会使测试失败。
        """
        close=np.array([110.,100.,90.,100.,110.,105.,88.,100.,110.])
        time=np.arange(len(close))*900000+1609459200000
        bars=pd.DataFrame(dict(open_time=time,close_time=time+899999,open=close,high=close+1,low=close-1,close=close,volume=np.ones(len(close))))
        result=multitimeframe_structure({'15m':bars},int(bars.close_time.iloc[-1])+1,pivot_width=1,rsi_period=2)
        event=next(item for item in result['events'] if item['kind']=='BULLISH_RSI_DIVERGENCE')
        self.assertEqual(event['occurred_at'],int(bars.close_time.iloc[6]))
        self.assertEqual(event['confirmed_at'],int(bars.close_time.iloc[7]))
        truncated=multitimeframe_structure({'15m':bars.iloc[:7]},int(bars.close_time.iloc[6])+1,pivot_width=1,rsi_period=2)
        self.assertFalse(any(item['kind']=='BULLISH_RSI_DIVERGENCE' for item in truncated['events']))

    def test_impulse_rules_and_ambiguity(self):
        """验证推动浪硬规则及歧义时不强制标浪。

        Returns:
            无返回值；任一断言失败会使测试失败。
        """
        def points(values):
            """将给定价格序列转成测试所需的交替确认转折。

            Args:
                values: 时间戳或数值序列，转换口径见用途。

            Returns:
                测试夹具、构造结果或断言辅助返回值；不影响真实数据。
            """
            return [dict(price=p,kind='low' if i%2==0 else 'high',index=i,confirmed_at=i+2) for i,p in enumerate(values)]
        p=points([100,110,105,125,115,135])
        self.assertEqual(impulse(p),1)
        self.assertEqual(wave_candidate(p)['status'],'FIVE_WAVE_CANDIDATE')
        inverse=[dict(x,price=250-x['price'],kind='high' if x['kind']=='low' else 'low') for x in p]
        self.assertEqual(impulse(inverse),-1)
        for values in ([100,110,99,125,115,135],[100,110,105,125,109,135],[100,120,110,125,121,145]):self.assertIsNone(impulse(points(values)))
        self.assertEqual(wave_candidate([])['status'],'AMBIGUOUS')
        self.assertEqual(wave_candidate(p+points([120,128,112]))['status'],'FIVE_PLUS_ABC_CANDIDATE')

    def test_dow_rule_is_independent_of_candlestick_vote_and_filters_context(self):
        """验证道氏入场独立于形态投票并遵守大周期过滤。

        Returns:
            无返回值；任一断言失败会使测试失败。
        """
        f=pd.DataFrame(dict(analysis_dow_event=[1,-1,1,1],higher_trend=[1,-1,-1,1],analysis_trend=[0,0,0,-1],swing_range_pct=[.1]*4))
        signal=analysis_signals(f,swing_min_range=.02,swing_max_range=.5)
        self.assertEqual(signal.direction.tolist(),[1,-1,0,0])
        self.assertEqual(signal.pattern.iloc[0],'DOW123_CONFIRMED')

    def test_cycle_is_historical_description_and_levels_are_on_correct_sides(self):
        """验证周期仅作历史描述且支撑压力分列参考价两侧。

        Returns:
            无返回值；任一断言失败会使测试失败。
        """
        b=candles(128);b['close']=100*np.exp(.02*np.sin(2*np.pi*np.arange(128)/16))
        cycle=spectral_cycle(b,'1h')
        self.assertEqual(cycle['status'],'EXPLORATORY');self.assertAlmostEqual(cycle['period_bars'],16)
        raw=structure_features(candles(),return_details=True)[2]
        levels=support_resistance(candles(),raw);close=candles().close.iloc[-1]
        self.assertTrue(all(p['center']<close for p in levels['support']))
        self.assertTrue(all(p['center']>close for p in levels['resistance']))

    def test_multi_report_has_candles_zones_scenarios_and_insufficient_monthly_data(self):
        """验证多周期报告含真实蜡烛/区域/场景并标记月线样本不足。

        Returns:
            无返回值；任一断言失败会使测试失败。
        """
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);source=root/'data';source.mkdir();b=candles()
            b.to_csv(source/'bars.csv',index=False);(source/'source.json').write_text(json.dumps(dict(symbol='KORUUSDT',synthetic=False)))
            report=analyze_market('KORUUSDT',source,root/'analysis')
            self.assertEqual(len(report['timeframes']),8)
            self.assertEqual(report['timeframes']['1mo']['status'],'INSUFFICIENT_DATA')
            self.assertEqual(report['structural_events']['schema_version'],1)
            self.assertEqual(report['structural_events']['timeframes']['15m']['source_sha256'],report['dataset_sha256'])
            text=(root/'analysis/index.html').read_text()
            for word in ['支撑','压力','条件情景','波浪','背离','3倍','候选','多周期结构事件','CSV','<rect','clip_15m']:self.assertIn(word,text)
            self.assertTrue((root/'analysis/4h.svg').is_file())
            json.loads((root/'analysis/report.json').read_text())

if __name__=='__main__':unittest.main()
