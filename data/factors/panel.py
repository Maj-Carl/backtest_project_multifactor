"""将多标的日频因子做截面去极值、市值（规模）中性、标准化，并写回各标的 DataFrame。"""

from __future__ import annotations

import numpy as np
import pandas as pd

from utils.logger import get_backtest_logger

# 参与截面管线（log_size 仅作回归自变量，不做 z 输出）
FACTOR_COLS_FOR_CS = (
    "mom20",
    "mom60",
    "vol20",
    "liq20",
    "rev20",
    "dvol20",
    "amihud20",
)


def _promote_cs_to_factor_columns(merged: pd.DataFrame) -> pd.DataFrame:
    """把 ``cs_*`` 写回与行情因子同名列，供 PandasData 用常规列名绑定；原始日波动放入 ``rollvol20``。"""
    m = merged.copy()
    cs_present = any(str(c).startswith("cs_") for c in m.columns)
    if not cs_present:
        return m
    if "vol20" in m.columns:
        m["rollvol20"] = m["vol20"].astype(float)
    for col in FACTOR_COLS_FOR_CS:
        csn = f"cs_{col}"
        if csn in m.columns:
            m[col] = m[csn]
    drop_cs = [c for c in m.columns if str(c).startswith("cs_")]
    m.drop(columns=drop_cs, inplace=True, errors="ignore")
    return m


def _winsorize_vec(y: np.ndarray, low_pct: float, high_pct: float) -> np.ndarray:
    m = np.isfinite(y)
    if m.sum() < 5:
        return y
    qlo, qhi = np.nanpercentile(y[m], [low_pct * 100.0, high_pct * 100.0])
    out = y.astype(float, copy=True)
    out[m] = np.clip(out[m], qlo, qhi)
    return out


def _neutralize_vs_size(y: np.ndarray, log_size: np.ndarray, min_n: int) -> np.ndarray:
    m = np.isfinite(y) & np.isfinite(log_size)
    if int(m.sum()) < min_n:
        return y
    yv = y[m].astype(float)
    xv = log_size[m].astype(float)
    X = np.column_stack([np.ones(len(yv)), xv])
    beta, _, _, _ = np.linalg.lstsq(X, yv, rcond=None)
    resid = yv - X @ beta
    out = y.astype(float, copy=True)
    out[m] = resid
    out[~m] = np.nan
    return out


def _zscore_vec(y: np.ndarray) -> np.ndarray:
    m = np.isfinite(y)
    if int(m.sum()) < 5:
        return np.full_like(y, np.nan, dtype=float)
    yv = y[m]
    mu = float(yv.mean())
    sig = float(yv.std())
    out = np.full_like(y, np.nan, dtype=float)
    if sig < 1e-12:
        out[m] = 0.0
    else:
        out[m] = (yv - mu) / sig
    return out


def _index_to_trade_dates(idx: pd.Index) -> pd.DatetimeIndex:
    t = pd.to_datetime(idx, errors="coerce")
    if isinstance(t, pd.DatetimeIndex) and t.tz is not None:
        t = t.tz_convert(None)
    return t.normalize()


def _multi_to_long(multi_data: dict[str, pd.DataFrame], cols: list[str]) -> pd.DataFrame:
    parts: list[pd.DataFrame] = []
    for code, df in multi_data.items():
        miss = [c for c in cols if c not in df.columns]
        if miss:
            continue
        x = df[cols].copy()
        x["code"] = code
        x.insert(0, "trade_date", _index_to_trade_dates(x.index))
        parts.append(x.reset_index(drop=True))
    if not parts:
        return pd.DataFrame()
    return pd.concat(parts, ignore_index=True)


def _normalize_calendar_day(s: pd.Series) -> pd.Series:
    """统一为日历日 naive datetime，避免 merge 时 dtype/时区不一致导致全 NaN。"""
    t = pd.to_datetime(s, errors="coerce")
    try:
        if t.dt.tz is not None:
            t = t.dt.tz_convert(None)
    except (TypeError, ValueError, AttributeError):
        pass
    return t.dt.normalize()


def _long_cs_to_dict(long_cs: pd.DataFrame, multi_data: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    cs_cols = [c for c in long_cs.columns if c.startswith("cs_")]
    if not cs_cols:
        return dict(multi_data)
    for code, df in multi_data.items():
        sub = long_cs[long_cs["code"] == code][["trade_date"] + cs_cols].copy()
        if sub.empty:
            m = df.copy()
            for c in cs_cols:
                m[c] = float("nan")
            out[code] = _promote_cs_to_factor_columns(m)
            continue
        left = df.reset_index()
        idx_col = left.columns[0]
        left = left.rename(columns={idx_col: "_d"})
        left["_d"] = _normalize_calendar_day(left["_d"])
        sub = sub.rename(columns={"trade_date": "_d"})
        sub["_d"] = _normalize_calendar_day(sub["_d"])
        sub = sub.drop_duplicates(subset=["_d"], keep="last")
        merged = left.merge(sub[["_d"] + cs_cols], on="_d", how="left")
        merged = merged.set_index("_d").sort_index()
        merged.index.name = df.index.name
        out[code] = _promote_cs_to_factor_columns(merged)
    return out


def apply_cross_section_to_multi_data(
    multi_data: dict[str, pd.DataFrame],
    *,
    winsor_low: float = 0.01,
    winsor_high: float = 0.99,
    min_names_per_day: int = 40,
) -> dict[str, pd.DataFrame]:
    """对每个交易日：分位去极值 → 对 log_size 一元回归取残差 → 截面 z-score，生成 ``cs_*`` 列。"""
    log = get_backtest_logger()
    cols_needed = list(FACTOR_COLS_FOR_CS) + ["log_size"]
    long_df = _multi_to_long(multi_data, cols_needed)
    if long_df.empty:
        log.warning("[截面因子] 无法拼接长表，跳过截面处理。")
        return dict(multi_data)

    out_days: list[pd.DataFrame] = []
    for trade_date, day in long_df.groupby("trade_date", sort=True):
        day = day.copy()
        n = len(day)
        if n < min_names_per_day:
            for col in FACTOR_COLS_FOR_CS:
                day[f"cs_{col}"] = np.nan
            out_days.append(day)
            continue

        size = day["log_size"].to_numpy(dtype=float)
        for col in FACTOR_COLS_FOR_CS:
            y = day[col].to_numpy(dtype=float)
            y = _winsorize_vec(y, winsor_low, winsor_high)
            y = _neutralize_vs_size(y, size, min_n=min_names_per_day)
            y = _zscore_vec(y)
            day[f"cs_{col}"] = y
        out_days.append(day)

    long_cs = pd.concat(out_days, ignore_index=True)
    merged = _long_cs_to_dict(long_cs, multi_data)
    log.info(
        "[截面因子] 已对 %s 个因子做截面处理；样本日=%s、标的数=%s",
        len(FACTOR_COLS_FOR_CS),
        long_df["trade_date"].nunique(),
        len(multi_data),
    )
    return merged
