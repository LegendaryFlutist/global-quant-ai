"""Export validation-only model comparison curves and a static report."""
import html
import os
from pathlib import Path
import tempfile
import numpy as np
import pandas as pd


def export_comparison(out, curves, summary, source):
    # All series are out-of-fold validation predictions; no holdout re-ranking.
    os.environ.setdefault('MPLCONFIGDIR',str(Path(tempfile.gettempdir())/'quant_lab_matplotlib'))
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    wide=pd.DataFrame(curves)
    wide.index.name='exit_time'
    wide.to_csv(out/'validation_equity.csv')
    pd.DataFrame(summary).to_csv(out/'model_comparison.csv',index=False)
    times=pd.to_datetime(wide.index,unit='ms',utc=True)
    label=source['symbol']+(' (SYNTHETIC)' if source.get('synthetic',True) else '')
    fig,ax=plt.subplots(figsize=(13,6),layout='constrained')
    for name in wide:
        ax.plot(times,(wide[name]-1)*100,label=name,linewidth=1.3)
    ax.axhline(0,color='gray',linewidth=.7)
    ax.set(title=label+' | 1h trend / 15m entry | OOF validation',ylabel='Net return (%)',xlabel='UTC')
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d'))
    ax.grid(alpha=.2);ax.legend(fontsize=8,ncol=3)
    for ext in ('png','svg'):
        fig.savefig(out/('model_comparison.'+ext),dpi=150)
    plt.close(fig)
    model_names=[s['name'] for s in summary]
    fig,axes=plt.subplots(4,2,figsize=(13,13),sharex=True,layout='constrained')
    for ax,name in zip(axes.flat,model_names):
        ax.plot(times,(wide[name]-1)*100,linewidth=1)
        ax.axhline(0,color='gray',linewidth=.7);ax.grid(alpha=.2)
        row=next(s for s in summary if s['name']==name)
        ax.set(title=f"{name} | return {row['stitched_net_return']:.2%} | DD {row['stitched_max_drawdown']:.2%}",ylabel='Net return (%)')
        ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=3,maxticks=5))
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d'))
        ax.tick_params(axis='x',labelsize=8)
    fig.suptitle(label+' | each model, threshold chosen on validation only')
    for ext in ('png','svg'):
        fig.savefig(out/('individual_models.'+ext),dpi=150)
    plt.close(fig)
    rows=[]
    for s in summary:
        rows.append('<tr>'+''.join('<td>'+html.escape(str(v))+'</td>' for v in
            (s['name'],s['threshold'],f"{s['stitched_net_return']:.2%}",
             f"{s['stitched_max_drawdown']:.2%}",s['entries'],s['eligible']))+'</tr>')
    page='''<!doctype html><meta charset="utf-8"><title>模型收益曲线比较</title>
<style>body{max-width:1300px;margin:25px auto;font:16px sans-serif}img{width:100%}td,th{padding:8px;border-bottom:1px solid #ddd}table{border-collapse:collapse}</style>'''
    page+='<h1>'+html.escape(label)+' 模型比较</h1><p>1小时趋势过滤，15分钟信号入场。仅比较验证集，末尾20%测试集不参与排名。折间平仓，净值按折复合，已计手续费、滑点及资金费率。曲线包含初始净值1。</p>'
    page+='<p>每个模型阈值在同一验证集选择，曲线仍有选择偏差；此页不能证明未来收益。候选筛选另见 report.json，未合格不会自动启用。</p>'
    page+='<img src="model_comparison.svg" alt="所有模型收益对比"><table><tr><th>模型</th><th>阈值</th><th>验证收益</th><th>最大回撤</th><th>开仓次数</th><th>合格</th></tr>'+''.join(rows)+'</table><img src="individual_models.svg" alt="每个模型单独曲线">'
    (out/'comparison.html').write_text(page,encoding='utf-8')
