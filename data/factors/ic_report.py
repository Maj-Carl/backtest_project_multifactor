"""因子 Rank IC（Spearman）与 IC_IR 摘要；可选按全市场截面 IC 符号对齐策略权重。"""

from __future__ import annotations

import math
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

# 与 strategies.PriceVolumeMultiFactorStrategy 中 w_* 参数名一致
FACTOR_TO_WEIGHT_PARAM: dict[str, str] = {
    "mom20": "w_mom20",
    "mom60": "w_mom60",
    "vol20": "w_vol20",
    "liq20": "w_liq20",
    "rev20": "w_rev20",
    "dvol20": "w_dvol20",
    "amihud20": "w_amihud20",
}


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


def truncate_ic_daily_for_align(ic_daily: pd.DataFrame, prefix_ratio: float) -> pd.DataFrame:
    """仅用前若干交易日的日度 IC 估计 mean_ic，prefix_ratio=1 为全样本。"""
    if ic_daily.empty:
        return ic_daily
    r = float(prefix_ratio)
    if r >= 1.0 - 1e-12:
        return ic_daily
    r = max(1e-6, min(1.0, r))
    n = max(30, int(len(ic_daily) * r))
    n = min(n, len(ic_daily))
    return ic_daily.iloc[:n]


def align_strategy_weights_by_ic_summary(
    base_params: dict,
    summary: pd.DataFrame | None,
    *,
    min_days: int = 40,
    min_abs_mean: float = 0.0,
) -> tuple[dict, dict[str, float]]:
    """按各因子 mean_ic 符号调整 w_*：mean_ic>0 保持，mean_ic<0 权重取反；无效则保持原权重。

    返回 (新参数字典, 各因子实际乘的 sign，仅含被调整因子)。
    """
    out = dict(base_params)
    signs_applied: dict[str, float] = {}
    if summary is None or summary.empty:
        return out, signs_applied
    for _, row in summary.iterrows():
        fac = str(row.get("factor", ""))
        param = FACTOR_TO_WEIGHT_PARAM.get(fac)
        if param is None or param not in out:
            continue
        n = int(row.get("n_days") or 0)
        m = row.get("mean_ic")
        if n < min_days or m is None or (isinstance(m, float) and math.isnan(m)):
            continue
        mf = float(m)
        if abs(mf) < float(min_abs_mean):
            continue
        sgn = 1.0 if mf >= 0.0 else -1.0
        if sgn < 0:
            signs_applied[fac] = sgn
            out[param] = float(out[param]) * sgn
    return out, signs_applied


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
