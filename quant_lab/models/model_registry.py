"""Named, version-pinned algorithms selectable in the research backtest page."""

DEEP_MODEL_NAMES = ("patchtst", "tft_attention", "tcn")
MODEL_NAMES = ("analysis_dow123", *DEEP_MODEL_NAMES)
MODEL_LABELS = {
    "analysis_dow123": "多周期结构 · 道氏123",
    "patchtst": "PatchTST · 分段Transformer",
    "tft_attention": "TFT-Attention · 时序融合注意力",
    "tcn": "TCN · 膨胀时序卷积",
}
MODEL_ALGORITHMS = {
    "analysis_dow123": "Confirmed Dow 1-2-3 structure with four-times higher-timeframe trend gate",
    "patchtst": "48-bar overlapping patches, two Transformer encoder layers, 32 hidden units, four-head attention",
    "tft_attention": "48-bar variable selection, one-layer LSTM, four-head temporal attention and gated residual",
    "tcn": "48-bar residual temporal convolutions with dilation rates 1, 2 and 4",
}
MODEL_SEEDS = {name: 42 for name in DEEP_MODEL_NAMES}
