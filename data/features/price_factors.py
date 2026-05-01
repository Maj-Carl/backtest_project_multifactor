"""由日线行情推导的工程特征。"""

from __future__ import annotations

import pandas as pd


def add_factor_columns(df: pd.DataFrame) -> pd.DataFrame:
    """在原行情列上追加 ``ret_1`` / ``mom*`` / ``vol20`` / ``liq20`` 等列。"""
    out = df.copy()
    out["ret_1"] = out["close"].pct_change()
    out["mom20"] = out["close"].pct_change(20)
    out["mom60"] = out["close"].pct_change(60)
    out["vol20"] = out["ret_1"].rolling(20).std()
    liquidity_base = out["amount"] if "amount" in out.columns else out["volume"]
    out["liq20"] = liquidity_base.rolling(20).mean()
    return out


__all__ = ["add_factor_columns"]
