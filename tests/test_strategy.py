import unittest
import numpy as np
import pandas as pd
from strategy import hourly_context, follow_hourly, signal_features
from lab import positions


def bars(n=500):
    t=1609459200000+np.arange(n)*900000
    price=100+np.arange(n)*.02
    return pd.DataFrame(dict(open_time=t,close_time=t+899999,
        open=price,high=price+.1,low=price-.1,close=price,volume=10.))


class StrategyTests(unittest.TestCase):
    def test_hour_not_available_before_close(self):
        b=bars();c=hourly_context(b)
        self.assertTrue(c.higher_close_time.iloc[:3].isna().all())
        self.assertEqual(c.higher_close_time.iloc[3],b.close_time.iloc[3])
        self.assertEqual(c.higher_close_time.iloc[6],b.close_time.iloc[3])
        self.assertEqual(c.higher_close_time.iloc[7],b.close_time.iloc[7])
        self.assertTrue((c.higher_trend.iloc[:191]==0).all())
        self.assertEqual(c.higher_trend.iloc[191],1)
        self.assertTrue((c.higher_close_time.dropna().to_numpy()<=b.close_time[c.higher_close_time.notna()].to_numpy()).all())

    def test_partial_hour_cannot_change_earlier_direction(self):
        b=bars();base=hourly_context(b)
        changed=b.copy();changed.loc[498:,'close']=1
        actual=hourly_context(changed)
        pd.testing.assert_frame_equal(base.iloc[:499],actual.iloc[:499])
        for n in (191,192,193,499):
            pd.testing.assert_frame_equal(base.iloc[:n],hourly_context(b.iloc[:n]))

    def test_trend_filter_and_no_direction(self):
        d=pd.DataFrame({'higher_trend':[1,1,-1,-1,0], 'dow123_signal':[1,-1,1,-1,1]})
        np.testing.assert_array_equal(follow_hourly(d.dow123_signal,d),[1,0,0,-1,0])
        np.testing.assert_array_equal(positions('dow123',None,d,.6),[1,0,0,-1,0])
        with self.assertRaises(ValueError): follow_hourly([1],pd.DataFrame({'close':[100]}))

    def test_partial_first_hour_and_gaps(self):
        b=bars().iloc[1:].reset_index(drop=True)
        c=hourly_context(b)
        self.assertTrue(c.higher_close_time.iloc[:6].isna().all())
        self.assertEqual(c.higher_close_time.iloc[6],b.close_time.iloc[6])
        with self.assertRaises(ValueError): hourly_context(b.drop(index=50))

    def test_forward_features_prefix_invariant(self):
        b=bars();a=signal_features(b)
        pd.testing.assert_frame_equal(a.iloc[:399],signal_features(b.iloc[:399]))

if __name__=='__main__': unittest.main()
