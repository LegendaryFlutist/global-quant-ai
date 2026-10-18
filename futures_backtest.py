"""Isolated USDT linear futures approximation with mark-price liquidation.

Fixed maintenance rate, no tier deduction, no auto margin addition, no partial
liquidation or ADL. Funding is charged before the bar's risk check (conservative
ordering when intrabar timestamps cannot be reconstructed). Trade slippage and
fees use notional, not allocated margin. Account starts with normalized equity 1.
"""
import math
import numpy as np


def simulate_isolated(data, signals, leverage=5., margin_fraction=.1, fee=.0005,
                      slip=.0002, maintenance_rate=.01, liquidation_fee=.01):
    values=[leverage,margin_fraction,fee,slip,maintenance_rate,liquidation_fee]
    if not all(math.isfinite(float(x)) for x in values):
        raise ValueError('Nonfinite futures parameters')
    if not 1<=leverage<=125 or not 0<margin_fraction<=1 or not 0<=maintenance_rate<1/leverage:
        raise ValueError('Invalid leverage, margin fraction or maintenance rate')
    if min(fee,slip,liquidation_fee)<0 or slip>=1 or leverage*fee>=1:
        raise ValueError('Invalid futures costs')
    signals=np.asarray(signals)
    if len(data)==0 or len(signals)!=len(data) or not np.isin(signals,[-1,0,1]).all():
        raise ValueError('Invalid signals')
    required=['entry_price','exit_price','mark_open','mark_high','mark_low','exit_mark_open','funding','entry_time','exit_time']
    if not np.isfinite(data[required].to_numpy(dtype=float)).all():
        raise ValueError('Nonfinite futures market data')
    prices=data[['entry_price','exit_price','mark_open','mark_high','mark_low','exit_mark_open']]
    if (prices<=0).any().any() or (data.mark_high<data.mark_low).any() or (data.mark_open>data.mark_high).any() or (data.mark_open<data.mark_low).any():
        raise ValueError('Invalid mark prices')
    free,margin,qty,entry=1.,0.,0.,0.
    last_requested,blocked=0,0
    entries,sides,liquidations=0,0,0
    paid_fees,paid_funding=0.,0.
    peak,drawdown=1.,0.
    path,events,step_returns,stress_points=[],[],[],[]

    def nav(price):
        return free+margin+qty*(price-entry) if qty else free

    def observe(value):
        nonlocal peak,drawdown
        stress_points.append(float(max(0.,value)))
        peak=max(peak,value)
        if peak>0: drawdown=max(drawdown,1-max(0.,value)/peak)

    def close(price, timestamp, reason):
        nonlocal free,margin,qty,entry,sides,paid_fees
        direction=int(np.sign(qty))
        fill=price*(1-direction*slip)
        charge=min(abs(qty)*fill*fee,max(0.,margin+qty*(fill-entry)))
        credit=max(0.,margin+qty*(fill-entry)-charge)
        free+=credit
        paid_fees+=charge;sides+=1
        events.append(dict(time=int(timestamp),event=reason,direction=direction,price=float(fill),
                           quantity=float(abs(qty)),fee=float(charge),collateral_returned=float(credit)))
        margin,qty,entry=0.,0.,0.

    def liquidate(mark_price, timestamp, reason):
        nonlocal free,margin,qty,entry,liquidations,blocked,paid_fees,sides
        direction=int(np.sign(qty))
        charge=min(abs(qty)*mark_price*(liquidation_fee+slip),max(0.,margin+qty*(mark_price-entry)))
        credit=max(0.,margin+qty*(mark_price-entry)-charge)
        free+=credit
        paid_fees+=charge;liquidations+=1;sides+=1;blocked=direction
        events.append(dict(time=int(timestamp),event=reason,direction=direction,price=float(mark_price),
                           quantity=float(abs(qty)),fee=float(charge),collateral_returned=float(credit),
                           timestamp_precision='15m_bar_boundary_approximation'))
        margin,qty,entry=0.,0.,0.

    def risk_check(mark_price, timestamp, reason):
        if qty and margin+qty*(mark_price-entry)<=abs(qty)*mark_price*maintenance_rate:
            amount=abs(qty)
            threshold=((amount*entry-margin)/(amount*(1-maintenance_rate)) if qty>0
                       else (amount*entry+margin)/(amount*(1+maintenance_rate)))
            # Gaps liquidate at the adverse open, not a fictitious threshold fill.
            fill=min(mark_price,threshold) if qty>0 else max(mark_price,threshold)
            if reason=='LIQUIDATION_INTRABAR': fill=max(0.,threshold)
            liquidate(fill,timestamp,reason)
            return True
        return False

    for row,requested in zip(data.itertuples(),signals):
        requested=int(requested)
        previous=path[-1] if path else 1.
        if requested!=last_requested: blocked=0
        # Existing positions face an opening mark gap before voluntary rebalance.
        risk_check(row.mark_open,row.entry_time,'LIQUIDATION_OPEN_GAP')
        observe(nav(row.mark_open))
        current=int(np.sign(qty))
        if current and current!=requested:
            close(row.entry_price,row.entry_time,'EXIT' if requested==0 else 'REVERSE_EXIT')
        if requested and not qty and requested!=blocked and free>1e-12:
            equity=free
            allocated=equity*margin_fraction
            entry=row.entry_price*(1+requested*slip)
            qty=requested*allocated*leverage/entry
            charge=abs(qty)*entry*fee
            free-=allocated;margin=allocated-charge
            paid_fees+=charge;entries+=1;sides+=1
            events.append(dict(time=int(row.entry_time),event='ENTRY',direction=requested,
                price=float(entry),quantity=float(abs(qty)),allocated_margin=float(allocated),
                initial_notional=float(abs(qty)*entry),fee=float(charge)))
        if qty:
            funding_cash=qty*row.entry_price*row.funding
            margin-=funding_cash;paid_funding+=funding_cash
            # Fixed high-then-low stress ordering: an upper bound approximation
            # for within-bar drawdown; actual high/low ordering is unavailable.
            if not risk_check(row.mark_open,row.entry_time,'LIQUIDATION_AFTER_FUNDING'):
                favorable=row.mark_high if qty>0 else row.mark_low
                adverse=row.mark_low if qty>0 else row.mark_high
                observe(nav(favorable))
                if not risk_check(adverse,row.exit_time,'LIQUIDATION_INTRABAR'):
                    observe(nav(adverse))
                    # Next open can gap from the just-completed mark bar.
                    risk_check(row.exit_mark_open,row.exit_time,'LIQUIDATION_EXIT_GAP')
        value=max(0.,nav(row.exit_mark_open))
        observe(value)
        path.append(value)
        step_returns.append(value/previous-1 if previous>0 else 0.)
        last_requested=requested
    if qty:
        close(float(data.exit_price.iloc[-1]),int(data.exit_time.iloc[-1]),'FINAL_EXIT')
        final=max(0.,free)
        step_returns[-1]=(1+step_returns[-1])*final/path[-1]-1 if path[-1]>0 else 0.
        path[-1]=final;observe(final)
    sd=np.std(step_returns,ddof=1) if len(step_returns)>1 else 0.
    metrics=dict(net_return=float(path[-1]-1),max_drawdown=float(drawdown),entries=entries,
        order_sides=sides,liquidations=liquidations,fees_equity_fraction=float(paid_fees),
        funding_equity_fraction=float(paid_funding),bars=len(data),
        sharpe_estimate=float(np.mean(step_returns)/sd*np.sqrt(365*96)) if sd>1e-12 else 0.,
        leverage=float(leverage),margin_fraction=float(margin_fraction),initial_nominal_exposure=float(leverage*margin_fraction))
    return metrics,path,events,stress_points
