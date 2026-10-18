"""Read-only research aggregation: batch -> model -> instrument operations.

Every plotted line is an independent account.  This module never pools returns,
fits a model, submits an order, copies model binaries, or sends a notification.
"""
import copy
import hashlib
import html
import json
from pathlib import Path
import re
import shutil

import numpy as np
import pandas as pd

from quant_lab.reports.operation_report import export_operations_report


REPORT_VERSION = 2
MODEL_LABELS = {
    'torch_gru':'PyTorch双层GRU时序网络','analysis_dow123':'多周期结构 · 道氏123',
    'pattern_only': '反转形态规则', 'logistic': '逻辑回归',
    'logistic_regression': '逻辑回归', 'random_forest': '随机森林',
    'extra_trees': '极端随机树', 'hist_gradient': '直方图梯度提升',
    'hist_gradient_boosting': '直方图梯度提升', 'mlp': '多层感知机',
}
INPUTS = ('report.json', 'validation_curves.json', 'trades.csv', 'events.csv')
HOLDOUT_FILES = ('selected_holdout_trades.csv', 'selected_holdout_equity.csv',
                 'selected_holdout_events.csv')


def _safe_name(value):
    """限制派生报告目录名字符，防止相对路径跳出目标目录。

    Args:
        value: 待转换或校验的当前值，具体口径见函数用途。

    Returns:
        校验后的名称；非法名称抛出ValueError。
    """
    value = str(value)
    if not re.fullmatch(r'[A-Za-z0-9_-]+', value):
        raise ValueError('Unsafe report directory name: '+value)
    return value


def _json(path, value):
    """以UTF-8写入不允许NaN的标准JSON。

    Args:
        path: 输入/输出文件路径；具体读写方向见函数用途。
        value: 待转换或校验的当前值，具体口径见函数用途。

    Returns:
        无返回值；非JSON数值或写入失败会抛出异常。
    """
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2,
                               allow_nan=False), encoding='utf-8')


def _csv(path):
    """读取账本CSV，并把没有内容的文件视为空表。

    Args:
        path: 输入/输出文件路径；具体读写方向见函数用途。

    Returns:
        DataFrame；其他读取错误保留给调用方。
    """
    try:
        return pd.read_csv(path,low_memory=False)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def _fingerprint(root):
    """计算渲染器及原始报告/账本的联合指纹，判断派生页面是否过期。

    Args:
        root: 当前任务的项目/数据根目录路径；不会自动读取其他目录。

    Returns:
        SHA256字符串；不会改写原始输入。
    """
    digest = hashlib.sha256(str(REPORT_VERSION).encode())
    # Rebuild derived pages when either renderer changes, even if ledgers did not.
    for renderer in (Path(__file__),Path(__file__).with_name('operation_report.py')):
        digest.update(renderer.read_bytes())
    for name in INPUTS+HOLDOUT_FILES:
        digest.update(name.encode())
        path = root/name
        if path.exists():
            with path.open('rb') as stream:
                for block in iter(lambda: stream.read(1 << 20), b''):
                    digest.update(block)
    return digest.hexdigest()


def _economics(root, symbol):
    """逐笔拆开标的价格收益、保证金收益和账户收益三种口径。

    Args:
        root: 当前任务的项目/数据根目录路径；不会自动读取其他目录。
        symbol: 精确产品代码；股票、ETF与相关永续不能混用。

    Returns:
        写入收益口径CSV后的逐笔记录/统计，保持账户相互独立。

    Notes:
        原有细则保留如下：
        Make the three different return denominators explicit for every trade.
    """
    trades = _csv(root/'trades.csv')
    rows = []
    for trade in trades.to_dict('records'):
        entry, exit_ = float(trade['entry_price']), float(trade['exit_price'])
        direction = int(trade['direction'])
        net = float(trade['net_pnl'])
        before, margin = float(trade['account_equity_before']), float(trade['allocated_margin'])
        notional = float(trade.get('entry_notional', 0))
        factor = float(trade.get('fold_start_equity', 1))
        def optional_number(key):
            """从旧版交易记录读取可选数值，缺失或NaN时保留空值。

            Args:
                key: 稳定通知/账户配置标识，或当前记录的可选字段名。

            Returns:
                浮点数或None。
            """
            value = trade.get(key)
            return float(value) if value is not None and pd.notna(value) else None
        raw_entry, raw_exit = optional_number('entry_raw_price'), optional_number('exit_raw_price')
        rows.append(dict(
            config=trade['config'], symbol=symbol, fold=trade.get('fold', 0),
            position_id=trade.get('position_id'), direction=direction,
            direction_label='做多' if direction == 1 else '做空',
            entry_time=trade['entry_time'], exit_time=trade['exit_time'],
            holding_hours=(float(trade['exit_time'])-float(trade['entry_time']))/3_600_000,
            entry_price=entry, final_exit_price=exit_,
            signal_price=optional_number('signal_price'),
            entry_raw_price=raw_entry, exit_raw_price=raw_exit,
            price_basis='simulated fills including slippage; final exit is not partial-exit weighted',
            underlying_price_return=exit_/entry-1,
            raw_underlying_price_return=raw_exit/raw_entry-1 if raw_entry and raw_exit else None,
            direction_adjusted_price_return=direction*(exit_/entry-1),
            realized_price_return=float(trade['gross_pnl'])/notional if notional > 0 else None,
            directional_price_return=optional_number('directional_price_return') if 'directional_price_return' in trade else (float(trade['gross_pnl'])/notional if notional > 0 else None),
            realized_price_denominator_entry_notional=notional,
            net_pnl_fold_equity_unit=net,
            margin_return=net/margin if margin > 0 else None,
            margin_return_denominator_allocated_margin=margin,
            account_return=net/before if before > 0 else None,
            account_return_denominator_equity_before=before,
            initial_account_return_contribution=net*factor,
            fold_start_equity=factor,
            partial_exit_count=int(trade.get('partial_exit_count', 0)),
            exit_reason=trade['exit_reason']))
    columns = ['config', 'symbol', 'fold', 'position_id', 'direction', 'direction_label',
               'entry_time', 'exit_time', 'holding_hours', 'entry_price', 'final_exit_price',
               'signal_price', 'entry_raw_price', 'exit_raw_price',
               'price_basis', 'underlying_price_return', 'raw_underlying_price_return',
               'direction_adjusted_price_return', 'realized_price_return',
               'directional_price_return', 'realized_price_denominator_entry_notional',
               'net_pnl_fold_equity_unit', 'margin_return',
               'margin_return_denominator_allocated_margin', 'account_return',
               'account_return_denominator_equity_before',
               'initial_account_return_contribution', 'fold_start_equity',
               'partial_exit_count', 'exit_reason']
    pd.DataFrame(rows, columns=columns).to_csv(root/'trade_economics.csv', index=False, encoding='utf-8-sig')
    return rows


def _scoped_run(source, target, report, model, fingerprint):
    """为单一模型筛选全部周期/止盈账本并生成独立标的报告。

    Args:
        source: 已知来源元数据，或对齐函数中的源K线表。
        target: 报告目标目录或聚合目标周期；具体用途由函数名说明。
        report: 已经计算并保存的研究报告字典。
        model: 已拟合估计器；None表示只用规则，不在推断阶段拟合。
        fingerprint: 来源报告与渲染器联合SHA256指纹。

    Returns:
        该模型标的的操作汇总；留出结果仅复制给原选定模型。
    """
    target.mkdir(parents=True, exist_ok=True)
    manifest = target/'scope_manifest.json'
    if manifest.exists() and (target/'operation_summary.json').exists() and (target/'operations.html').exists():
        prior = json.loads(manifest.read_text())
        if prior.get('source_sha256') == fingerprint and prior.get('model') == model:
            return json.loads((target/'operation_summary.json').read_text())
    scoped = copy.deepcopy(report)
    ranking = [row for row in report['ranking'] if row['model'] == model]
    keys = {row['config'] for row in ranking}
    scoped['ranking'] = ranking
    scoped['scope'] = dict(model=model, symbol=report['source']['symbol'],
                           independent_accounts=True)
    scoped['original_run_status'] = report.get('status')
    periods = {row['timeframe'] for row in ranking}
    exits = {row['exit_mode'] for row in ranking}
    scoped['period_pairs'] = {key: value for key, value in report.get('period_pairs', {}).items() if key in periods}
    scoped['exit_modes'] = {key: value for key, value in report.get('exit_modes', {}).items() if key in exits}
    selected = report.get('selected')
    owns_holdout = isinstance(selected, dict) and selected.get('config') in keys
    if not owns_holdout:
        scoped['selected'] = None
        scoped.pop('holdout', None)
        scoped.pop('holdout_double_cost', None)
        scoped['holdout_used'] = False
        if isinstance(selected, dict):
            scoped['status'] = 'NOT_SELECTED'
    if model!='torch_gru':scoped.pop('deep_training',None)
    scoped.pop('simulated_mail_count', None)
    scoped['actual_email_sent'] = False
    scoped['real_orders_enabled'] = False
    _json(target/'report.json', scoped)
    curves = json.loads((source/'validation_curves.json').read_text())
    _json(target/'validation_curves.json', {key: value for key, value in curves.items() if key in keys})
    for name in ('trades.csv', 'events.csv'):
        if (source/name).exists():
            frame = _csv(source/name)
            if len(frame):
                if 'config' not in frame:
                    raise ValueError(name+' has no config column')
                frame = frame[frame.config.isin(keys)]
            frame.to_csv(target/name, index=False)
        elif (target/name).exists():
            (target/name).unlink()
    pd.DataFrame(ranking).to_csv(target/'ranking.csv', index=False)
    for name in HOLDOUT_FILES:
        if owns_holdout and (source/name).exists():
            shutil.copyfile(source/name, target/name)
        elif (target/name).exists():
            (target/name).unlink()
    summary = export_operations_report(target)
    _economics(target, summary['symbol'])
    _write_economics_page(target, summary)
    # The existing operations page links to comparison.html; keep this link local.
    (target/'comparison.html').write_text(_page('模型与标的账户',
        '<p><a href="../../index.html">返回本模型汇总</a> · '
        '<a href="operations.html">全部周期、止盈方式和逐笔操作</a> · '
        '<a href="trade_economics.html">标的波段 / 保证金 / 账户收益解释</a></p>'
        +_ranking_table(summary['accounts'].values())), encoding='utf-8')
    # Add navigation without changing operation_report's reusable renderer.
    page = (target/'operations.html').read_text()
    analysis=target.parents[3]/'analysis'/summary['symbol']/'index.html'
    if analysis.exists():
        page=page.replace('<main>','<main><p><a href="../../../../analysis/'+summary['symbol']+'/index.html">本标的多周期K线、趋势、支撑压力与波浪分析</a></p>',1)
    if model=='torch_gru' and (source/'deep_training.html').exists():
        relative=Path('../../../../')/source.relative_to(target.parents[3])/'deep_training.html' if source.is_relative_to(target.parents[3]) else None
        if relative:page=page.replace('<main>','<main><p><a href="'+str(relative)+'">实际GPU训练记录、损失曲线与检查点</a></p>',1)
    page = page.replace('<main>', '<main><p><a href="../../index.html">返回本模型报告</a> · '
        '<a href="../../../../index.html">总体报告</a> · '
        '<a href="trade_economics.html">每笔价格波段、保证金及账户收益</a></p>', 1)
    (target/'operations.html').write_text(page, encoding='utf-8')
    _json(manifest, dict(source_sha256=fingerprint, model=model, report_version=REPORT_VERSION))
    return summary


def _page(title, body):
    """包装转义后的标题与已构建HTML正文为离线报告页面。

    Args:
        title: 页面/图表标题，渲染时转义。
        body: 通知正文或已构建HTML正文，不应包含凭据。

    Returns:
        完整HTML字符串。
    """
    return ('<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>'+html.escape(title)+'</title><style>'
        'body{margin:0;background:#f3f6fb;color:#172334;font:15px system-ui,sans-serif}'
        'main{max-width:1280px;margin:auto;padding:26px}h1{font-size:29px}'
        'section,.card{background:white;border:1px solid #dce3ed;border-radius:12px;padding:18px;margin:16px 0}'
        '.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:14px}'
        '.cards .card{margin:0}.value{font-size:25px;font-weight:650}a{color:#1d4ed8}'
        '.muted{color:#64748b}.notice{background:#fff7df;padding:14px;border-left:4px solid #dfac23}'
        'select{font:inherit;max-width:100%;padding:10px}table{width:100%;border-collapse:collapse}'
        'td,th{text-align:left;padding:10px;border-bottom:1px solid #e2e8f0;white-space:nowrap}'
        '.table-wrap{overflow:auto;max-height:650px}svg{width:100%;height:auto}'
        '</style><main><h1>'+html.escape(title)+'</h1>'+body+'</main></html>')


def _ranking_table(accounts):
    """把独立账户的收益、回撤、操作和持仓指标列成排名表。

    Args:
        accounts: 互相独立的账户摘要列表，不共享本金。

    Returns:
        HTML表格字符串；不把独立账户相加为组合。
    """
    body = []
    for account in accounts:
        body.append('<tr>'+''.join('<td>'+html.escape(str(value))+'</td>' for value in (
            account['config'], account['symbol'], account['round_trips'],
            f'{account["net_return"]:.2%}', f'{account["max_drawdown"]:.2%}',
            f'{account["holding_hours_mean"]:.1f}小时',
            '验证合格' if account['eligible'] else '验证未合格'))+'</tr>')
    return ('<div class="table-wrap"><table><thead><tr><th>独立账户配置</th><th>标的</th>'
        '<th>交易数</th><th>验证账户收益</th><th>回撤</th><th>平均持仓</th><th>验证筛选</th>'
        '</tr></thead><tbody>'+''.join(body)+'</tbody></table></div>')


def _write_economics_page(target, summary):
    """生成解释价格、保证金和账户收益口径的逐笔页面。

    Args:
        target: 报告目标目录或聚合目标周期；具体用途由函数名说明。
        summary: 已经计算的操作/账户汇总字典。

    Returns:
        无返回值；写入收益口径页面。
    """
    intro = ('<p><a href="operations.html">返回逐笔操作与曲线</a> · '
        '<a href="trade_economics.csv">下载每笔收益口径 CSV</a></p>'
        '<p>标的价格波段与账户收益使用不同的分母。标的本身上涨2%，不代表账户上涨2%。</p>'
        '<ul><li>标的价格涨跌 = 最终平仓价 / 开仓价 − 1；做空的方向调整波段取相反数。'
        '这里使用包含模拟滑点的成交价。发生减仓时，该字段仅表示开仓到最终平仓的变化。'
        '新回测另列原始行情价格 entry_raw_price / exit_raw_price 与不含滑点的 raw_underlying_price_return；旧报告无此字段时留空。</li>'
        '<li>数量加权已实现价格收益 = 完整交易毛损益 / 初始开仓名义金额，包含各次减仓价格。</li>'
        '<li>保证金收益 = 扣除手续费及资金费后的完整交易净损益 / 本笔初始分配保证金。</li>'
        '<li>本笔账户收益 = 同一完整交易净损益 / 开仓前账户权益。</li>'
        '<li>初始账户收益贡献 = 本笔净损益 × 该验证折起始净值。它可与同一配置其他交易贡献相加，'
        '用于核对该配置曲线。不同配置的收益不可相加。</li></ul>'
        '<p>CSV 中每个分母均单独列出。这里显示验证期交易；预留测试交易若存在，在操作页单独下载。</p>')
    rows = _csv(target/'trade_economics.csv')
    parts = []
    for row in rows.head(1000).to_dict('records'):
        percent = lambda value: '—' if pd.isna(value) else f'{float(value):.2%}'
        values = (row['config'], row['direction_label'], f'{row["holding_hours"]:.1f}小时',
                  percent(row['underlying_price_return']), percent(row['direction_adjusted_price_return']),
                  percent(row['realized_price_return']), percent(row['margin_return']), percent(row['account_return']))
        parts.append('<tr>'+''.join('<td>'+html.escape(str(value))+'</td>' for value in values)+'</tr>')
    intro += f'<p>标的 {html.escape(summary["symbol"])}，共{len(rows)}笔独立实验交易；网页最多列1000笔，CSV完整保留。</p>'
    intro += ('<div class="table-wrap"><table><thead><tr><th>配置</th><th>方向</th><th>持仓</th>'
        '<th>标的价格涨跌</th><th>方向调整波段</th><th>数量加权波段</th><th>保证金收益</th>'
        '<th>本笔账户收益</th></tr></thead><tbody>'+''.join(parts)+'</tbody></table></div>')
    (target/'trade_economics.html').write_text(_page('逐笔价格波段与收益分母', intro), encoding='utf-8')


def _sample_curve(curve):
    """对展示曲线做轻量抽样以控制离线页面大小。

    Args:
        curve: 一个独立账户的已有权益/回撤序列。

    Returns:
        用于绘图的抽样点；不参与重新计算收益。
    """
    points = sorted((int(time), float(value)) for time, value in curve.items())
    keep = np.unique(np.linspace(0, len(points)-1, min(250, len(points)), dtype=int))
    return [points[index] for index in keep]


def _interactive(accounts, representatives):
    """构建代表账户叠图和全部独立账户选择器。

    Args:
        accounts: 互相独立的账户摘要列表，不共享本金。
        representatives: 每个标的/模型的原排名代表账户映射。

    Returns:
        可离线交互的HTML/脚本片段。

    Notes:
        原有细则保留如下：
        An overlay for representative accounts, and a selector for every account.
    """
    payload = json.dumps(dict(accounts=accounts, representatives=representatives),
                         ensure_ascii=False, allow_nan=False).replace('<', '\\u003c')
    return '''<section><h2>各模型独立账户曲线对比（验证期）</h2>
<p class="muted">每条线是一个模型在当前标的上的默认观察配置。优先展示原验证候选；其余为该模型原报告排名首位。图例明确列出配置；此图用于比较，未构造组合账户。</p>
<label>标的 <select id="overlay-symbol"></select></label><svg id="overlay" viewBox="0 0 1100 350" aria-label="模型独立账户收益对比"></svg><div id="legend"></div></section>
<section><h2>查看全部周期和止盈配置</h2><select id="account-select"></select>
<div id="account-stats" class="cards"></div><svg id="single-curve" viewBox="0 0 1100 350" aria-label="当前独立账户收益曲线"></svg><p id="account-link"></p>
<p class="muted">纵轴为扣除模拟手续费、滑点、资金费后的账户累计收益，初始账户净值为1。所有时间为上海时间。曲线显示至多250点，标的操作页可下载完整净值。</p></section>
<script id="batch-data" type="application/json">'''+payload+'''</script><script>
const data=JSON.parse(document.getElementById('batch-data').textContent), accounts=data.accounts;
const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const percent=v=>(v*100).toFixed(2)+'%', colors=['#2563eb','#ea580c','#059669','#9333ea','#db2777','#0891b2','#65a30d','#64748b'];
const date=t=>new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit'}).format(new Date(t));
function draw(id,lines){const points=lines.flatMap(a=>a.curve),el=document.getElementById(id);if(!points.length){el.innerHTML='';return;}
 const start=Math.min(...lines.map(a=>a.curve[0][0])),end=Math.max(...lines.map(a=>a.curve.at(-1)[0]));
 let low=Math.min(0,...points.map(p=>(p[1]-1)*100)),high=Math.max(0,...points.map(p=>(p[1]-1)*100)),span=high-low;
 if(span<.01){low-=.05;high+=.05;}else{low-=span*.08;high+=span*.08;}span=high-low;
 const x=t=>70+970*(t-start)/Math.max(1,end-start),y=v=>290-245*(v-low)/span;let body='';
 for(let i=0;i<=4;i++){const v=low+span*i/4,t=start+(end-start)*i/4;body+=`<line x1="70" x2="1040" y1="${y(v)}" y2="${y(v)}" stroke="#e2e8f0"/><text x="5" y="${y(v)+4}" fill="#64748b" font-size="12">${v.toFixed(2)}%</text><text x="${x(t)}" y="325" text-anchor="middle" fill="#64748b" font-size="12">${date(t)}</text>`;}
 body+=`<line x1="70" x2="1040" y1="${y(0)}" y2="${y(0)}" stroke="#94a3b8" stroke-dasharray="4 4"/>`;
 lines.forEach((a,i)=>{body+=`<polyline points="${a.curve.map(p=>x(p[0]).toFixed(1)+','+y((p[1]-1)*100).toFixed(1)).join(' ')}" stroke="${colors[i%colors.length]}" stroke-width="2" fill="none"><title>${esc(a.symbol+' '+a.config)}</title></polyline>`;});el.innerHTML=body;}
const symbols=[...new Set(accounts.map(a=>a.symbol))],os=document.getElementById('overlay-symbol');os.innerHTML=symbols.map(s=>`<option>${esc(s)}</option>`).join('');
function overlay(){const lines=data.representatives.map(i=>accounts[i]).filter(a=>a.symbol===os.value);draw('overlay',lines);document.getElementById('legend').innerHTML=lines.map((a,i)=>`<p><span style="color:${colors[i%colors.length]}">●</span> ${esc(a.config)} · ${percent(a.net_return)} · <a href="${esc(a.link)}">${esc(a.symbol)} 操作</a></p>`).join('');}os.addEventListener('change',overlay);overlay();
const select=document.getElementById('account-select');select.innerHTML=accounts.map((a,i)=>`<option value="${i}">${esc(a.symbol+' | '+a.config)} · ${percent(a.net_return)} · ${a.round_trips}笔</option>`).join('');
function render(){const a=accounts[Number(select.value)];if(!a)return;draw('single-curve',[a]);document.getElementById('account-stats').innerHTML=[['账户累计收益',percent(a.net_return)],['完整交易',a.round_trips+'笔'],['平均 / 最长持仓',a.holding_hours_mean.toFixed(1)+' / '+a.holding_hours_max.toFixed(1)+'小时'],['压力回撤近似',percent(a.max_drawdown)]].map(([label,value])=>`<div class="card">${label}<div class="value">${esc(value)}</div></div>`).join('');document.getElementById('account-link').innerHTML=`<a href="${esc(a.link)}">查看此模型下 ${esc(a.symbol)} 的全部操作、持仓时间和其他配置</a> · <a href="${esc(a.economics_link)}">标的价格波段、保证金与账户收益</a>`;}
select.addEventListener('change',render);render();</script>'''


def export_batch_report(batch, records):
    """从冻结研究产物生成总体、模型、标的三级报告树。

    Args:
        batch: 本次研究批次目录。
        records: 已保存的标的运行状态记录序列。

    Returns:
        汇总信息；不训练、合并账户或复制模型二进制。

    Notes:
        原有细则保留如下：
        Export one portable report tree without modifying any source run.

        ``records`` contains symbol/status plus ``run_dir`` relative to ``batch``.
        Legacy records containing a relative ``report`` HTML path are also accepted.
        Waiting/failed jobs can omit their source directory and remain in the overview.
    """
    batch = Path(batch).resolve()
    batch.mkdir(parents=True, exist_ok=True)
    model_scopes, all_accounts, representatives, sources = {}, [], [], []
    seen = set()
    for record in records:
        run_dir = record.get('run_dir')
        if not run_dir and record.get('report'):
            run_dir = str(Path(record['report']).parent)
        if not run_dir:
            continue
        source = Path(run_dir)
        if not source.is_absolute():
            source = batch/source
        source = source.resolve()
        if not (source/'report.json').exists():
            continue
        report = json.loads((source/'report.json').read_text())
        symbol = _safe_name(report['source']['symbol'])
        if record.get('symbol') and record['symbol'] != symbol:
            raise ValueError('Record symbol does not match source report')
        if symbol in seen:
            raise ValueError('Duplicate source report symbol: '+symbol)
        seen.add(symbol)
        fingerprint = _fingerprint(source)
        models = list(dict.fromkeys(row['model'] for row in report['ranking']))
        sources.append(dict(symbol=symbol, status=report.get('status'), source_dir=str(source),
                            configurations=len(report['ranking']), source=report.get('source', {}),
                            holding_policy=report.get('holding_policy', {}),
                            movement_policy=report.get('movement_policy', report.get('swing_policy', {})),
                            validation_windows=report.get('validation_windows', []),
                            selected=report.get('selected'), holdout=report.get('holdout')))
        for model in models:
            model = _safe_name(model)
            target = batch/'models'/model/'symbols'/symbol
            # Reject a source nested in a generated target to avoid overwriting it.
            if source == target.resolve() or target.resolve() in source.parents:
                raise ValueError('Source run must not be a generated scoped report')
            summary = _scoped_run(source, target, report, model, fingerprint)
            curves = json.loads((target/'validation_curves.json').read_text())
            scope = dict(symbol=symbol, model=model, summary=summary,
                         path=f'models/{model}/symbols/{symbol}/operations.html')
            model_scopes.setdefault(model, []).append(scope)
            for key, account in summary['accounts'].items():
                account = dict(account)
                account['curve'] = _sample_curve(curves[key])
                account['link'] = scope['path']
                account['economics_link'] = f'models/{model}/symbols/{symbol}/trade_economics.html'
                if key == summary['default_config']:
                    representatives.append(len(all_accounts))
                all_accounts.append(account)
    for model, scopes in model_scopes.items():
        title = MODEL_LABELS.get(model, model)+' · '+model
        body = '<p><a href="../../index.html">返回总体报告</a></p>'+_independence_notice()
        body += ''.join(_source_policy(source) for source in sources if source['symbol'] in {scope['symbol'] for scope in scopes})
        body += '<div class="cards">'+''.join(_scope_card(scope, prefix='symbols/'+scope['symbol']+'/') for scope in scopes)+'</div>'
        model_accounts, model_reps = [], []
        for index, account in enumerate(all_accounts):
            if account['model'] != model:
                continue
            value = dict(account)
            value['link'] = f'symbols/{account["symbol"]}/operations.html'
            value['economics_link'] = f'symbols/{account["symbol"]}/trade_economics.html'
            if index in representatives:
                model_reps.append(len(model_accounts))
            model_accounts.append(value)
        body += _interactive(model_accounts, model_reps)
        (batch/'models'/model/'index.html').write_text(_page(title, body), encoding='utf-8')
    body = '<p>一轮研究的总体报告 → 每个模型报告 → 分标的操作与持仓明细。</p>'+_independence_notice()
    if (batch/'analysis/index.html').exists():
        body='<section><h2><a href="analysis/index.html">优先四标的：多周期K线、趋势、周期、支撑压力与波浪分析</a></h2><p>包含分析型道氏123与PyTorch GRU；点击标的查看8周期图表、候选计数和失效条件。</p></section>'+body
    body += '<div class="cards">'
    for label, value in [('已完成标的', len(sources)), ('模型类型', len(model_scopes)),
                         ('独立回测配置', len(all_accounts)),
                         ('全部实验完整交易', sum(a['round_trips'] for a in all_accounts))]:
        body += f'<div class="card">{label}<div class="value">{value}</div></div>'
    body += '</div><section><h2>本次运行与数据范围</h2>'
    for record in records:
        body += '<p>'+html.escape(str(record.get('symbol', ''))+'：'+str(record.get('status', 'UNKNOWN')))
        if record.get('error'):
            body += ' · '+html.escape(str(record['error']))
        body += '</p>'
    for source in sources:
        data = source['source']
        def date(value):
            """把汇总报告的毫秒时刻转换为上海时间展示。

            Args:
                value: 待转换或校验的当前值，具体口径见函数用途。

            Returns:
                面向用户的日期字符串。
            """
            return pd.to_datetime(value, unit='ms', utc=True).tz_convert('Asia/Shanghai').strftime('%Y-%m-%d %H:%M') if value is not None else '见原始报告'
        body += '<p>'+html.escape(source['symbol']+' 行情：'+date(data.get('start'))+' 至 '+date(data.get('end'))+'（上海时间）')+'</p>'
        body += _source_policy(source)
        if source.get('holdout'):
            body += ('<p class="notice">'+html.escape(source['symbol'])+' 的唯一验证候选 '
                +html.escape(source['selected']['config'])+'，预留测试净收益 '
                +f'{float(source["holdout"]["net_return"]):.2%}；最终状态 '
                +html.escape(str(source['status']))+'。预留测试只保存在所属模型的标的报告中。</p>')
    body += '</section><h2>每个模型的报告</h2><div class="cards">'
    for model, scopes in model_scopes.items():
        body += '<div class="card"><h3><a href="models/'+model+'/index.html">'+html.escape(MODEL_LABELS.get(model, model)+' · '+model)+'</a></h3>'
        for scope in scopes:
            account = scope['summary']['accounts'][scope['summary']['default_config']]
            body += '<p><a href="'+scope['path']+'">'+html.escape(scope['symbol'])+' 逐笔操作</a> · 默认观察 '+f'{account["net_return"]:.2%}'+'<br><small>'+html.escape(account['config'])+'</small></p>'
        body += '</div>'
    body += '</div>'
    if all_accounts:
        body += _interactive(all_accounts, representatives)
    body += '<p><a href="batch_summary.json">总体报告数据 JSON</a> · <a href="accounts_summary.csv">全部独立账户统计 CSV</a></p>'
    (batch/'index.html').write_text(_page('量化回测总体报告', body), encoding='utf-8')
    # CSV deliberately contains no pooled return or pooled holding duration.
    pd.DataFrame([{key: value for key, value in account.items() if key not in ('curve',)}
                  for account in all_accounts]).to_csv(batch/'accounts_summary.csv', index=False, encoding='utf-8-sig')
    result = dict(index='index.html', report_version=REPORT_VERSION, independent_accounts=True,
        models={model: f'models/{model}/index.html' for model in model_scopes},
        symbols=[source['symbol'] for source in sources], configurations=len(all_accounts),
        sources=sources, real_orders_enabled=False, actual_email_sent=False)
    _json(batch/'batch_summary.json', result)
    _json(batch/'run_status.json', records)
    return result


def _independence_notice():
    """说明曲线代表不同独立账户而非资金共享组合。

    Returns:
        HTML说明片段。
    """
    return ('<p class="notice">每个「模型 × 周期 × 止盈方式 × 标的」对应独立模拟账户。'
        '总体报告不将不同策略的收益拼接或相加。默认观察账户依据原验证报告，'
        '不能用预留测试重新挑选策略。交易次数是全部实验次数，不能视为单个账户的频率。'
        '曲线属于验证期；预留测试结果单独显示。邮件仅模拟记录。</p>')


def _source_policy(source):
    """提取来源报告中的持仓、价格波幅和风险假设供展示。

    Args:
        source: 已知来源元数据，或对齐函数中的源K线表。

    Returns:
        面向页面的策略说明。
    """
    holding, movement = source.get('holding_policy', {}), source.get('movement_policy', {})
    parts = []
    if holding:
        minimum, maximum = holding.get('min_holding_hours'), holding.get('max_holding_hours')
        if minimum is not None and maximum is not None:
            parts.append(f'持仓目标 {float(minimum):g}小时～{float(maximum)/24:g}天；风险退出及研究期末截尾可提前发生。')
            windows = source.get('validation_windows', [])
            durations = [(float(end)-float(start))/86_400_000 for start, end in windows]
            if durations and float(maximum)/24 > max(durations):
                parts.append(f'本轮单折验证窗口约{min(durations):.1f}～{max(durations):.1f}天，短于持仓上限；折末强制平仓会截断长持仓，不能据此确认可完整持有{float(maximum)/24:g}天。')
    if movement:
        low, high = movement.get('min_range'), movement.get('max_range')
        lookback = movement.get('lookback_hours', 168)
        if low is not None and high is not None:
            parts.append(f'机会筛选：只使用信号出现前已收盘的完整4小时行情，观察最近{float(lookback)/24:g}日最高低振幅，范围{float(low):.0%}～{float(high):.0%}。这是已知历史价格波段，不是未来价格涨幅或账户收益保证。')
        if movement.get('min_profit_move_pct') is not None:
            parts.append(f'常规价格止盈目标至少覆盖{float(movement["min_profit_move_pct"]):.0%}的标的价格波段；止损、反向趋势、持仓上限及截尾等例外单独记录。')
    return '<p class="notice"><strong>'+html.escape(source['symbol'])+'</strong>：'+html.escape(' '.join(parts))+'</p>' if parts else ''


def _scope_card(scope, prefix):
    """为一个报告层级构建包含收益与操作入口的摘要卡片。

    Args:
        scope: 当前模型/标的报告层级摘要。
        prefix: HTML相对导航路径前缀。

    Returns:
        HTML卡片字符串。
    """
    summary = scope['summary']
    account = summary['accounts'][summary['default_config']]
    return ('<div class="card"><h3>'+html.escape(scope['symbol'])+'</h3>'
        '<p>默认观察账户：'+html.escape(account['config'])+'</p><div class="value">'
        +f'{account["net_return"]:.2%}'+'</div><p>'+str(account['round_trips'])+'笔完整交易 · '
        +f'平均持仓{account["holding_hours_mean"]:.1f}小时</p><p>'
        +str(summary['configurations'])+'个周期 / 止盈配置可切换</p><p><a href="'
        +prefix+'operations.html">分标的全部操作与持仓</a> · <a href="'+prefix
        +'trade_economics.html">逐笔价格波段与收益</a></p></div>')
