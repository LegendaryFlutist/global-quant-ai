import unittest
import tempfile
from pathlib import Path
import numpy as np
import pandas as pd
from lab import simulate, dataset, demo, positions, models
from features import make_features, FEATURES

class Tests(unittest.TestCase):
    def test_flat(self):
        d=pd.DataFrame(dict(forward=[.1,-.1],funding=[.01,.01]))
        m,_=simulate(d,[0,0]);self.assertEqual(m['net_return'],0)
    def test_cost_entry_exit(self):
        d=pd.DataFrame(dict(forward=[0.,0.],funding=[0.,0.]))
        m,_=simulate(d,[1,1],fee=.001,slip=0,exposure=1)
        self.assertAlmostEqual(m['net_return'],-.002);self.assertEqual(m['order_sides'],2)
    def test_short_funding(self):
        d=pd.DataFrame(dict(forward=[0.],funding=[.01]))
        m,_=simulate(d,[-1],0,0,1);self.assertAlmostEqual(m['net_return'],.01)
    def test_fixed_units(self):
        d=pd.DataFrame(dict(forward=[.1,.1],funding=[0.,0.]))
        m,_=simulate(d,[1,1],0,0,.2);self.assertAlmostEqual(m['net_return'],.042)
    def test_flip_sides(self):
        d=pd.DataFrame(dict(forward=[0.,0.],funding=[0.,0.]))
        m,_=simulate(d,[1,-1],0,0,1);self.assertEqual(m['order_sides'],4)
    def test_drawdown_includes_initial_loss(self):
        d=pd.DataFrame(dict(forward=[-.1],funding=[0.]))
        m,_=simulate(d,[1],0,0,1);self.assertAlmostEqual(m['max_drawdown'],.1)
    def test_causality_target_and_funding(self):
        with tempfile.TemporaryDirectory() as t:
            path=Path(t)/'data';demo(path,300)
            b=pd.read_csv(path/'bars.csv');f=pd.read_csv(path/'funding.csv')
            d=dataset(b,f);row=d.iloc[0];i=int(np.where(b.open_time==row.open_time)[0][0])
            self.assertAlmostEqual(row.forward,b.open.iloc[i+2]/b.open.iloc[i+1]-1)
            # Event at open 64 belongs to signal at close 62.
            r=d[d.open_time==b.open_time.iloc[62]].iloc[0]
            self.assertAlmostEqual(r.funding,.0001*b.open.iloc[64]/b.open.iloc[63])
            changed=b.copy();changed.loc[200:,'close']*=2
            a=make_features(b);c=make_features(changed)
            np.testing.assert_allclose(a[FEATURES].iloc[:200],c[FEATURES].iloc[:200],equal_nan=True)
            with self.assertRaises(ValueError): dataset(b.drop(index=100),f)
    def test_bounds(self):
        with self.assertRaises(ValueError): simulate(pd.DataFrame(dict(forward=[0],funding=[0])),[1],exposure=2)
    def test_model_fit(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'data';demo(p,400);d=dataset(pd.read_csv(p/'bars.csv'),pd.read_csv(p/'funding.csv'))
            for name,m in models().items():
                m.fit(d[FEATURES],d.target)
                self.assertTrue(np.isin(positions(name,m,d,.6),[-1,0,1]).all())

if __name__=='__main__': unittest.main()
