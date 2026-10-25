"""Direct entry point: local pattern research with simulated email records."""
import argparse
from datetime import datetime
from zoneinfo import ZoneInfo
import hashlib
import html
import importlib.util
import importlib.metadata
import json
import os
import platform
from pathlib import Path
import sys
import tempfile
from quant_lab.core.research_config import DEFAULT_CONFIG,load_config
from quant_lab.models.model_registry import MODEL_NAMES

from quant_lab.core.paths import PROJECT_ROOT

ROOT=PROJECT_ROOT
DEFAULT_SYMBOLS=('KORUUSDT','CLUSDT','NVDAUSDT','BTCUSDT')
SUPPORTED_SYMBOLS=DEFAULT_SYMBOLS+('SKHYNIXUSDT','SPCXUSDT','SNDKUSDT','XAUUSDT','QQQUSDT','INTCUSDT','TSLAUSDT','OPENAIUSDT','ZHIPUUSDT')
TIMEFRAMES=('15m','30m','1h','2h','4h')


def build_plan(root,symbols):
    """核对标的真实来源、数据校验和冻结留出边界，生成研究任务。

    Args:
        root: 当前任务的项目/数据根目录路径；不会自动读取其他目录。
        symbols: 明确登记且本次选择的标的代码序列。

    Returns:
        任务字典列表；缺文件或来源/哈希不匹配时拒绝运行。
    """
    jobs=[]
    manifest=root/'data/priority_analysis_latest.json'
    analysis_sources=json.loads(manifest.read_text()).get('directories',{}) if manifest.exists() else {}
    for symbol in symbols:
        data=root/'data'/(symbol.lower()+'_90d_connector')
        baseline=root/'runs'/(symbol.lower()+'_1h_15m_20261017')/'report.json'
        if (data/'evaluation_plan.json').is_file():baseline=data/'evaluation_plan.json'
        for path in [data/name for name in ('bars.csv','funding.csv','mark_bars.csv','source.json')]+[baseline]:
            if not path.is_file():raise ValueError(f'缺少已有研究数据：{path}；请恢复该文件或使用 lab.py patterns 指定数据。')
        source=json.loads((data/'source.json').read_text())
        report=json.loads(baseline.read_text())
        if source.get('symbol')!=symbol or source.get('synthetic') is not False:
            raise ValueError(f'{symbol} 的数据来源标记不匹配。')
        digest=hashlib.sha256((data/'bars.csv').read_bytes()+(data/'funding.csv').read_bytes()).hexdigest()
        if report.get('dataset_sha256')!=digest or len(report.get('holdout_period_ms',[]))!=2:
            raise ValueError(f'{symbol} 数据与基准报告不匹配，请使用匹配的数据和原预留测试边界。')
        analysis=root/analysis_sources[symbol] if symbol in analysis_sources else data
        jobs.append(dict(symbol=symbol,data=data,baseline=baseline,source=source,analysis_data=analysis))
    return jobs


def save_summary(batch,records):
    """把本批任务状态同步写为JSON和可离线查看的汇总页面。

    Args:
        batch: 本次研究批次目录。
        records: 已保存的标的运行状态记录序列。

    Returns:
        无返回值；仅写入传入批次目录，不训练模型。
    """
    (batch/'run_status.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')
    parts=['<!doctype html><meta charset="utf-8"><title>本次量化研究</title>',
           '<style>body{max-width:1000px;margin:30px auto;font:16px sans-serif}</style>',
           '<h1>12类反转形态与多止盈研究</h1><p>已有历史行情回测；邮件仅模拟记录。</p>']
    for row in records:
        label=html.escape(row['symbol']+'：'+row['status'])
        if row.get('report'):
            parts.append(f'<p><a href="{html.escape(row["report"],quote=True)}">{label}，查看收益曲线</a></p>')
        else:parts.append('<p>'+label+'</p>')
    (batch/'index.html').write_text('\n'.join(parts),encoding='utf-8')


def main(argv=None,root=ROOT):
    """解析启动配置，检查环境并按标的执行已授权的模拟研究。

    Args:
        argv: 命令行参数列表；None时读取当前进程参数。
        root: 当前任务的项目/数据根目录路径；不会自动读取其他目录。

    Returns:
        进程退出码：0表示完成或预检通过，1表示检查/任务失败。
    """
    pre=argparse.ArgumentParser(add_help=False)
    pre.add_argument('--config',type=Path,default=DEFAULT_CONFIG)
    early,_=pre.parse_known_args(argv)
    try:defaults=load_config(early.config)
    except (OSError,ValueError,TypeError) as error:
        print('研究配置读取失败：'+str(error),file=sys.stderr)
        return 1
    parser=argparse.ArgumentParser(description='已核验合约的多周期结构与道氏123规则回测。')
    parser.add_argument('--config',type=Path,default=early.config,help='JSON研究参数；命令行参数可覆盖')
    parser.add_argument('--out',type=Path,help='本次统一输出目录，必须不存在')
    parser.add_argument('--symbols',nargs='+',choices=SUPPORTED_SYMBOLS,default=list(DEFAULT_SYMBOLS))
    parser.add_argument('--models',nargs='+',choices=MODEL_NAMES,default=defaults['models'])
    parser.add_argument('--timeframes',nargs='+',choices=TIMEFRAMES,default=defaults['timeframes'])
    for name in defaults:
        if name not in ('timeframes','models'):parser.add_argument('--'+name.replace('_','-'),type=float,default=defaults[name])
    parser.add_argument('--check',action='store_true',help='仅检查环境与数据，不重跑回测')
    args=parser.parse_args(argv)
    if len(set(args.symbols))!=len(args.symbols) or len(set(args.timeframes))!=len(args.timeframes):
        parser.error('标的和周期不能重复')
    if len(set(args.models))!=len(args.models) or any(name not in MODEL_NAMES for name in args.models):
        parser.error('模型名称无效或重复')
    if not 1<=args.leverage<100 or not 0<args.margin_fraction<=1:
        parser.error('固定1%维持保证金率假设下杠杆需≥1且<100，保证金比例需大于0且不超过1')
    if not args.execution_delay_minutes>=0 or not 0<=args.min_holding_hours<=args.max_holding_hours or not 0<args.max_holding_hours:
        parser.error('执行延迟不能为负，持仓上限需大于0且不能小于下限')
    root=Path(root).resolve()
    # Set before scientific libraries are imported; never read SMTP credentials.
    os.environ['OMP_NUM_THREADS']='1'
    os.environ['OPENBLAS_NUM_THREADS']='1'
    os.environ['QUANT_MAIL_MODE']='simulate'
    missing=[name for name in ('numpy','pandas','sklearn','joblib','matplotlib') if importlib.util.find_spec(name) is None]
    if missing:
        print('依赖缺失：'+', '.join(missing)+f'。请使用 {root}/.venv/bin/python，并安装 requirements.txt。',file=sys.stderr)
        return 1
    try:jobs=build_plan(root,args.symbols)
    except (OSError,ValueError,KeyError,TypeError) as error:
        print('启动检查失败：'+str(error),file=sys.stderr)
        return 1
    print(f'Python：{sys.executable}',flush=True)
    print(f'研究配置：{args.leverage:g}倍逐仓，保证金{args.margin_fraction:.0%}；周期 '+', '.join(args.timeframes),flush=True)
    print(f'持仓目标：{args.min_holding_hours:g}–{args.max_holding_hours:g}小时；信号响应延迟{args.execution_delay_minutes:g}分钟；风险退出仍有效。',flush=True)
    print(f'价格波段：过去{args.swing_lookback_hours:g}小时范围{args.swing_min_range:.0%}–{args.swing_max_range:.0%}；止盈最小价格变动{args.min_profit_move_pct:.0%}；百分比模式目标{args.price_target_pct:.0%}。',flush=True)
    for job in jobs:
        source=job['source']
        def date(value):
            """把行情毫秒时间戳格式化为上海时间，缺失时提示查看来源文件。

            Args:
                value: 待转换或校验的当前值，具体口径见函数用途。

            Returns:
                面向控制台的日期字符串。
            """
            return datetime.fromtimestamp(value/1000,ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M') if value is not None else '见source.json'
        print(f"{job['symbol']}：已有行情 {date(source.get('start'))} 至 {date(source.get('end'))}（上海时间）",flush=True)
        if job['analysis_data']!=job['data']:
            analysis_source=json.loads((job['analysis_data']/'source.json').read_text())
            print(f"  独立观察行情截至 {date(analysis_source.get('end'))}；较新观察K线不计入本轮训练与回测。",flush=True)
    print('启动检查通过；使用本地历史数据，邮件模拟记录。',flush=True)
    if args.check:return 0
    from quant_lab.research.pattern_research import run_pattern_research
    if args.out:
        batch=args.out if args.out.is_absolute() else root/args.out
        if batch.exists():
            print('输出目录已存在；为保留旧结果，请使用新目录：'+str(batch),file=sys.stderr)
            return 1
        batch.mkdir(parents=True)
    else:
        parent=root/'runs';parent.mkdir(exist_ok=True,parents=True)
        stamp=datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y%m%d_%H%M%S')
        batch=Path(tempfile.mkdtemp(prefix='run_'+stamp+'_',dir=parent))
    research_args={name:getattr(args,name) for name in defaults}
    research_args['models']=args.models
    (batch/'config.json').write_text(json.dumps(research_args,ensure_ascii=False,indent=2),encoding='utf-8')
    runtime=dict(python=sys.version,executable=sys.executable,architecture=platform.machine(),
        containerized=Path('/.dockerenv').exists(),mail_mode=os.environ['QUANT_MAIL_MODE'],
        started_at=datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),
        packages={name:importlib.metadata.version(name) for name in ('numpy','pandas','scikit-learn','joblib','matplotlib')})
    (batch/'runtime.json').write_text(json.dumps(runtime,ensure_ascii=False,indent=2),encoding='utf-8')
    records=[dict(symbol=j['symbol'],status='WAITING',run_dir='symbols/'+j['symbol']) for j in jobs]
    save_summary(batch,records)
    for index,job in enumerate(jobs):
        records[index]['status']='RUNNING';save_summary(batch,records)
        output=batch/records[index]['run_dir']
        output.parent.mkdir(parents=True,exist_ok=True)
        print(f"开始 {job['symbol']}；输出目录：{output}",flush=True)
        try:
            from quant_lab.reports.structure_report import analyze_market
            analyze_market(job['symbol'],job['analysis_data'],batch/'analysis'/job['symbol'])
            records[index]['analysis']='analysis/'+job['symbol']+'/index.html'
            report=run_pattern_research(job['data'],output,job['baseline'],**research_args)
            page='operations.html' if (output/'operations.html').exists() else 'comparison.html'
            records[index].update(status=report['status'],report=records[index]['run_dir']+'/'+page)
        except (OSError,ValueError,RuntimeError,ImportError) as error:
            records[index].update(status='FAILED',error=str(error))
            print(f"{job['symbol']} 运行失败：{error}",file=sys.stderr,flush=True)
        save_summary(batch,records)
    from quant_lab.reports.structure_report import export_analysis_overview
    export_analysis_overview(batch,records)
    if any((batch/record['run_dir']/'report.json').is_file() for record in records):
        from quant_lab.reports.batch_report import export_batch_report
        export_batch_report(batch,records)
    print('本次汇总页面：'+str(batch/'index.html'),flush=True)
    if batch.is_relative_to(root):print('宿主项目内报告路径：'+str(batch.relative_to(root)/'index.html'),flush=True)
    return int(any(row['status']=='FAILED' for row in records))


if __name__=='__main__':raise SystemExit(main())
