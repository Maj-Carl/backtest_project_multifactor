"""交易日历工具：优先官方源（akshare），失败时回退本地 silver 观察日历。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Optional

import pandas as pd


@lru_cache(maxsize=1)
def _official_trade_dates() -> pd.DatetimeIndex:
    import akshare as ak

    df = ak.tool_trade_date_hist_sina()
    if "trade_date" not in df.columns:
        raise ValueError("官方交易日历缺少 trade_date 列")
    days = pd.to_datetime(df["trade_date"], errors="coerce").dropna().dt.normalize()
    if days.empty:
        raise ValueError("官方交易日历为空")
    return pd.DatetimeIndex(days.sort_values().unique())


def _observed_trade_dates_from_silver(
    cache_dir: Path,
    period: str,
    ty: str,
    *,
    sample_limit: int = 100,
) -> pd.DatetimeIndex:
    silver_dir = cache_dir / "silver"
    pattern = f"*_{period}_*_{str(ty).replace('/', '_')}.parquet"
    paths = sorted(silver_dir.glob(pattern))[: max(1, int(sample_limit))]
    observed: set[pd.Timestamp] = set()
    for p in paths:
        try:
            dates = pd.read_parquet(p, columns=["date"])["date"]
        except Exception:
            continue
        observed.update(pd.to_datetime(dates, errors="coerce").dropna().dt.normalize().tolist())
    return pd.DatetimeIndex(sorted(observed))


def get_trade_days(
    start: str | pd.Timestamp,
    end: str | pd.Timestamp,
    *,
    cache_dir: Optional[Path] = None,
    period: str = "1d",
    ty: str = "个股",
) -> pd.DatetimeIndex:
    s = pd.Timestamp(start).normalize()
    e = pd.Timestamp(end).normalize()
    if s > e:
        return pd.DatetimeIndex([])

    try:
        official = _official_trade_dates()
        return official[(official >= s) & (official <= e)]
    except Exception:
        pass

    if cache_dir is not None and period == "1d":
        observed = _observed_trade_dates_from_silver(cache_dir, period, ty)
        if len(observed) > 0:
            return observed[(observed >= s) & (observed <= e)]

    raise RuntimeError(
        "无法获取交易日历：官方源不可用且本地 silver 观察日历为空。"
        "请联网后重试，或先准备本地 silver 数据。"
    )
