"""因子 Rank IC（Spearman）与 IC_IR 摘要；按日滚动 IC 符号预计算（见 build_rolling_ic_weight_signs）。"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Iterable

import pandas as pd

from utils.logger import get_backtest_logger

FACTOR_COLS_IC = (
    "mom20",
    "mom60",
    "vol20",
    "liq20",
    "rev20",
    "dvol20",
    "amihud20",
)


def _multi_to_ic_long(multi_data: dict[str, pd.DataFrame], cols: Iterable[str]) -> pd.DataFrame:
    parts: list[pd.DataFrame] = []
    need = list(cols) + ["fwd_ret_5"]
    for code, df in multi_data.items():
        miss = [c for c in need if c not in df.columns]
        if miss:
            continue
        x = df[need].copy()
        x["code"] = code
        x.insert(0, "trade_date", pd.to_datetime(x.index))
        parts.append(x.reset_index(drop=True))
    if not parts:
        return pd.DataFrame()
    return pd.concat(parts, ignore_index=True)


def build_ic_daily_from_multi(multi_data: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """全市场逐日 Rank IC（因子 vs 未来 5 日收益），行为与历史 IC 报告一致。"""
    long_df = _multi_to_ic_long(multi_data, FACTOR_COLS_IC)
    if long_df.empty:
        return pd.DataFrame()
    rows_ic: list[pd.DataFrame] = []
    for dt, day in long_df.groupby("trade_date", sort=True):
        if len(day) < 30:
            continue
        ic_row = {"trade_date": dt}
        sub = day.dropna(subset=["fwd_ret_5"], how="any")
        if len(sub) < 30:
            continue
        for fac in FACTOR_COLS_IC:
            pair = sub[[fac, "fwd_ret_5"]].dropna()
            if len(pair) < 30:
                ic_row[fac] = float("nan")
                continue
            ic_row[fac] = pair[fac].corr(pair["fwd_ret_5"], method="spearman")
        rows_ic.append(pd.DataFrame([ic_row]))
    if not rows_ic:
        return pd.DataFrame()
    return pd.concat(rows_ic, ignore_index=True).set_index("trade_date").sort_index()


def ic_summary_from_daily(ic_daily: pd.DataFrame) -> pd.DataFrame:
    """由日度 IC 得到各因子 mean_ic / std_ic / ic_ir / n_days。"""
    if ic_daily.empty:
        return pd.DataFrame(columns=["factor", "mean_ic", "std_ic", "ic_ir", "n_days"])
    summary_rows = []
    for fac in FACTOR_COLS_IC:
        if fac not in ic_daily.columns:
            summary_rows.append({"factor": fac, "mean_ic": None, "std_ic": None, "ic_ir": None, "n_days": 0})
            continue
        s = ic_daily[fac].dropna()
        if s.empty:
            summary_rows.append({"factor": fac, "mean_ic": None, "std_ic": None, "ic_ir": None, "n_days": 0})
            continue
        m = float(s.mean())
        sd = float(s.std(ddof=1)) if len(s) > 1 else 0.0
        ir = (m / sd) if sd > 1e-12 else float("nan")
        summary_rows.append(
            {"factor": fac, "mean_ic": m, "std_ic": sd, "ic_ir": ir, "n_days": int(len(s))}
        )
    return pd.DataFrame(summary_rows)


def build_rolling_ic_weight_signs(
    ic_daily: pd.DataFrame,
    *,
    window: int,
    min_periods: int,
    min_abs_mean: float = 0.0,
) -> pd.DataFrame:
    """按交易日预计算各因子在打分时的权重乘子（+1 / -1）。

    在日历日 *t* 使用的符号，仅依赖 **严格早于 t** 的日度 IC：对每列先做
    ``rolling(window).mean().shift(1)``，再与 ``min_abs_mean`` 比较后取符号；
    不足窗宽、均值为 NaN、或 |均值| < min_abs_mean 时乘子为 +1（不翻转）。

    说明：日度 IC 本身已含 ``fwd_ret_5``；此处 ``shift(1)`` 避免在 *t* 使用当日 IC 行。
    """
    if ic_daily.empty or int(window) <= 0:
        return pd.DataFrame()
    w = max(1, int(window))
    mp = max(1, min(int(min_periods), w))
    thr = float(min_abs_mean)
    base = ic_daily.copy()
    base.index = pd.to_datetime(base.index, errors="coerce").normalize()
    out = pd.DataFrame(index=base.index)
    for fac in FACTOR_COLS_IC:
        col_name = f"sign_{fac}"
        if fac not in base.columns:
            out[col_name] = 1.0
            continue
        s = pd.to_numeric(base[fac], errors="coerce")
        roll = s.rolling(window=w, min_periods=mp).mean().shift(1)
        mult = pd.Series(1.0, index=base.index, dtype=float)
        valid = roll.notna()
        strong = valid & (roll.abs() >= thr)
        mult[strong & (roll < 0.0)] = -1.0
        mult[strong & (roll >= 0.0)] = 1.0
        out[col_name] = mult
    return out


def maybe_write_factor_ic_report(
    multi_data: dict[str, pd.DataFrame],
    reports_dir: str,
    *,
    enabled: bool = True,
    ic_daily_precomputed: pd.DataFrame | None = None,
) -> str | None:
    """对原始（截面处理前）因子与未来 5 日收益计算日度 Rank IC，输出 CSV。失败时仅打日志。"""
    if not enabled:
        return None
    log = get_backtest_logger()
    ic_daily = ic_daily_precomputed
    if ic_daily is None:
        ic_daily = build_ic_daily_from_multi(multi_data)
    if ic_daily.empty:
        log.warning("[因子IC] 日度 IC 为空，跳过 IC 报告。")
        return None

    summary = ic_summary_from_daily(ic_daily)
    os.makedirs(reports_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(reports_dir, f"factor_ic_summary_{ts}.csv")
    summary.to_csv(path, index=False, encoding="utf-8-sig")
    log.info("[因子IC] 已写入 %s", path)
    return path
