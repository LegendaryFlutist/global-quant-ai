import numpy as np
import pandas as pd

COLS = ["open_time", "open", "high", "low", "close", "volume", "close_time",
        "quote_volume", "trades", "taker_volume", "taker_quote", "unused"]


def parse_klines(rows):
    if not rows:
        raise RuntimeError("Empty Kline response")
    df = pd.DataFrame(rows, columns=COLS)
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="raise")
    for col in ["open_time", "close_time"]:
        df[col] = pd.to_numeric(df[col], errors="raise").astype("int64")
    return df.sort_values("open_time").drop_duplicates("open_time").reset_index(drop=True)


FEATURES = ["ret1", "ret3", "ret8", "ema_gap", "volatility", "candle_range",
            "body", "volume_z", "volume_change"]


def make_features(bars):
    d = bars.copy()
    close = d["close"]
    d["ret1"] = close.pct_change(1)
    d["ret3"] = close.pct_change(3)
    d["ret8"] = close.pct_change(8)
    d["ema_gap"] = (close.ewm(span=12, adjust=False).mean() /
                    close.ewm(span=48, adjust=False).mean()) - 1
    d["volatility"] = d["ret1"].rolling(24).std()
    d["candle_range"] = (d["high"] - d["low"]) / close
    d["body"] = (d["close"] - d["open"]) / d["open"]
    logvol = np.log1p(d["volume"])
    d["volume_z"] = (logvol - logvol.rolling(48).mean()) / (
        logvol.rolling(48).std() + 1e-10)
    d["volume_change"] = logvol.diff(1)
    return d.replace([np.inf, -np.inf], np.nan)
