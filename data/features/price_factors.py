"""由日线行情推导的工程特征。"""

from __future__ import annotations

import numpy as np
import pandas as pd


def add_factor_columns(df: pd.DataFrame) -> pd.DataFrame:
    """在原行情列上追加收益、动量、波动、流动性、反转、Amihud、规模及 IC 用前向收益等列。"""
    out = df.copy()
    out["ret_1"] = out["close"].pct_change()
    out["mom20"] = out["close"].pct_change(20)
    out["mom60"] = out["close"].pct_change(60)
    out["rev20"] = -out["mom20"]
    out["vol20"] = out["ret_1"].rolling(20).std()
    out["rollvol20"] = out["vol20"]
    neg_ret = out["ret_1"].where(out["ret_1"] < 0)
    out["dvol20"] = neg_ret.rolling(20).std()
    # 窗口内若无下跌日则 std 为空；用全样本波动率替代，避免截面/回测整条得分被 NaN 否决
    out["dvol20"] = out["dvol20"].fillna(out["vol20"])
    liquidity_base = out["amount"] if "amount" in out.columns else out["volume"]
    out["liq20"] = liquidity_base.rolling(20).mean()
    amt = out["amount"].replace(0, np.nan) if "amount" in out.columns else None
    if amt is not None:
        out["amihud20"] = (out["ret_1"].abs() / amt).rolling(20).mean()
    else:
        vol = out["volume"].replace(0, np.nan)
        out["amihud20"] = (out["ret_1"].abs() / vol).rolling(20).mean()
    turn = (out["close"] * out["volume"]).clip(lower=1.0)
    out["log_size"] = np.log(turn).rolling(20).mean()
    out["fwd_ret_5"] = out["close"].shift(-5) / out["close"] - 1.0
    return out


__all__ = ["add_factor_columns"]
