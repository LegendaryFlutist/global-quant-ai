#!/usr/bin/env python3
"""Inspect tracked/staged publish content without printing sensitive values."""
import argparse
import os
from pathlib import Path
import re
import subprocess

DENIED_PARTS={'.env','.runtime','.venv','.aws','.codex','.agents','node_modules','__pycache__','data','runs'}
DENIED_SUFFIXES={'.pem','.key','.p12','.pfx','.eml','.joblib','.pt','.pth','.onnx','.parquet','.sqlite','.db','.pyc'}
PATTERNS={
 'private_key':re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
 'github_token':re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})\b'),
 'aws_access_key':re.compile(r'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b'),
 'possible_exchange_key':re.compile(r'(?<![A-Za-z0-9])[A-Za-z0-9]{64}(?![A-Za-z0-9])'),
}

def git(*args):
    """执行只用于发布检查的Git子命令并获取原始输出。

    Args:
        *args: 透传的额外位置参数，按当前接口契约解释。

    Returns:
        命令标准输出字节；Git失败传播CalledProcessError。
    """
    return subprocess.check_output(['git',*args])

def main():
    """检查将要发布的路径、大小和凭据模式，并只报告脱敏位置。

    Returns:
        无返回值；发现禁止文件或疑似凭据以非零码退出。
    """
    parser=argparse.ArgumentParser();parser.add_argument('--staged',action='store_true');args=parser.parse_args()
    paths=[p for p in git('ls-files','-z').decode().split('\0') if p]
    failures=[];size=0
    for name in paths:
        path=Path(name)
        if any(part in DENIED_PARTS for part in path.parts) or path.suffix in DENIED_SUFFIXES:
            failures.append((name,'excluded_file'));continue
        if path.name.startswith('.env') and path.name!='.env.example':failures.append((name,'environment_file'));continue
        if not path.is_file():continue
        data=git('show',':'+name) if args.staged else path.read_bytes();size+=len(data)
        if len(data)>5*1024*1024:failures.append((name,'oversized_file'))
        try:content=data.decode('utf-8')
        except UnicodeDecodeError:continue
        for kind,pattern in PATTERNS.items():
            for match in pattern.finditer(content):
                value=match.group()
                # Dataset/code SHA256 hex digests are public provenance, not exchange keys.
                if kind=='possible_exchange_key' and re.fullmatch('[0-9a-fA-F]{64}',value):continue
                line=content[content.rfind('\n',0,match.start())+1:content.find('\n',match.end()) if '\n' in content[match.end():] else len(content)]
                if kind=='possible_exchange_key' and path.name=='package-lock.json' and re.fullmatch(r'\s*"integrity"\s*:\s*"sha(?:256|384|512)-[A-Za-z0-9+/=]+"\s*,?\s*',line):continue
                failures.append((name,kind+' line '+str(content.count('\n',0,match.start())+1)))
    if failures:
        for name,kind in failures:print(f'BLOCKED: {name}: {kind}')
        raise SystemExit(1)
    print(f'PASS: {len(paths)} files, {size:,} bytes; no prohibited paths or detected credential patterns')

if __name__=='__main__':main()
