"""Offline, selectable account performance and explicit simulated operations."""
import html
import json
import os
from pathlib import Path
import tempfile
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd


def shanghai(values):
    """将毫秒UTC时间戳序列转换为上海时间显示文本。

    Args:
        values: 时间戳或数值序列，转换口径见用途。

    Returns:
        与输入对齐的格式化时间序列。
    """
    return pd.to_datetime(values,unit='ms',utc=True).dt.tz_convert('Asia/Shanghai').dt.strftime('%Y-%m-%d %H:%M:%S')


def _duration(hours):
    """把实际经过小时转换为天/小时/分钟/秒，避免夸大K线内精度。

    Args:
        hours: 实际经过的持仓小时数。

    Returns:
        可读持仓时长字符串。

    Notes:
        原有细则保留如下：
        Format elapsed wall time, without inflating an intrabar approximation.
    """
    seconds=max(0,int(round(float(hours)*3600)))
    days,seconds=divmod(seconds,86400)
    hours,seconds=divmod(seconds,3600)
    minutes,seconds=divmod(seconds,60)
    return (f'{days}天 ' if days else '')+f'{hours}小时 {minutes}分 {seconds}秒'


def _holding_summary(group, series, min_hours=4., max_hours=168.):
    """按真实时间戳统计持仓分布、目标区间和提前风险退出原因。

    Args:
        group: 当前独立账户的完整交易记录表。
        series: 当前账户的时间与权益序列。
        min_hours: 报告中目标最短持仓小时数。
        max_hours: 报告中目标最长持仓小时数。

    Returns:
        持仓统计字典；合并重叠区间以免市场暴露时间超过100%。
    """
    hours=group.holding_hours.astype(float) if len(group) else pd.Series(dtype=float)
    tolerance=1/3_600_000 # close_time is the last millisecond of a candle.
    window=max(0.,(series[-1][0]-series[0][0])/3_600_000)
    # A single account has one position at a time. Merge intervals defensively
    # so overlapping legacy records never produce exposure above 100%.
    intervals=sorted((max(int(t.entry_time),series[0][0]),min(int(t.exit_time),series[-1][0]))
                     for t in group.itertuples())
    merged=[]
    for start,end in intervals:
        if end<=start:continue
        if merged and start<=merged[-1][1]:merged[-1][1]=max(merged[-1][1],end)
        else:merged.append([start,end])
    occupied=sum(end-start for start,end in merged)/3_600_000
    early=group[group.holding_hours<min_hours-tolerance] if len(group) else group
    early_reasons={str(reason):int(count) for reason,count in early.exit_reason.value_counts().items()} if len(early) else {}
    reasons={str(reason):int(count) for reason,count in group.exit_reason.value_counts().items()} if len(group) else {}
    exceptions={'stop_loss':0,'liquidation':0,'fold_or_backtest_end':0,'other':0}
    for reason,count in early_reasons.items():
        category=('liquidation' if 'LIQUIDATION' in reason else
                  'stop_loss' if 'STOP' in reason else
                  'fold_or_backtest_end' if any(token in reason for token in ('FINAL_EXIT','FOLD','END')) else 'other')
        exceptions[category]+=count
    return dict(holding_hours_mean=float(hours.mean()) if len(hours) else 0.,
        holding_hours_median=float(hours.median()) if len(hours) else 0.,
        holding_hours_min=float(hours.min()) if len(hours) else 0.,
        holding_hours_max=float(hours.max()) if len(hours) else 0.,
        holding_hours_total=float(hours.sum()),backtest_window_hours=window,
        occupied_hours=occupied,time_in_market=occupied/window if window else 0.,
        holding_lt_4h_trades=int((hours<4-tolerance).sum()),
        holding_within_4h_7d_trades=int(((hours>=4-tolerance)&(hours<=168+tolerance)).sum()),
        holding_gt_7d_trades=int((hours>168+tolerance).sum()),
        holding_target_min_hours=float(min_hours),holding_target_max_hours=float(max_hours),
        holding_below_target_trades=int((hours<min_hours-tolerance).sum()),
        holding_within_target_trades=int(((hours>=min_hours-tolerance)&(hours<=max_hours+tolerance)).sum()),
        holding_above_target_trades=int((hours>max_hours+tolerance).sum()),
        early_exit_reason_counts=early_reasons,early_exit_exception_counts=exceptions,
        exit_reason_counts=reasons)


def _partial_events(root):
    """从已有事件账本筛选分批减仓记录。

    Args:
        root: 当前任务的项目/数据根目录路径；不会自动读取其他目录。

    Returns:
        减仓事件DataFrame；缺文件/空文件返回空表。
    """
    path=root/'events.csv'
    if not path.exists():return pd.DataFrame()
    try:events=pd.read_csv(path,low_memory=False)
    except pd.errors.EmptyDataError:return pd.DataFrame()
    kind='event' if 'event' in events else 'operation'
    if kind not in events or 'config' not in events:return pd.DataFrame()
    return events[events[kind]=='PARTIAL_EXIT'].copy()


def _trade_partials(events, key, trade):
    """按账户配置、验证折和仓位ID匹配一笔持仓的减仓事件。

    Args:
        events: 已保存的事件账本或待规范化的资金费记录。
        key: 稳定通知/账户配置标识，或当前记录的可选字段名。
        trade: 当前完整仓位的交易记录。

    Returns:
        按时间排序的匹配事件表。
    """
    if events.empty:return events
    matching=events[events.config==key]
    if 'fold' in matching and 'fold' in trade:
        matching=matching[matching.fold==trade['fold']]
    if 'position_id' in matching and pd.notna(trade.get('position_id')):
        matching=matching[matching.position_id==trade['position_id']]
    elif 'entry_time' in matching:
        matching=matching[matching.entry_time==trade.entry_time]
    else:
        matching=matching[(matching.time>=trade.entry_time)&(matching.time<=trade.exit_time)]
    return matching.sort_values('time')


def export_operations_report(run_dir):
    """将既有账本转换为独立账户曲线、逐笔操作和持仓统计。

    Args:
        run_dir: 现有研究运行目录；只向其中写派生报告。

    Returns:
        完整操作汇总；只生成派生报告，不重跑模型或发邮件。
    """
    root=Path(run_dir).resolve()
    report=json.loads((root/'report.json').read_text())
    curves=json.loads((root/'validation_curves.json').read_text())
    try:ledger=pd.read_csv(root/'trades.csv')
    except pd.errors.EmptyDataError:ledger=pd.DataFrame()
    symbol=report.get('source',{}).get('symbol','UNKNOWN')
    ranking=report['ranking']
    if not ranking:raise ValueError('Report has no configurations')
    selected=report.get('selected')
    default=selected['config'] if isinstance(selected,dict) else ranking[0]['config']
    if default not in curves:raise ValueError('Default configuration curve missing')
    partial_events=_partial_events(root)
    readable=ledger.copy()
    if len(readable):
        readable['symbol']=symbol
        readable['entry_time_shanghai']=shanghai(readable.entry_time)
        readable['exit_time_shanghai']=shanghai(readable.exit_time)
        readable['direction_label']=np.where(readable.direction==1,'做多','做空')
        readable['holding_hours']=(readable.exit_time.astype(float)-readable.entry_time.astype(float))/3_600_000
        if (readable.holding_hours<0).any():raise ValueError('Trade exits before entry')
        readable['holding_duration']=readable.holding_hours.map(_duration)
    else:
        readable=pd.DataFrame(columns=['config','symbol','entry_time_shanghai','exit_time_shanghai','direction_label','holding_hours','holding_duration'])
    readable.to_csv(root/'trades_readable.csv',index=False,encoding='utf-8-sig')
    accounts={};payload={};operations=[];partial_rows=[]
    for row in ranking:
        key=row['config']
        series=sorted((int(time),float(value)) for time,value in curves[key].items())
        if not series or not np.isfinite(np.asarray(series,dtype=float)).all():raise ValueError('Invalid equity curve')
        group=readable[readable.config==key].copy()
        factor=group.fold_start_equity.astype(float) if len(group) else pd.Series(dtype=float)
        pnl=(group.net_pnl.astype(float)*factor) if len(group) else pd.Series(dtype=float)
        net=series[-1][1]-1
        if abs(net-float(row['net_return']))>1e-8 or abs(net-float(pnl.sum()))>1e-8:
            raise ValueError('Equity/trade PnL mismatch for '+key)
        fees=float((group.total_fee.astype(float)*factor).sum()) if len(group) else 0.
        funding=float((group.funding_paid.astype(float)*factor).sum()) if len(group) else 0.
        account=dict(config=key,symbol=symbol,timeframe=row['timeframe'],model=row['model'],exit_mode=row['exit_mode'],
            round_trips=len(group),open_operations=len(group),close_operations=len(group),operations=2*len(group),
            long_trades=int((group.direction==1).sum()) if len(group) else 0,
            short_trades=int((group.direction==-1).sum()) if len(group) else 0,
            net_return=net,fees=fees,funding=funding,win_rate=float((pnl>0).mean()) if len(group) else 0.,
            max_drawdown=float(row.get('max_drawdown',0.)),complete_folds=row.get('complete_folds'),
            eligible=bool(row.get('eligible',False)),rejection_reasons=row.get('rejection_reasons',[]))
        policy=report.get('holding_policy',{})
        account.update(_holding_summary(group,series,float(policy.get('min_holding_hours',4)),float(policy.get('max_holding_hours',168))))
        account['partial_exit_operations']=0
        accounts[key]=account
        trade_rows=[];account_partials=[]
        for n,(_,trade) in enumerate((group.sort_values(['entry_time','exit_time']) if len(group) else group).iterrows(),1):
            scale=float(trade.fold_start_equity)
            direction=int(trade.direction)
            contribution=float(trade.net_pnl)*scale
            partials=_trade_partials(partial_events,key,trade)
            partial_quantity=float(partials.quantity.sum()) if len(partials) and 'quantity' in partials else 0.
            partial_pnl=float(partials.net_pnl.sum()) if len(partials) and 'net_pnl' in partials else 0.
            partial_fee=float(partials.fee.sum()) if len(partials) and 'fee' in partials else 0.
            final_exit_fee=float(trade.get('final_exit_fee',max(0.,float(trade.exit_fee)-partial_fee)))
            partial_funding=float(partials.funding_paid.sum()) if len(partials) and 'funding_paid' in partials else 0.
            # Existing reports lack events.csv; do not fabricate their timestamps.
            account['partial_exit_operations']+=len(partials)
            trade_rows.append(dict(number=n,symbol=symbol,direction='做多' if direction==1 else '做空',
                entry_time=trade.entry_time_shanghai,exit_time=trade.exit_time_shanghai,
                entry_timestamp=int(trade.entry_time),exit_timestamp=int(trade.exit_time),
                holding_hours=float(trade.holding_hours),holding_duration=trade.holding_duration,
                partial_exit_count=int(trade.get('partial_exit_count',len(partials))),
                entry_price=float(trade.entry_price),exit_price=float(trade.exit_price),
                directional_price_return=float(trade.get('directional_price_return',float(trade.gross_pnl)/float(trade.entry_notional))),
                margin_return=float(trade.get('margin_return',float(trade.net_pnl)/float(trade.allocated_margin))),
                reason=str(trade.exit_reason),pattern=str(trade.get('pattern','')),
                contribution=contribution,account_return=float(trade.get('account_return',0.)),
                cumulative_return=float(trade.account_equity_after)*scale-1,
                precision=str(trade.get('exit_time_precision',''))))
            for opening in (True,False):
                event_time=int(trade.entry_time if opening else trade.exit_time)
                action=('开多' if direction==1 else '开空') if opening else ('平多' if direction==1 else '平空')
                buy=(direction==1) if opening else (direction==-1)
                operations.append(dict(config=key,symbol=symbol,timeframe=row['timeframe'],model=row['model'],
                    exit_mode=row['exit_mode'],fold=int(trade.get('fold',0)),trade_number=n,
                    operation='OPEN' if opening else 'CLOSE',action_label=action,side='BUY' if buy else 'SELL',
                    time=event_time,time_shanghai=trade.entry_time_shanghai if opening else trade.exit_time_shanghai,
                    price=float(trade.entry_price if opening else trade.exit_price),
                    quantity_initial_equity_unit=(float(trade.get('quantity',0.)) if opening else max(0.,float(trade.get('quantity',0.))-partial_quantity))*scale,
                    fee_initial_equity_fraction=(float(trade.entry_fee) if opening else final_exit_fee)*scale,
                    funding_initial_equity_fraction=0. if opening else (float(trade.funding_paid)-partial_funding)*scale,
                    pnl_initial_equity_fraction=None if opening else contribution-partial_pnl*scale,
                    exit_reason='' if opening else str(trade.exit_reason),
                    time_precision='bar_open' if opening else str(trade.get('exit_time_precision',''))))
            for partial in partials.to_dict('records'):
                timestamp=int(partial['time'])
                time_local=pd.to_datetime(timestamp,unit='ms',utc=True).tz_convert('Asia/Shanghai').strftime('%Y-%m-%d %H:%M:%S')
                reason=str(partial.get('reason','TIME_DEVIATION'))
                holding_hours=(timestamp-int(trade.entry_time))/3_600_000
                value=dict(config=key,symbol=symbol,timeframe=row['timeframe'],model=row['model'],
                    exit_mode=row['exit_mode'],fold=int(trade.get('fold',0)),trade_number=n,
                    operation='PARTIAL_EXIT',action_label='减多' if direction==1 else '减空',
                    side='SELL' if direction==1 else 'BUY',time=timestamp,time_shanghai=time_local,
                    price=float(partial['price']),quantity_initial_equity_unit=float(partial.get('quantity',0.))*scale,
                    fee_initial_equity_fraction=float(partial.get('fee',0.))*scale,
                    funding_initial_equity_fraction=float(partial.get('funding_paid',0.))*scale,
                    pnl_initial_equity_fraction=float(partial.get('net_pnl',0.))*scale,
                    exit_reason=reason,time_precision=str(partial.get('timestamp_precision','bar_open')),
                    holding_hours=holding_hours,holding_duration=_duration(holding_hours),
                    remaining_quantity_initial_equity_unit=float(partial.get('remaining_quantity',0.))*scale)
                operations.append(value);partial_rows.append(value)
                account_partials.append(dict(trade_number=n,time=time_local,price=value['price'],
                    direction='做多' if direction==1 else '做空',reason=reason,
                    quantity=value['quantity_initial_equity_unit'],remaining_quantity=value['remaining_quantity_initial_equity_unit'],
                    holding_hours=holding_hours,holding_duration=value['holding_duration'],
                    contribution=value['pnl_initial_equity_fraction']))
        account['operations']=account['open_operations']+account['close_operations']+account['partial_exit_operations']
        # Full-resolution curve remains in validation_curves.json and account_equity.csv.
        chosen=np.unique(np.linspace(0,len(series)-1,min(500,len(series)),dtype=int))
        payload[key]=dict(summary=account,curve=[series[i] for i in chosen],trades=trade_rows,partials=account_partials)
    operation_columns=['config','symbol','timeframe','model','exit_mode','fold','trade_number','operation','action_label','side','time','time_shanghai','price','quantity_initial_equity_unit','fee_initial_equity_fraction','funding_initial_equity_fraction','pnl_initial_equity_fraction','exit_reason','time_precision','holding_hours','holding_duration','remaining_quantity_initial_equity_unit']
    pd.DataFrame(operations,columns=operation_columns).sort_values(['config','time','operation']).to_csv(root/'operations.csv',index=False,encoding='utf-8-sig')
    pd.DataFrame(partial_rows,columns=operation_columns).to_csv(root/'partial_exits.csv',index=False,encoding='utf-8-sig')
    holding_columns=['config','symbol','round_trips','open_operations','close_operations','partial_exit_operations',
        'holding_hours_mean','holding_hours_median','holding_hours_min','holding_hours_max','holding_hours_total',
        'backtest_window_hours','occupied_hours','time_in_market','holding_lt_4h_trades',
        'holding_within_4h_7d_trades','holding_gt_7d_trades','holding_target_min_hours','holding_target_max_hours',
        'holding_below_target_trades','holding_within_target_trades','holding_above_target_trades','early_exit_reason_counts','early_exit_exception_counts']
    pd.DataFrame(accounts.values(),columns=holding_columns).to_csv(root/'holding_summary.csv',index=False,encoding='utf-8-sig')
    all_equity=[dict(config=key,time=int(time),equity=float(value),return_rate=float(value)-1) for key in accounts for time,value in sorted(curves[key].items(),key=lambda pair:int(pair[0]))]
    pd.DataFrame(all_equity).to_csv(root/'account_equity.csv',index=False)
    summary=dict(symbol=symbol,status=report.get('status','UNKNOWN'),independent_accounts=True,
        configurations=len(accounts),total_round_trips=len(readable),total_operations=len(operations),
        total_open_operations=len(readable),total_close_operations=len(readable),total_partial_exit_operations=len(partial_rows),
        default_config=default,accounts=accounts,actual_email_sent=False,real_orders_enabled=False)
    (root/'operation_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    _plot_default(root,default,accounts[default],curves[default],symbol)
    _write_html(root,summary,payload,report)
    original=root/'comparison.html'
    if original.exists():
        text=original.read_text()
        if 'id="operations-link"' not in text:
            link='<p><a id="operations-link" href="operations.html">查看标的、开平仓次数、逐笔操作和账户总收益曲线</a></p>'
            text=text.replace('</style>','</style>'+link,1) if '</style>' in text else link+text
            original.write_text(text,encoding='utf-8')
    return summary


def _plot_default(root,key,account,curve,symbol):
    """为默认独立账户绘制权益、回撤与持仓时间线。

    Args:
        root: 当前任务的项目/数据根目录路径；不会自动读取其他目录。
        key: 稳定通知/账户配置标识，或当前记录的可选字段名。
        account: 当前单一独立账户的汇总。
        curve: 一个独立账户的已有权益/回撤序列。
        symbol: 精确产品代码；股票、ETF与相关永续不能混用。

    Returns:
        无返回值；图仅用于展示已有账本。
    """
    os.environ.setdefault('MPLCONFIGDIR',str(Path(tempfile.gettempdir())/'quant_lab_matplotlib'))
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    pairs=sorted((int(t),float(e)) for t,e in curve.items())
    time=pd.to_datetime([t for t,e in pairs],unit='ms',utc=True).tz_convert('Asia/Shanghai')
    value=(np.array([e for t,e in pairs])-1)*100
    fig,ax=plt.subplots(figsize=(13,5),layout='constrained')
    ax.plot(time,value,color='#2563eb',linewidth=1.5)
    ax.axhline(0,color='#9ca3af',linewidth=.8);ax.grid(alpha=.2)
    ax.set(title=f'{symbol} | {key} | {account["round_trips"]} round trips | net {account["net_return"]:.2%}',
           xlabel='Shanghai time | independent simulated account',ylabel='Cumulative account return (%)')
    ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=3,maxticks=6))
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d',tz=ZoneInfo('Asia/Shanghai')))
    for ext in ('png','svg'):fig.savefig(root/('overall_equity.'+ext),dpi=150)
    plt.close(fig)


def _write_html(root,summary,payload,report):
    """生成含账户选择、曲线和逐笔操作的离线HTML页面。

    Args:
        root: 当前任务的项目/数据根目录路径；不会自动读取其他目录。
        summary: 已经计算的操作/账户汇总字典。
        payload: 通过API schema校验的请求数据或报告绘图数据。
        report: 已经计算并保存的研究报告字典。

    Returns:
        无返回值；写入当前派生报告目录。
    """
    safe=lambda value:html.escape(str(value),quote=True)
    options=''.join(f'<option value="{safe(key)}">{safe(key)} · {value["round_trips"]}笔 · {value["net_return"]:.2%}</option>' for key,value in summary['accounts'].items())
    document='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>交易操作与账户收益</title>
<style>body{font:15px system-ui,sans-serif;background:#f3f6fb;color:#172334;margin:0}main{max-width:1280px;margin:auto;padding:24px}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px}.card,section{background:white;border:1px solid #dce3ed;border-radius:10px;padding:16px;margin-bottom:14px}.value{font-size:25px;font-weight:650;margin-top:6px}.muted{color:#64748b}.notice{background:#fff7df;border-left:4px solid #dfac23;padding:12px;margin:14px 0}select{width:100%;padding:10px;font-size:15px}svg{width:100%;height:auto}table{width:100%;border-collapse:collapse;white-space:nowrap}td,th{text-align:left;padding:10px;border-bottom:1px solid #e2e8f0;font-size:13px}th{background:#f4f7fc}button{padding:7px 12px;margin:8px}a{color:#1d4ed8}.table-wrap{overflow:auto;max-height:650px}.up{color:#15803d}.down{color:#c62828}.row{display:flex;gap:12px;align-items:center;flex-wrap:wrap}</style>
<main><h1>交易操作与账户总收益曲线</h1>'''
    document+=f'<p class="muted">标的：{safe(summary["symbol"])} · 研究状态：{safe(summary["status"])} · 历史验证期模拟记录</p><div class="cards">'
    analysis=root.parents[1]/'analysis'/summary['symbol']/'index.html'
    if analysis.exists():document+=f'<p><a href="../../analysis/{safe(summary["symbol"])}/index.html">本标的多周期K线、支撑压力与波浪分析</a> · <a href="../../index.html">总体报告</a></p>'
    for label,value in [('本次标的',summary['symbol']),('独立回测组合',summary['configurations']),('全部实验完整交易',summary['total_round_trips']),('全部实验开仓 / 完整平仓 / 减仓',f'{summary["total_open_operations"]} / {summary["total_close_operations"]} / {summary["total_partial_exit_operations"]}')]:
        document+=f'<div class="card">{safe(label)}<div class="value">{safe(value)}</div></div>'
    document+='</div><div class="notice">全部实验次数来自多个独立模拟账户。下方曲线、收益率和持仓时间对应你当前选择的一个账户。不能将不同策略的持仓时长相加作为单个账户的持仓占比。</div>'
    if report.get('holdout'):
        test=report['holdout'];stress=report.get('holdout_double_cost',{})
        document+=f'<div class="notice">单一验证候选的预留测试：{safe(report["selected"]["config"])}；{test.get("entries",0)}次开仓，净收益{float(test["net_return"]):.2%}，双倍成本净收益{float(stress.get("net_return",0)):.2%}。最终状态：{safe(report["status"])}。下方图表属于验证期，不能用验证盈利替代测试结果。<a href="selected_holdout_trades.csv">测试逐笔交易及持仓时长</a> · <a href="selected_holdout_equity.csv">测试净值</a></div>'
    policy=report.get('holding_policy',{})
    if policy:
        minimum=float(policy.get('min_holding_hours',4))
        maximum=float(policy.get('max_holding_hours',168))
        delay=float(policy.get('execution_delay_minutes',15))
        scale_after=float(policy.get('scale_out_after_hours',72))
        scale_interval=float(policy.get('scale_out_interval_hours',24))
        scale_fraction=float(policy.get('scale_out_fraction',.25))
        deviation=float(policy.get('scale_out_deviation_atr',3))
        document+=f'<div class="notice">本轮启用持仓时间控制：策略性退出最短约{minimum:g}小时，最长约{maximum:g}小时（{maximum/24:g}天）；硬止损、强平及验证折 / 回测期末截尾仍可提前退出。信号执行延迟{delay:g}分钟。持仓达到{scale_after:g}小时且价格偏离入场价至少{deviation:g}倍入场ATR时，每隔{scale_interval:g}小时减少初始数量的{scale_fraction:.0%}，实际减仓在下方逐次列出。</div>'
    swing=report.get('swing_policy')
    if swing:
        document+=f'<div class="notice">价格波段机会筛选：最近{swing["lookback_hours"]:g}小时完整4小时K线的(最高价−最低价)/最低价为{swing["min_range"]:.0%}～{swing["max_range"]:.0%}。普通止盈至少要求标的有利价格变动{swing["min_profit_move_pct"]:.0%}；波段百分比模式目标{swing["price_target_pct"]:.0%}。历史波动符合条件不保证未来能获利；保护止损、趋势退出、持仓上限、减仓和研究截尾可在目标前发生。</div>'
    document+='<section><h2>选择要看的策略账户</h2><select id="config">'+options+'</select><p id="eligibility" class="notice"></p><div class="cards" id="stats"></div><h2>整体账户累计收益曲线（验证期）</h2><svg id="curve" viewBox="0 0 1100 340" role="img" aria-label="账户累计收益曲线"></svg><p class="muted">账户初始净值为1，收益已包含模拟手续费、滑点和资金费。网页曲线最多显示500点；完整曲线可下载。时间为上海时间。</p><p><a href="overall_equity.png">默认账户曲线 PNG</a> · <a href="overall_equity.svg">SVG</a> · <a href="account_equity.csv">全部账户完整净值 CSV</a> · <a href="comparison.html">模型与止盈方式比较</a></p></section>'
    document+='<section><h2>当前账户整体持仓时间</h2><div class="cards" id="holding-stats"></div><h3 id="holding-distribution-title">持仓时长分布</h3><svg id="holding-distribution" viewBox="0 0 1100 160" role="img" aria-label="持仓时长分布"></svg><p id="holding-exceptions" class="notice"></p><h3>整个验证期的持仓时间线</h3><svg id="holding-timeline" viewBox="0 0 1100 180" role="img" aria-label="逐笔持仓时间线"></svg><p class="muted">蓝色为做多，紫色为做空；横线从开仓持续到完整平仓，减仓后剩余仓位仍计为持仓。悬停查看逐笔时长。时长按平仓时间减开仓时间计算；K线内退出只知道所属K线，时间与时长均为近似值。分布区间使用本轮持仓参数，边界仅容许K线末尾的1毫秒时间戳误差。持仓占比以净值曲线覆盖的回测窗口为分母。</p><p><a href="holding_summary.csv">全部独立账户持仓统计 CSV</a></p></section>'
    document+='<section><h2>当前账户逐笔操作</h2><p>每一行是一笔完整交易，包含开仓、期间减仓和最终平仓。做多：买入开仓、卖出平仓；做空：卖出开仓、买入平仓。标的方向涨跌按全部减仓及平仓的加权价差除以初始名义价值；保证金净收益按本笔净损益除以初始保证金；账户收益按本笔净损益除以开仓前账户净值。K线内平仓时间为近似值。</p><div class="row"><span id="table-count"></span><button id="previous">上一页</button><button id="next">下一页</button></div><div class="table-wrap"><table><thead><tr><th>序号</th><th>标的</th><th>操作</th><th>开仓时间</th><th>开仓价</th><th>平仓时间</th><th>持仓时长</th><th>减仓次数</th><th>平仓价</th><th>退出原因</th><th>标的方向涨跌</th><th>保证金净收益</th><th>本笔账户收益</th><th>累计账户收益</th></tr></thead><tbody id="trades"></tbody></table></div><p><a href="operations.csv">全部开仓、减仓、完整平仓操作 CSV</a> · <a href="trades_readable.csv">逐笔交易及持仓时长 CSV</a> · <a href="operation_summary.json">详细统计</a></p></section>'
    document+='<section><h2>当前账户逐步减仓记录</h2><p id="partial-count"></p><div class="table-wrap"><table><thead><tr><th>所属交易</th><th>方向</th><th>减仓时间</th><th>已持仓</th><th>减仓价</th><th>减少数量</th><th>剩余数量</th><th>原因</th><th>本次账户收益贡献</th></tr></thead><tbody id="partials"></tbody></table></div><p class="muted">数量以初始账户权益为1的单位表示。减仓损益已包含在对应完整交易中；本笔账户收益与此处收益不能重复相加。</p><p><a href="partial_exits.csv">全部减仓记录 CSV</a></p></section></main>'
    embedded=json.dumps(dict(default=summary['default_config'],accounts=payload,selected=bool(report.get('selected')),
        status=report.get('status'),selected_config=report['selected'].get('config') if isinstance(report.get('selected'),dict) else None),ensure_ascii=False,allow_nan=False).replace('<','\\u003c')
    document+='<script id="report-data" type="application/json">'+embedded+'</script>'
    document+='''<script>
const data=JSON.parse(document.getElementById('report-data').textContent), select=document.getElementById('config');
let page=0; const pageSize=50;
const percent=x=>(x*100).toFixed(2)+'%';
const date=t=>new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit'}).format(new Date(t));
const escape=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const reasons={TAKE_PROFIT:'止盈',TAKE_PROFIT_GAP:'跳空止盈',STOP_LOSS:'止损',STOP_LOSS_GAP:'跳空止损',ATR_TRAILING_STOP:'ATR跟踪退出',THREE_BAR_TRAILING_PROFIT:'三根K线跟踪止盈',TIME_EXIT:'时间退出',MAX_HOLDING_TIME:'达到持仓上限',HIGHER_TREND_REVERSAL:'大周期反向',TREND_REVERSAL:'大周期反向',OPPOSITE_SIGNAL:'反向信号',FINAL_EXIT:'验证折 / 回测期末强制平仓',LIQUIDATION_INTRABAR:'盘中近似强平',LIQUIDATION_OPEN_GAP:'跳空强平',LIQUIDATION_AFTER_FUNDING:'资金费后强平',TIME_DEVIATION_FAVORABLE:'持仓较久且盈利偏移，逐步减仓',TIME_DEVIATION_ADVERSE:'持仓较久且亏损偏移，逐步减仓',TIME_SCALE_OUT_COMPLETE_FAVORABLE:'按时间与有利偏移完成减仓',TIME_SCALE_OUT_COMPLETE_ADVERSE:'按时间与不利偏移完成减仓'};
const duration=h=>h>=24?(h/24).toFixed(2)+'天':h.toFixed(2)+'小时';
function holding(account){const s=account.summary;
 document.getElementById('holding-stats').innerHTML=[['平均持仓',duration(s.holding_hours_mean)],['中位持仓',duration(s.holding_hours_median)],['最短 / 最长',duration(s.holding_hours_min)+' / '+duration(s.holding_hours_max)],['累计持仓',duration(s.holding_hours_total)],['回测窗口',duration(s.backtest_window_hours)],['有仓位时间占比',percent(s.time_in_market)]].map(([label,value])=>`<div class="card">${label}<div class="value">${escape(value)}</div></div>`).join('');
 const lowLabel=duration(s.holding_target_min_hours),highLabel=duration(s.holding_target_max_hours);
 document.getElementById('holding-distribution-title').textContent='持仓时长分布（本轮目标：'+lowLabel+'～'+highLabel+'）';
 const buckets=[['低于下限',s.holding_below_target_trades,'#ea580c'],['目标范围内',s.holding_within_target_trades,'#2563eb'],['高于上限',s.holding_above_target_trades,'#dc2626']],max=Math.max(1,...buckets.map(x=>x[1]));
 document.getElementById('holding-distribution').innerHTML=buckets.map(([label,count,color],i)=>`<text x="10" y="${35+i*47}" fill="#475569" font-size="15">${label}</text><rect x="160" y="${14+i*47}" width="${Math.max(0,820*count/max)}" height="28" rx="4" fill="${color}"/><text x="${175+820*count/max}" y="${35+i*47}" fill="#334155" font-size="15">${count}笔</text>`).join('');
 const exceptions=Object.entries(s.early_exit_reason_counts).map(([reason,count])=>(reasons[reason]||reason)+' '+count+'笔').join('；');
 document.getElementById('holding-exceptions').textContent='不足'+lowLabel+' '+s.holding_below_target_trades+'笔'+(exceptions?'：'+exceptions:'。')+' 止损、强平和验证折末强制平仓需单独查看；早退记录均保留在统计中。';
 const curve=account.curve,start=curve[0][0],end=curve[curve.length-1][0],x=t=>80+955*(t-start)/Math.max(1,end-start);
 let lines='<text x="10" y="60" fill="#2563eb">做多</text><text x="10" y="110" fill="#7c3aed">做空</text><line x1="80" x2="1035" y1="55" y2="55" stroke="#e2e8f0"/><line x1="80" x2="1035" y1="105" y2="105" stroke="#e2e8f0"/>';
 lines+=account.trades.map(t=>`<rect x="${x(t.entry_timestamp)}" y="${t.direction==='做多'?44:94}" width="${Math.max(1,x(t.exit_timestamp)-x(t.entry_timestamp))}" height="22" rx="2" fill="${t.direction==='做多'?'#2563eb':'#7c3aed'}"><title>${escape('#'+t.number+' '+t.direction+' '+t.entry_time+' → '+t.exit_time+'；持仓 '+t.holding_duration)}</title></rect>`).join('');
 for(let i=0;i<=4;i++){const t=start+(end-start)*i/4;lines+=`<text x="${x(t)}" y="155" text-anchor="middle" fill="#64748b" font-size="12">${date(t)}</text>`;}
 document.getElementById('holding-timeline').innerHTML=lines;
 const partials=account.partials;document.getElementById('partial-count').textContent='开仓 '+s.open_operations+'次，完整平仓 '+s.close_operations+'次，部分减仓 '+s.partial_exit_operations+'次。';
 document.getElementById('partials').innerHTML=partials.map(t=>`<tr><td>${t.trade_number}</td><td>${escape(t.direction)}</td><td>${escape(t.time)}</td><td>${escape(t.holding_duration)}</td><td>${t.price.toFixed(2)}</td><td>${t.quantity.toPrecision(6)}</td><td>${t.remaining_quantity.toPrecision(6)}</td><td>${escape(reasons[t.reason]||t.reason)}</td><td>${percent(t.contribution)}</td></tr>`).join('')||'<tr><td colspan="9">此账户没有可用减仓事件；旧报告需按新参数重新回测后才有减仓记录。</td></tr>';
}
function draw(curve){
 const svg=document.getElementById('curve'), xs=curve.map(p=>p[0]), ys=curve.map(p=>(p[1]-1)*100);
 let low=Math.min(0,...ys), high=Math.max(0,...ys), span=high-low;if(span<.01){low-=.05;high+=.05;span=high-low;}
 low-=span*.08;high+=span*.08;span=high-low;
 const x=t=>65+990*(t-xs[0])/Math.max(1,xs[xs.length-1]-xs[0]), y=v=>285-240*(v-low)/span;
 let body='';for(let i=0;i<=4;i++){const value=low+(high-low)*i/4,py=y(value);body+=`<line x1="65" x2="1055" y1="${py}" y2="${py}" stroke="#e2e8f0"/><text x="4" y="${py+4}" fill="#64748b" font-size="12">${value.toFixed(2)}%</text>`;}
 for(let i=0;i<=4;i++){const t=xs[0]+(xs[xs.length-1]-xs[0])*i/4;body+=`<text x="${x(t)}" y="320" text-anchor="middle" fill="#64748b" font-size="12">${date(t)}</text>`;}
 body+=`<line x1="65" x2="1055" y1="${y(0)}" y2="${y(0)}" stroke="#94a3b8" stroke-dasharray="4 4"/>`;
 body+=`<polyline points="${curve.map(p=>x(p[0]).toFixed(1)+','+y((p[1]-1)*100).toFixed(1)).join(' ')}" fill="none" stroke="#2563eb" stroke-width="2.2"/>`;
 svg.innerHTML=body;
}
function table(){const trades=data.accounts[select.value].trades,total=Math.max(1,Math.ceil(trades.length/pageSize));page=Math.max(0,Math.min(page,total-1));
 document.getElementById('table-count').textContent=`共 ${trades.length} 笔完整交易，第 ${page+1}/${total} 页`;
 document.getElementById('trades').innerHTML=trades.slice(page*pageSize,(page+1)*pageSize).map(t=>`<tr><td>${t.number}</td><td>${escape(t.symbol)}</td><td>${t.direction==='做多'?'开多 → 平多':'开空 → 平空'}</td><td>${escape(t.entry_time)}</td><td>${t.entry_price.toFixed(2)}</td><td>${escape(t.exit_time)}${t.precision.includes('approximation')?' ≈':''}</td><td>${escape(t.holding_duration)}</td><td>${t.partial_exit_count}</td><td>${t.exit_price.toFixed(2)}</td><td>${escape(reasons[t.reason]||t.reason)}</td><td>${percent(t.directional_price_return)}</td><td>${percent(t.margin_return)}</td><td class="${t.account_return>=0?'up':'down'}">${percent(t.account_return)}</td><td>${percent(t.cumulative_return)}</td></tr>`).join('')||'<tr><td colspan="14">此配置没有交易</td></tr>';
}
function render(){const a=data.accounts[select.value],s=a.summary;
 document.getElementById('stats').innerHTML=[['标的',s.symbol],['开仓 / 完整平仓 / 减仓',s.open_operations+' / '+s.close_operations+' / '+s.partial_exit_operations],['做多 / 做空',s.long_trades+' / '+s.short_trades],['总账户收益',percent(s.net_return)],['胜率',percent(s.win_rate)],['压力回撤近似',percent(s.max_drawdown)],['手续费占初始账户',percent(s.fees)],['资金费净支出（负数为收入）',percent(s.funding)]].map(([label,value])=>`<div class="card">${label}<div class="value">${escape(value)}</div></div>`).join('');
 const state=s.eligible?(data.status==='REJECTED'&&data.selected_config===select.value?'验证筛选合格，但预留测试未通过，最终已拒绝。':'验证筛选合格，最终结果见预留测试。'):'未通过筛选：'+(s.rejection_reasons.join('；')||'见研究报告');
 document.getElementById('eligibility').textContent=(data.selected?'当前显示一个研究账户。':'当前没有选中可启用模型；默认仅展示报告排名首位，供观察。')+' '+state+' 完整训练折：'+s.complete_folds+'/3。';
 page=0;draw(a.curve);holding(a);table();}
select.value=data.default;select.addEventListener('change',render);document.getElementById('previous').onclick=()=>{page--;table()};document.getElementById('next').onclick=()=>{page++;table()};render();
</script></html>'''
    (root/'operations.html').write_text(document,encoding='utf-8')


def print_operations_summary(run_dir,summary):
    """在控制台打印操作报告入口及关键交易/持仓指标。

    Args:
        run_dir: 现有研究运行目录；只向其中写派生报告。
        summary: 已经计算的操作/账户汇总字典。

    Returns:
        无返回值。
    """
    import shlex
    root=Path(run_dir).resolve();account=summary['accounts'][summary['default_config']]
    print(f"标的：{summary['symbol']}；独立配置：{summary['configurations']}；全部实验完整交易：{summary['total_round_trips']} 笔；交易操作（含减仓）：{summary['total_operations']} 次。",flush=True)
    print(f"默认观察账户：{summary['default_config']}；开仓 {account['open_operations']} 次、完整平仓 {account['close_operations']} 次、减仓 {account['partial_exit_operations']} 次；净收益 {account['net_return']:.2%}（研究展示）。",flush=True)
    print(f"持仓时间：平均 {_duration(account['holding_hours_mean'])}，中位 {_duration(account['holding_hours_median'])}，最长 {_duration(account['holding_hours_max'])}；累计 {_duration(account['holding_hours_total'])}，占回测窗口 {account['time_in_market']:.2%}。",flush=True)
    print('操作及收益报告：'+str(root/'operations.html'),flush=True)
    print('打开报告：xdg-open '+shlex.quote(str(root/'operations.html')),flush=True)
