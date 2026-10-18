"""Multi-period candles, confirmed structure, cycles, Dow123 and wave scenarios."""
import html
import hashlib
import json
from pathlib import Path
import numpy as np
import pandas as pd
from quant_lab.analysis.market_structure import structure_features,wave_candidate,multitimeframe_structure
from quant_lab.reports.trend_report import load_bars,aggregate

PERIODS=('15m','30m','1h','2h','4h','1d','1w','1mo')
MINUTES={'15m':15,'30m':30,'1h':60,'2h':120,'4h':240,'1d':1440,'1w':10080,'1mo':43200}
NAMES={'KORUUSDT':'韩国MSCI指数每日3倍做多ETF永续','CLUSDT':'原油永续','NVDAUSDT':'英伟达股票永续','BTCUSDT':'比特币永续','ETHUSDT':'以太坊永续'}
TREND={1:'上升',-1:'下降',0:'震荡 / 分歧',None:'确认转折不足'}
SOURCES=[('币安合约规格与市场数据','https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Exchange-Information'),
    ('Elliott推动浪与修正规则','https://chartschool.stockcharts.com/table-of-contents/market-analysis/elliott-wave-analysis-articles/identifying-elliott-wave-patterns'),
    ('波浪周期与主观计数说明','https://chartschool.stockcharts.com/table-of-contents/market-analysis/elliott-wave-analysis-articles/introduction-to-elliott-wave-theory'),
    ('KORU产品说明','https://www.direxion.com/product/daily-msci-south-korea-bull-3x-etf')]


def local_time(ms):
    """将单个毫秒时间戳显示为上海本地时间。

    Args:
        ms: UTC毫秒时间戳。

    Returns:
        时间字符串。
    """
    return pd.to_datetime(ms,unit='ms',utc=True).tz_convert('Asia/Shanghai').strftime('%Y-%m-%d %H:%M')


def spectral_cycle(candles,timeframe):
    """从已收盘历史样本估计主要频谱周期供描述使用。

    Args:
        candles: 已经完整收盘的OHLCV记录/表。
        timeframe: 源或目标周期名称，例如15m/4h。

    Returns:
        周期长度及样本充足状态，不是未来拐点预测。
    """
    n=min(256,len(candles))
    if n<32:return dict(status='INSUFFICIENT_DATA',observations=n,note='少于32根完整K线，不估计主导周期。')
    value=np.log(candles.close.iloc[-n:].to_numpy());x=np.arange(n)
    detrended=value-np.polyval(np.polyfit(x,value,1),x)
    power=np.abs(np.fft.rfft(detrended*np.hanning(n)))**2
    periods=np.divide(n,np.arange(len(power)),out=np.full(len(power),np.inf),where=np.arange(len(power))!=0)
    valid=(periods>=6)&(periods<=n/2);indices=np.flatnonzero(valid)
    if not len(indices) or power[indices].sum()<1e-16:return dict(status='FLAT_OR_UNRESOLVED',observations=n,note='去趋势后周期能量不足。')
    index=int(indices[np.argmax(power[indices])]);period=float(periods[index])
    return dict(status='EXPLORATORY',observations=n,period_bars=period,period_hours=period*MINUTES[timeframe]/60,
        observed_repetitions=n/period,spectral_power_share=float(power[index]/power[indices].sum()),
        note='仅描述过去完整K线中的去趋势频谱峰值；不是未来拐点时间，也没有做统计显著性保证。')


def support_resistance(candles,pivots,reference_price=None):
    """根据已确认转折聚类压力/支撑区域并相对参考价排序。

    Args:
        candles: 已经完整收盘的OHLCV记录/表。
        pivots: 截至观察时刻已确认的高低转折点。
        reference_price: 用于区分支撑/压力两侧的当前可知价格。

    Returns:
        支持和压力区域数据；不是保证成交的精确价位。
    """
    close=float(candles.close.iloc[-1] if reference_price is None else reference_price);tr=pd.concat([candles.high-candles.low,(candles.high-candles.close.shift()).abs(),(candles.low-candles.close.shift()).abs()],axis=1).max(axis=1)
    atr=float(tr.ewm(alpha=1/14,adjust=False).mean().iloc[-1]);tolerance=max(close*.003,atr*.35)
    clusters=[]
    for point in sorted(pivots[-120:],key=lambda p:p['price']):
        if clusters and abs(point['price']-np.mean([x['price'] for x in clusters[-1]]))<=tolerance:clusters[-1].append(point)
        else:clusters.append([point])
    levels=[]
    for cluster in clusters:
        center=float(np.mean([p['price'] for p in cluster]));last=max(cluster,key=lambda p:p['confirmed_at'])
        levels.append(dict(center=center,low=min(p['price'] for p in cluster),high=max(p['price'] for p in cluster),touches=len(cluster),
            last_confirmed_at=last['confirmed_at'],distance_pct=center/close-1,evidence='多次确认转折区' if len(cluster)>1 else '单个确认转折候选'))
    supports=sorted([p for p in levels if p['center']<close],key=lambda p:-p['center'])[:3]
    resistance=sorted([p for p in levels if p['center']>close],key=lambda p:p['center'])[:3]
    return dict(support=supports,resistance=resistance,atr=atr,zone_tolerance=tolerance,reference_price=close,
        definition='最近120个已确认高低转折按max(0.3%价格,0.35ATR)合并；只列当前价格下方支撑与上方压力。')


def candle_svg(candles,technical,levels,wave,tf):
    """绘制真实蜡烛、确认趋势线、压力支撑和波浪候选SVG。

    Args:
        candles: 已经完整收盘的OHLCV记录/表。
        technical: 与行情对齐的已确认技术分析结果。
        levels: 已经计算的支撑/压力区域。
        wave: 已经计算的波浪候选与失效位置。
        tf: 当前图表/模型的周期名称。

    Returns:
        SVG字符串；不完整样本明确显示数据不足。
    """
    first=max(0,len(candles)-150);view=candles.iloc[first:];n=len(candles)
    low=min(float(view.low.min()),levels['reference_price']);high=max(float(view.high.max()),levels['reference_price']);span=max(high-low,high*.002)
    x=lambda i:70+930*(i-first)/max(1,n-1-first)
    y=lambda p:325-265*(p-low)/span
    width=max(1,930/max(1,len(view))*.55);clip='clip_'+tf
    out=[f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1160 385" aria-label="{tf} K线与趋势支撑压力"><defs><clipPath id="{clip}"><rect x="65" y="40" width="945" height="295"/></clipPath></defs>']
    for j in range(5):
        p=low+span*j/4;out.append(f'<line x1="65" x2="1010" y1="{y(p):.2f}" y2="{y(p):.2f}" stroke="#e2e8f0"/><text x="4" y="{y(p)+4:.2f}">{p:.4g}</text>')
    out.append(f'<g clip-path="url(#{clip})">')
    out.append(f'<line x1="65" x2="1010" y1="{y(levels["reference_price"]):.2f}" y2="{y(levels["reference_price"]):.2f}" stroke="#111827" stroke-dasharray="9 4" opacity=".6"/>')
    for i in range(first,n):
        r=candles.iloc[i];color='#059669' if r.close>=r.open else '#dc2626';top=min(y(r.open),y(r.close));height=max(1,abs(y(r.open)-y(r.close)))
        out.append(f'<line x1="{x(i):.2f}" x2="{x(i):.2f}" y1="{y(r.high):.2f}" y2="{y(r.low):.2f}" stroke="{color}"/><rect x="{x(i)-width/2:.2f}" y="{top:.2f}" width="{width:.2f}" height="{height:.2f}" fill="{color}"><title>{local_time(r.close_time)} O {r.open:g} H {r.high:g} L {r.low:g} C {r.close:g}</title></rect>')
    last=technical.iloc[-1]
    for kind,color in [('support','#059669'),('resistance','#dc2626')]:
        if pd.notna(last[kind]):
            a=int(last[kind+'_anchor1']);z=int(last[kind+'_anchor2']);column='low' if kind=='support' else 'high'
            slope=(candles[column].iloc[z]-candles[column].iloc[a])/(z-a);left=max(first,a);value=candles[column].iloc[a]+slope*(left-a)
            out.append(f'<line x1="{x(left):.2f}" y1="{y(value):.2f}" x2="{x(n-1):.2f}" y2="{y(last[kind]):.2f}" stroke="{color}" stroke-width="2" stroke-dasharray="7 5"/>')
        for level in levels[kind]:
            out.append(f'<line x1="65" x2="1010" y1="{y(level["center"]):.2f}" y2="{y(level["center"]):.2f}" stroke="{color}" opacity=".55" stroke-dasharray="3 5"/>')
    for i in range(first,n):
        event=int(technical.dow123_event.iloc[i])
        if event:out.append(f'<circle cx="{x(i):.2f}" cy="{y(candles.close.iloc[i]):.2f}" r="5" fill="{("#2563eb" if event>0 else "#a855f7")}"><title>123 {("向上" if event>0 else "向下")}确认 {local_time(candles.close_time.iloc[i])}</title></circle>')
    points=wave['points'];visible=[p for p in points if p['index']>=first]
    if visible:
        coords=' '.join(f'{x(p["index"]):.2f},{y(p["price"]):.2f}' for p in visible)
        out.append(f'<polyline points="{coords}" fill="none" stroke="#ea580c" stroke-width="2" opacity=".8"/>')
    for label,p in zip(wave['labels'],points):
        if p['index']>=first:out.append(f'<text x="{x(p["index"]):.2f}" y="{y(p["price"])+(18 if p["kind"]=="low" else -10):.2f}" fill="#c2410c">{label}</text>')
    out.append('</g>')
    for j in range(5):
        i=first+round((n-1-first)*j/4);date=local_time(candles.close_time.iloc[i])
        out.append(f'<text x="{x(i):.2f}" y="360" text-anchor="middle">{date[5:]}</text>')
    out.append('<text x="70" y="23">实心K线 · 绿/红虚线=确认趋势线 · 水平点线=支撑/压力区 · 蓝/紫圆点=123确认 · 橙线=波浪候选</text></svg>')
    return ''.join(out)


def _page(title,body):
    """包装多周期结构分析页面的标题、样式和正文。

    Args:
        title: 页面/图表标题，渲染时转义。
        body: 通知正文或已构建HTML正文，不应包含凭据。

    Returns:
        HTML字符串。
    """
    return '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+html.escape(title)+'</title><style>body{font:15px system-ui;background:#f3f6fb;color:#172334;margin:0}main{max-width:1320px;margin:auto;padding:25px}section,.card{background:white;border:1px solid #dce3ed;border-radius:10px;padding:18px;margin:16px 0}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px}.cards .card{margin:0}a{color:#2563eb}.notice{background:#fff7df;padding:14px;border-left:4px solid #eab308}svg{width:100%;height:auto}svg text{font-size:12px}table{width:100%;border-collapse:collapse}td,th{text-align:left;border-bottom:1px solid #ddd;padding:9px}.table{overflow:auto}.muted{color:#64748b}.value{font-size:24px;font-weight:650}</style><main><h1>'+html.escape(title)+'</h1>'+body+'</main></html>'


def analyze_market(symbol,data_dir,out,refresh=False):
    """读取独立观察行情并导出多周期结构、候选波浪和失效条件。

    Args:
        symbol: 精确产品代码；股票、ETF与相关永续不能混用。
        data_dir: 受控输入行情目录，包含来源及所需CSV。
        out: 本次输出目录；研究执行器通常要求目录尚不存在。
        refresh: 是否重建派生观察报告，不影响冻结训练/回测。

    Returns:
        该标的结构分析报告；不把新增观察行情并入冻结训练/回测。
    """
    data_dir=Path(data_dir);out=Path(out);out.mkdir(parents=True,exist_ok=refresh)
    b=load_bars(data_dir/'bars.csv');bars_hash=hashlib.sha256((data_dir/'bars.csv').read_bytes()).hexdigest();source=json.loads((data_dir/'source.json').read_text())
    if source.get('symbol')!=symbol:raise ValueError('Analysis source symbol mismatch')
    report=dict(symbol=symbol,name=NAMES.get(symbol,symbol),source=source,dataset_sha256=bars_hash,as_of=int(b.close_time.iloc[-1]),latest_price=float(b.close.iloc[-1]),timeframes={},wave_count_is_hypothesis=True,real_orders_enabled=False)
    sections=[];overview=[];structure_inputs={}
    for tf in PERIODS:
        candles=aggregate(b,tf,'binance_usdm')
        structure_inputs[tf]=candles
        if len(candles)<5:
            report['timeframes'][tf]=dict(status='INSUFFICIENT_DATA',bars=len(candles),note='不足5根完整K线，不能确认转折/123/波浪或市场周期。')
            section=f'<section id="{tf}"><h2>{tf}</h2><p>仅{len(candles)}根完整K线，样本不足，暂不建立123与波浪判断。图中黑线仅为最新15m快照参考，不是未收盘的大周期K线。</p>'
            if len(candles):
                _,technical,pivots,turns=structure_features(candles,return_details=True)
                levels=support_resistance(candles,pivots,reference_price=report['latest_price'])
                svg=candle_svg(candles,technical,levels,wave_candidate(turns),tf)
                (out/(tf+'.svg')).write_text(svg);candles.to_csv(out/(tf+'.csv'),index=False)
                section+=svg+f'<p><a href="{tf}.svg">独立K线图SVG</a> · <a href="{tf}.csv">完整原始K线CSV</a></p>'
            sections.append(section+'</section>');continue
        features,technical,pivots,turns=structure_features(candles,return_details=True)
        combined=pd.concat([candles,features.reset_index(drop=True),technical.reset_index(drop=True)],axis=1)
        combined.to_csv(out/(tf+'.csv'),index=False)
        wave=wave_candidate(turns);levels=support_resistance(candles,pivots,reference_price=report['latest_price']);cycle=spectral_cycle(candles,tf)
        close=float(candles.close.iloc[-1])
        structure=int(features.analysis_trend.iloc[-1]) if sum(p['kind']=='high' for p in pivots)>=2 and sum(p['kind']=='low' for p in pivots)>=2 else None
        ema=None
        if len(candles)>=48:
            fast=candles.close.ewm(span=12,adjust=False).mean().iloc[-1];slow=candles.close.ewm(span=48,adjust=False).mean().iloc[-1]
            ema=int(np.sign(fast/slow-1))
        events=technical.index[technical.dow123_event!=0];recent=int(events[-1]) if len(events) else None
        row=dict(status='OK' if ema is not None else 'LIMITED_CONTEXT',bars=len(candles),close=close,as_of=int(candles.close_time.iloc[-1]),
            structural_trend=structure,ema_trend=ema,levels=levels,cycle=cycle,wave=wave,
            dow123=dict(current_event=int(technical.dow123_event.iloc[-1]),historical_state=int(technical.dow123_signal.iloc[-1]),
                bullish_step=int(technical.bullish_step.iloc[-1]),bearish_step=int(technical.bearish_step.iloc[-1]),
                last_event_time=int(candles.close_time.iloc[recent]) if recent is not None else None,
                last_event_direction=int(technical.dow123_event.iloc[recent]) if recent is not None else 0),
            trend_lines={k:dict(value=float(technical[k].iloc[-1]) if pd.notna(technical[k].iloc[-1]) else None,state=technical[k+'_state'].iloc[-1]) for k in ('support','resistance')})
        if len(turns)>=2:
            a,z=turns[-2:];extent=abs(z['price']-a['price']);direction=np.sign(z['price']-a['price'])
            row['retracement_references']={str(r):float(z['price']-direction*extent*r) for r in (.382,.5,.618)}
        report['timeframes'][tf]=row
        cycle_text=f'探索性频谱周期约{cycle["period_bars"]:.1f}根 / {cycle["period_hours"]:.1f}小时；并非未来拐点预测。' if 'period_bars' in cycle else cycle['note']
        ema_text=TREND[ema] if ema is not None else '不足48根完整K线'
        event_text=(f'最近123{TREND[row["dow123"]["last_event_direction"]]}确认：{local_time(row["dow123"]["last_event_time"])}。' if recent is not None else '尚无123确认。')
        event_text+= '当前K线没有新的123事件。' if row['dow123']['current_event']==0 else '当前K线出现新123确认事件。'
        overview.append((tf,str(len(candles)),TREND[structure],ema_text,
            f'{levels["support"][0]["center"]:.6g}' if levels['support'] else '暂无确认支撑',
            f'{levels["resistance"][0]["center"]:.6g}' if levels['resistance'] else '暂无确认压力'))
        section=f'<section id="{tf}"><h2>{tf}：结构{TREND[structure]} / EMA趋势{ema_text}</h2><p>本周期最新完整收盘 {close:.6g} USDT · {local_time(row["as_of"])} · {len(candles)}根完整K线。快照15m价格 {report["latest_price"]:.6g} USDT，支撑压力按此参考价格分上下方；图中黑点线为该快照价格，并非未完成的本周期K线。</p>'
        svg=candle_svg(candles,technical,levels,wave,tf)
        (out/(tf+'.svg')).write_text(svg)
        section+=svg
        section+='<h3>支撑与压力</h3><div class="table"><table><tr><th>类型</th><th>价格区域</th><th>中心</th><th>距离现价</th><th>转折证据</th></tr>'
        for kind,label in [('support','支撑'),('resistance','压力')]:
            for level in levels[kind]:section+=f'<tr><td>{label}</td><td>{level["low"]:.6g}～{level["high"]:.6g}</td><td>{level["center"]:.6g}</td><td>{level["distance_pct"]:.2%}</td><td>{level["touches"]}个确认转折</td></tr>'
            if not levels[kind]:section+=f'<tr><td>{label}</td><td colspan="4">没有足够已确认转折，保留未知</td></tr>'
        section+='</table></div><p class="muted">'+html.escape(levels['definition'])+'</p>'
        line_states='；'.join(('上升/下降支撑线' if k=='support' else '上升/下降压力线')+' '+('已破位' if v['state']=='BROKEN' else '尚守住' if v['state']=='HOLDING' else '尚未建立') for k,v in row['trend_lines'].items())
        section+='<h3>趋势与123</h3><p>'+html.escape(line_states+'。'+event_text)+f' 待确认阶段：向上{row["dow123"]["bullish_step"]}/3，向下{row["dow123"]["bearish_step"]}/3。</p>'
        section+='<p>123规则：已确认趋势线被收盘突破 → 出现突破后的更高低点/更低高点 → 收盘突破冻结的前高/前低；新极值使未完成结构失效。圆点是历史确认，持续状态不等于新开仓信号。</p>'
        section+='<h3>周期与波浪候选</h3><p>'+html.escape(cycle_text)+'</p><p>'+html.escape(wave['interpretation'])+'</p>'
        if wave['invalidation'] is not None:section+=f'<p>当前候选计数的失效参考位置：{wave["invalidation"]:.6g} USDT。浪2不能越过浪1起点；浪3不能最短；标准推动浪的浪4不能重叠浪1。未完成的浪型不能提前宣称通过全部约束。</p>'
        if row.get('retracement_references'):section+='<p>最近确认波段的回撤参考（非保证支撑/压力）：'+'；'.join(f'{float(r):.1%} → {v:.6g}' for r,v in row['retracement_references'].items())+'。</p>'
        support=levels['support'][0]['center'] if levels['support'] else None;resistance=levels['resistance'][0]['center'] if levels['resistance'] else None
        section+='<h3>条件情景</h3><p>'+(f'向上：收盘有效突破压力区{resistance:.6g}后，等待下级周期回踩及123确认。' if resistance else '向上：暂无确认上方压力，不虚构突破价位。')+(f' 向下：若收盘跌破支撑区{support:.6g}且反抽失败，再检查下级向下123。' if support else ' 向下：暂无确认下方支撑。')+' 大周期若与小周期相反，先记为分歧；这不是已成交指令。</p>'
        section+=f'<p><a href="{tf}.csv">完整K线、趋势线与确认指标 CSV</a> · <a href="{tf}.svg">独立K线图 SVG</a></p></section>';sections.append(section)
    structural_report=multitimeframe_structure(structure_inputs,int(b.close_time.iloc[-1])+1,{tf:bars_hash for tf in structure_inputs})
    report['structural_events']=structural_report
    header='<p><a href="../../analysis/index.html">四标的分析总览</a> · <a href="../../index.html">回测总体报告</a></p>'
    header+=f'<p class="notice">数据截至{local_time(report["as_of"])}（上海时间），为最近90天公开合约K线快照，不是实时行情。支撑压力为区域，波浪为候选，周期为历史描述。</p>'
    if source.get('analysis_only'):header+='<p class="notice">本页观察行情已更新到最新完整K线；模型回测仍保留统一的原90天区间。新增观察K线没有用于本轮拟合或补算收益，不能把本页较新价位当成历史开仓时已知的信息。</p>'
    if symbol=='KORUUSDT':header+='<p class="notice">KORU对应每日3倍做多韩国ETF，其每日杠杆与合约额外杠杆是不同层次；不把指数涨跌简单乘3视为多日产品收益。本轮数据从7月19日起，位于7月15日合约拆股调整之后。</p>'
    header+='<p>周期导航：'+' · '.join(f'<a href="#{tf}">{tf}</a>' for tf in PERIODS)+'</p>'
    header+='<section><h2>多周期一致性与关键位置</h2><div class="table"><table><tr><th>周期</th><th>完整K线</th><th>确认高低点结构</th><th>EMA12/48趋势</th><th>最近支撑中心</th><th>最近压力中心</th></tr>'
    header+=''.join('<tr>'+''.join('<td>'+html.escape(v)+'</td>' for v in row)+'</tr>' for row in overview)+'</table></div>'
    major=[(tf,report['timeframes'][tf]) for tf in ('1h','4h','1d') if report['timeframes'].get(tf,{}).get('status') in ('OK','LIMITED_CONTEXT')]
    header+='<p>重点周期：'+'；'.join(tf+'结构'+TREND[r['structural_trend']] for tf,r in major)+'。多周期出现分歧时，不把小周期反弹自动解释为大趋势反转。</p></section>'
    event_rows=[]
    for event in structural_report['events'][-16:]:
        label={'TRENDLINE_BREAK':'趋势线收盘破坏','DOW123_CONFIRMED':'道氏123确认','BULLISH_RSI_DIVERGENCE':'价格-RSI底背离','BEARISH_RSI_DIVERGENCE':'价格-RSI顶背离'}.get(event['kind'],event['kind'])
        event_rows.append('<tr><td>'+html.escape(event['timeframe'])+'</td><td>'+html.escape(label)+'</td><td>'+local_time(event['occurred_at'])+'</td><td>'+local_time(event['confirmed_at'])+'</td><td>'+html.escape(json.dumps(event['evidence'],ensure_ascii=False,allow_nan=False))+'</td></tr>')
    event_section='<section><h2>多周期结构事件与背离</h2><p>事件按周期收盘确认时间排序；pivot原点只作回看位置，不表示该处当时已知。每周期使用该周期收盘数据，不将未收盘高周期信息提前泄露。</p><div class="table"><table><tr><th>周期</th><th>事件</th><th>结构发生</th><th>可知确认</th><th>可复核证据</th></tr>'+(''.join(event_rows) if event_rows else '<tr><td colspan="5">当前区间没有已确认的结构破坏、123或RSI背离事件。</td></tr>')+'</table></div></section>'
    footer='<section><h2>方法与来源</h2><p>确认转折需等待右侧两根完整K线，指标与训练均在确认后可见；图上标注原转折位置不代表当时就能提前知道。RSI背离同时要求价格形成已确认的更高高点/更低低点及RSI相反方向；其确认时间为第二个pivot右侧样本收盘。候选波浪合并连续同类极值，相邻反向转折至少有0.5%价差；计数会随新的确认转折更新。未完成周/月线不参与。月线不足5根时直接报告样本不足。</p>'
    footer+='<p>'+' · '.join(f'<a href="{url}">{html.escape(label)}</a>' for label,url in SOURCES)+'</p><p><a href="report.json">结构分析JSON</a></p></section>'
    (out/'index.html').write_text(_page(symbol+' · '+report['name']+' · 多周期K线分析',header+event_section+''.join(sections)+footer))
    (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False))
    return report


def export_analysis_overview(batch,records):
    """为多个标的的独立结构报告生成统一入口页。

    Args:
        batch: 本次研究批次目录。
        records: 已保存的标的运行状态记录序列。

    Returns:
        无返回值；仅链接已经生成的分析结果。
    """
    batch=Path(batch);folder=batch/'analysis';folder.mkdir(exist_ok=True)
    body='<p><a href="../index.html">回测与模型总体报告</a></p><p>优先按KORU、原油、英伟达、BTC顺序观察；各周期只使用已完整收盘的K线，候选波浪与历史周期不作为收益保证。观察行情更新与回测评估分开：本页日期较新时，新增K线不计入本轮模型训练和收益。</p><div class="cards">'
    for record in records:
        symbol=record['symbol'];path=folder/symbol/'report.json'
        if not path.exists():continue
        r=json.loads(path.read_text());tf=r['timeframes'].get('4h',{});levels=tf.get('levels',{})
        support=levels.get('support',[]);resistance=levels.get('resistance',[])
        body+=f'<div class="card"><h2><a href="{symbol}/index.html">{symbol}</a></h2><p>{html.escape(r["name"])}</p><div class="value">{r.get("latest_price",tf.get("close",0)):.6g} USDT</div><p>4h结构：{TREND.get(tf.get("structural_trend",0))}</p><p>4h最近支撑：'+(f'{support[0]["center"]:.6g}' if support else '尚未确认')+'</p><p>4h最近压力：'+(f'{resistance[0]["center"]:.6g}' if resistance else '尚未确认')+f'</p><p>截至 {local_time(r["as_of"])}</p><p><a href="{symbol}/index.html">8周期K线、趋势线、支撑压力、波浪与123</a></p></div>'
    body+='</div><section><h2>两个新增模型</h2><p>道氏123：下级已确认123事件 + 四倍大周期同向 + 高低点结构不反向 + 历史波动范围筛选。波浪是辅助结构解释，不强行标成信号。</p><p>PyTorch GRU：24根连续已收盘K线输入，双层32单元GRU；训练集独立标准化、固定20轮，读取形态与趋势/123/波浪候选/周期等特征，只用过去上下文。样本外概率再结合大周期和结构过滤。</p></section>'
    (folder/'index.html').write_text(_page('优先四标的 · 多周期结构分析',body))
