"""历史接口返回的中文列名归一映射。"""

from __future__ import annotations

import pandas as pd


def normalize_legacy_columns(df: pd.DataFrame) -> pd.DataFrame:
    rename_map = {
        "日期": "date",
        "代码": "code",
        "开盘": "open",
        "最高": "high",
        "最低": "low",
        "收盘": "close",
        "成交量（手）": "volume",
        "成交额": "amount",
        "振幅": "amplitude",
        "涨跌幅": "pct_change",
        "换手率": "turnover",
    }
    df.rename(columns=rename_map, inplace=True)
    if "date" not in df.columns:
        raise ValueError("数据缺少日期字段，无法继续处理。")
    df["date"] = pd.to_datetime(df["date"])
    df.sort_values(
        by=["code", "date"] if "code" in df.columns else ["date"], inplace=True
    )
    return df
