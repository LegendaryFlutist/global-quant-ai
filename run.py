"""兼容旧模块 run；业务实现位于 quant_lab.cli.run。"""
if __name__ == '__main__':
    from quant_lab.cli.run import main
    raise SystemExit(main())
else:
    from quant_lab.compat import alias_legacy
    alias_legacy(__name__, 'quant_lab.cli.run')
