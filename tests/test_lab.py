import unittest
import tempfile
from pathlib import Path
import numpy as np
import pandas as pd
from lab import simulate, dataset, demo, positions, models
from features import make_features, FEATURES

class Tests(unittest.TestCase):
    def test_flat(self):
        """验证空仓期间净值与交易成本保持正确。

        Returns:
            无返回值；任一断言失败会使测试失败。
        """
        d=pd.DataFrame(dict(forward=[.1,-.1],funding=[.01,.01]))
        m,_=simulate(d,[0,0]);self.assertEqual(m['net_return'],0)
    def test_cost_entry_exit(self):
        """验证入场和平仓均计入费用。

        Returns:
            无返回值；任一断言失败会使测试失败。
        """
        d=pd.DataFrame(dict(forward=[0.,0.],funding=[0.,0.]))
        m,_=simulate(d,[1,1],fee=.001,slip=0,exposure=1)
        self.assertAlmostEqual(m['net_return'],-.002);self.assertEqual(m['order_sides'],2)
    def test_short_funding(self):
        """验证做空方向的资金费收付符号。

        Returns:
            无返回值；任一断言失败会使测试失败。
        """
        d=pd.DataFrame(dict(forward=[0.],funding=[.01]))
        m,_=simulate(d,[-1],0,0,1);self.assertAlmostEqual(m['net_return'],.01)
    def test_fixed_units(self):
        """验证持仓期间数量固定，不因权益变化逐根重新加仓。

        Returns:
            无返回值；任一断言失败会使测试失败。
        """
        d=pd.DataFrame(dict(forward=[.1,.1],funding=[0.,0.]))
        m,_=simulate(d,[1,1],0,0,.2);self.assertAlmostEqual(m['net_return'],.042)
    def test_flip_sides(self):
        """验证方向翻转产生正确的平仓与开仓边。

        Returns:
            无返回值；任一断言失败会使测试失败。
        """
        d=pd.DataFrame(dict(forward=[0.,0.],funding=[0.,0.]))
        m,_=simulate(d,[1,-1],0,0,1);self.assertEqual(m['order_sides'],4)
    def test_drawdown_includes_initial_loss(self):
        """验证最大回撤包含初始权益到首笔亏损的下降。

        Returns:
            无返回值；任一断言失败会使测试失败。
        """
        d=pd.DataFrame(dict(forward=[-.1],funding=[0.]))
        m,_=simulate(d,[1],0,0,1);self.assertAlmostEqual(m['max_drawdown'],.1)
    def test_causality_target_and_funding(self):
        """验证标签、成交时刻和资金费按可知时间对齐。

        Returns:
            无返回值；任一断言失败会使测试失败。
        """
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
        """验证旧版回放参数范围校验。

        Returns:
            无返回值；任一断言失败会使测试失败。
        """
        with self.assertRaises(ValueError): simulate(pd.DataFrame(dict(forward=[0],funding=[0])),[1],exposure=2)
    def test_model_fit(self):
        """验证支持的传统估计器可在测试样本上完成拟合。

        Returns:
            无返回值；任一断言失败会使测试失败。
        """
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'data';demo(p,400);d=dataset(pd.read_csv(p/'bars.csv'),pd.read_csv(p/'funding.csv'))
            for name,m in models().items():
                m.fit(d[FEATURES],d.target)
                self.assertTrue(np.isin(positions(name,m,d,.6),[-1,0,1]).all())

if __name__=='__main__': unittest.main()
