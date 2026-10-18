import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
import pandas as pd
from futures_backtest import simulate_isolated
from paper_mail import record_simulated
from notifications import deliver_once


def market(opens=(100.,), exits=(100.,), high=None, low=None, funding=None):
    entry=np.array(opens,dtype=float);end=np.array(exits,dtype=float)
    return pd.DataFrame(dict(entry_price=entry,exit_price=end,mark_open=entry,
        exit_mark_open=end,mark_high=np.maximum(entry,end) if high is None else high,
        mark_low=np.minimum(entry,end) if low is None else low,
        funding=np.zeros(len(entry)) if funding is None else funding,
        entry_time=np.arange(len(entry))*900000,exit_time=(np.arange(len(entry))+1)*900000))


class FuturesTests(unittest.TestCase):
    def test_long_short_account_return(self):
        for exit_price,direction in [(105.,1),(95.,-1)]:
            m,*_=simulate_isolated(market(exits=(exit_price,)),[direction],5,.1,0,0)
            self.assertAlmostEqual(m['net_return'],.025)
            self.assertEqual(m['liquidations'],0)

    def test_funding_cost_notional_and_short_credit(self):
        for direction,expected in [(1,-.0005),(-1,.0005)]:
            m,*_=simulate_isolated(market(funding=[.001]),[direction],5,.1,0,0)
            self.assertAlmostEqual(m['net_return'],expected)
        m,*_=simulate_isolated(market(),[1],5,.1,.001,0)
        self.assertAlmostEqual(m['net_return'],-.001)
        self.assertAlmostEqual(m['fees_equity_fraction'],.001)

    def test_same_notional_with_different_leverage_and_margin(self):
        d=market(exits=(105.,))
        a,*_=simulate_isolated(d,[1],5,.2,0,0)
        b,*_=simulate_isolated(d,[1],10,.1,0,0)
        self.assertAlmostEqual(a['net_return'],b['net_return'])
        self.assertAlmostEqual(a['net_return'],.05)

    def test_intrabar_liquidation_despite_recovery(self):
        d=market((100.,100.),(100.,100.),high=[110.,100.],low=[80.,100.])
        m,path,events,_=simulate_isolated(d,[1,1],10,.1,0,0)
        self.assertAlmostEqual(m['net_return'],-.1)
        self.assertEqual(m['liquidations'],1)
        self.assertEqual(m['entries'],1)
        self.assertEqual(events[1]['event'],'LIQUIDATION_INTRABAR')
        self.assertGreater(m['max_drawdown'],.1)
        self.assertTrue(all(v>=0 for v in path))

    def test_short_liquidation_and_reverse_reset(self):
        d=market((100.,100.,100.),(100.,100.,100.),high=[120.,100.,100.],low=[100.,100.,100.])
        m,_,events,_=simulate_isolated(d,[-1,-1,1],10,.1,0,0)
        self.assertEqual(m['liquidations'],1)
        self.assertEqual(m['entries'],2)
        self.assertEqual([e['direction'] for e in events if e['event']=='ENTRY'],[-1,1])

    def test_fixed_units(self):
        m,*_=simulate_isolated(market((100.,105.),(105.,110.)),[1,1],5,.1,0,0)
        self.assertAlmostEqual(m['net_return'],.05)
        self.assertEqual(m['entries'],1)

    def test_open_gap_before_manual_exit(self):
        d=market((100.,70.),(100.,70.))
        m,_,events,_=simulate_isolated(d,[1,0],10,.1,0,0)
        self.assertAlmostEqual(m['net_return'],-.1)
        self.assertEqual(m['liquidations'],1)
        self.assertEqual(events[1]['event'],'LIQUIDATION_OPEN_GAP')

    def test_invalid_parameters_and_marks(self):
        for kwargs in [dict(leverage=0),dict(margin_fraction=2),dict(maintenance_rate=.2),dict(leverage=np.nan)]:
            with self.assertRaises(ValueError):simulate_isolated(market(),[1],**kwargs)
        d=market();d.loc[0,'mark_low']=np.nan
        with self.assertRaises(ValueError):simulate_isolated(d,[1])

    def test_simulated_delivery_no_smtp_and_separate_state(self):
        with tempfile.TemporaryDirectory() as root,patch('notifications.send_email') as send:
            self.assertEqual(record_simulated(root,'one','title','body',{'signal':1}),'SIMULATED_NOT_SENT')
            self.assertEqual(record_simulated(root,'one','title','body',{'signal':1}),'ALREADY_SIMULATED')
            with patch.dict(os.environ,{'QUANT_MAIL_MODE':'simulate'},clear=True):
                self.assertEqual(deliver_once('two','title','body',root),'SIMULATED_NOT_SENT')
            send.assert_not_called()
            with patch.dict(os.environ,{'QUANT_MAIL_MODE':'real'},clear=True):
                self.assertEqual(deliver_once('two','title','body',root),'SMTP_ACCEPTED')
            send.assert_called_once()
            self.assertTrue(list(Path(root).glob('*.eml')))

if __name__=='__main__': unittest.main()
