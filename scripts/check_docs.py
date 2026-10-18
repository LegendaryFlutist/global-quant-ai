#!/usr/bin/env python3
"""检查项目所有具名Python函数是否有说明；不导入模块或执行研究任务。"""
import ast
from pathlib import Path


def check_documentation(root=None):
    """静态遍历生产、测试、迁移和工具函数，返回缺少docstring的位置。

    Args:
        root: 项目根目录；默认由当前检查脚本位置确定。

    Returns:
        已检查函数数和缺失项列表；匿名lambda由其所属函数的文档解释。
    """
    root=Path(root) if root else Path(__file__).resolve().parents[1]
    files=[*root.glob('*.py')]
    for name in ('quant_lab','backend','scripts','tests'):
        files.extend((root/name).rglob('*.py'))
    count=0;missing=[]
    for path in sorted(set(files)):
        if '__pycache__' in path.parts:continue
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
            if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)):
                count+=1
                if not ast.get_docstring(node):missing.append(f'{path.relative_to(root)}:{node.lineno} {node.name}')
    return count,missing


def main():
    """输出静态文档覆盖结果；缺少函数说明时以非零状态退出供CI拦截。"""
    count,missing=check_documentation()
    if missing:
        print('\n'.join(missing))
        raise SystemExit(1)
    print(f'PASS: all {count} named Python functions have docstrings')


if __name__=='__main__':main()
