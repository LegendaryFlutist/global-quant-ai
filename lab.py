"""兼容旧模块 lab；业务实现位于 quant_lab.cli.lab。"""
if __name__ == '__main__':
    import sys
    if len(sys.argv) == 1:
        from run import main
        raise SystemExit(main())
    from quant_lab.cli.lab import main
    main()
else:
    from quant_lab.compat import alias_legacy
    alias_legacy(__name__, 'quant_lab.cli.lab')
