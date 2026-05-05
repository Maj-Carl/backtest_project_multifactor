"""接口B：api_stock_kline_daily_th（按交易日拉全市场快照）。

字段约定（与本仓库线上核对一致）：
- volume：成交量，单位为「股」，不是「手」（1 手 = 100 股）。与接口原始列名一致，本模块不做手/股换算。

HTTP 与 ``DEFAULTS`` / CLI（避免混淆）：
- **远端** ``GET .../api_stock_kline_daily_th/`` 仅查询参数 ``key``、``date``（单日）。**没有** ``start``/``end``。
- **``DEFAULTS`` 里**：``key_file``、``cache_dir`` 为调试脚本共用；``date`` 对应 ``--mode market`` 与 HTTP 的 ``date``；
  ``code``、``start``、``end`` 仅给 ``--mode code_range`` / ``fetch_daily_th_bars_for_code`` 在本机用 ``get_trade_days``
  枚举交易日，再**逐日**调 HTTP；``start``/``end`` **不会**出现在请求 URL 上。
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

import pandas as pd
import requests

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from data.fetch.trade_calendar import get_trade_days
from utils.logger import LoggerConfig, get_backtest_logger, get_debug_logger

DAILY_TH_URL = "http://39.98.238.239/api_stock_kline_daily_th/"
_MARKET_CACHE: dict[str, pd.DataFrame] = {}
_CACHE_MAX = 128

# 本模块 ``main()`` 调试默认值。**不是**远端接口的完整参数表：HTTP 仅 ``key``+``date``（见模块说明）。
DEFAULTS = {
    "key_file": r"C:\投资\STOCK_API_KEY.txt",
    "date": "2025-04-15",  # --mode market；与 HTTP 的 date 一致
    "code": "000001",  # --mode code_range
    "start": "2025-04-15",  # code_range：仅本机日历区间起点，非 HTTP 查询参数
    "end": "2025-04-30",  # code_range：仅本机日历区间终点，非 HTTP 查询参数
    "cache_dir": r"C:\投资\STOCK_DATA",
}


def _load_default_key() -> str | None:
    p = Path(DEFAULTS["key_file"])
    if not p.exists():
        return None
    v = p.read_text(encoding="utf-8-sig").strip()
    return v or None


def _parse_th_code(raw) -> str:
    s = str(raw).strip()
    if "." in s:
        s = s.split(".", 1)[0]
    if s.isdigit():
        return s.zfill(6)
    m = re.search(r"(\d{6})", s)
    return m.group(1) if m else s


def fetch_daily_th_market(
    api_key: str,
    trade_date: str,
    *,
    verbose: bool = False,
    timeout: tuple[float, float] | None = None,
    use_cache: bool = True,
) -> pd.DataFrame:
    """拉取接口B指定交易日的全市场数据（HTTP：``key`` + ``date``）。

    返回 DataFrame 中 ``volume`` 列为当日成交量，单位：股。
    """
    if use_cache and trade_date in _MARKET_CACHE:
        return _MARKET_CACHE[trade_date]

    connect_t, read_t = timeout if timeout is not None else (5.0, 45.0)
    http_dbg = get_debug_logger("http") if LoggerConfig.DEBUG_MODE else None
    if http_dbg is not None:
        http_dbg.debug(
            "[daily_th] GET %s date=%s timeout=(%.1f,%.1f)",
            DAILY_TH_URL,
            trade_date,
            connect_t,
            read_t,
        )
    t0 = time.perf_counter()
    resp = requests.get(DAILY_TH_URL, params={"key": api_key, "date": trade_date}, timeout=(connect_t, read_t))
    resp.raise_for_status()
    payload = resp.json()
    status = payload.get("status")
    err = payload.get("error")
    if status == "失败" or (err and str(err).strip()):
        raise ValueError(f"daily_th 失败: {err or status}")

    rows = payload.get("data")
    cols = payload.get("columns")
    if not rows or not cols:
        get_backtest_logger().warning(
            "[接口B] 全日快照无表格行 date=%s（data/columns 为空或缺失）",
            trade_date,
        )
        raise ValueError("daily_th 返回空表格")
    if http_dbg is not None:
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        http_dbg.debug(
            "[daily_th] OK date=%s http=%s rows=%d cols=%d elapsed_ms=%.1f",
            trade_date,
            resp.status_code,
            len(rows),
            len(cols) if cols else 0,
            elapsed_ms,
        )

    df = pd.DataFrame(data=rows, columns=cols)
    df["code"] = df["code"].map(_parse_th_code)
    df["date"] = pd.to_datetime(df["date"])
    # volume：成交量（股）；与接口返回值一致，不转换为「手」。
    # 接口偶发返回字符串数值；与本地 float 合并写 Parquet 时需统一为数值，避免 mixed object 报错。
    for c in (
        "open",
        "high",
        "low",
        "close",
        "volume",
        "amount",
        "prev_close",
        "avg_price",
        "high_limit",
        "low_limit",
        "factor",
        "is_paused",
        "is_st",
    ):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    if "turnover_rate" in df.columns:
        df["turnover"] = pd.to_numeric(df["turnover_rate"], errors="coerce")
    elif "turnover" not in df.columns:
        df["turnover"] = 0.0

    if use_cache:
        _MARKET_CACHE[trade_date] = df
    if len(_MARKET_CACHE) > _CACHE_MAX:
        for k in list(_MARKET_CACHE.keys())[: len(_MARKET_CACHE) - _CACHE_MAX + 16]:
            _MARKET_CACHE.pop(k, None)
    return df


def fetch_daily_th_bars_for_code(
    code: str,
    start_date: str,
    end_date: str,
    api_key: str,
    *,
    verbose: bool = False,
    cache_dir: Path | None = None,
) -> pd.DataFrame:
    """按交易日拼接接口B，返回单只股票区间日线。

    ``start_date``/``end_date`` 只在本机用于 ``get_trade_days`` 枚举交易日；
    每个交易日仍调用 ``fetch_daily_th_market``（单日 HTTP），不是一次请求拉区间。

    单列合并后 ``volume`` 仍为成交量（股），按日去重、排序。
    """
    code_z = str(code).strip().zfill(6) if str(code).strip().isdigit() else str(code).strip()
    parts: list[pd.DataFrame] = []
    http_dbg = get_debug_logger("http") if LoggerConfig.DEBUG_MODE else None
    if http_dbg is not None:
        http_dbg.debug(
            "[daily_th] bars_for_code start code=%s range=%s~%s",
            code_z,
            start_date,
            end_date,
        )
    for d in get_trade_days(start_date, end_date, cache_dir=cache_dir, period="1d", ty="个股"):
        ds = d.strftime("%Y-%m-%d")
        try:
            mkt = fetch_daily_th_market(api_key, ds, verbose=verbose)
        except (ValueError, requests.RequestException, OSError) as exc:
            if verbose:
                get_backtest_logger().info("[daily_th] %s 跳过: %s", ds, exc)
            if http_dbg is not None:
                http_dbg.debug(
                    "[daily_th] bars_for_code skip date=%s code=%s err=%s",
                    ds,
                    code_z,
                    exc,
                )
            continue
        sub = mkt[mkt["code"].astype(str) == code_z]
        if not sub.empty:
            parts.append(sub)

    if not parts:
        return pd.DataFrame()
    out = pd.concat(parts, ignore_index=True)
    keep = [
        c
        for c in (
            "code",
            "date",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "amount",
            "turnover",
            "factor",
            "is_paused",
            "is_st",
            "high_limit",
            "low_limit",
            "avg_price",
            "prev_close",
        )
        if c in out.columns
    ]
    return out[keep].drop_duplicates(subset=["date"], keep="last").sort_values("date")


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="调试接口B(api_stock_kline_daily_th)。")
    p.add_argument(
        "--mode",
        choices=["market", "code_range"],
        default="market",
        help="market=单日全市场；code_range=区间内在本机按交易日循环，多日=多次 HTTP",
    )
    p.add_argument("--key", default=None, help="API key（不传则尝试读取 DEFAULTS['key_file']）")
    p.add_argument(
        "--date",
        default=None,
        help="market：交易日 YYYY-MM-DD（即 HTTP 的 date）；默认见 DEFAULTS['date']",
    )
    p.add_argument(
        "--code",
        default=None,
        help="code_range：股票代码；默认 DEFAULTS['code']（非 HTTP 字段名，仅 CLI）",
    )
    p.add_argument(
        "--start",
        default=DEFAULTS["start"],
        help="code_range：本机区间起点，用于 get_trade_days，不发给接口（默认 DEFAULTS['start']）",
    )
    p.add_argument(
        "--end",
        default=DEFAULTS["end"],
        help="code_range：本机区间终点，同上（默认 DEFAULTS['end']）",
    )
    p.add_argument(
        "--cache-dir",
        default=DEFAULTS["cache_dir"],
        help="交易日历回退时使用的本地仓目录",
    )
    return p


def main():
    args = _build_parser().parse_args()
    key = args.key or _load_default_key()
    if not key:
        raise SystemExit(f"缺少 API key。请传 --key 或在 {DEFAULTS['key_file']} 写入 key")

    date = args.date or DEFAULTS["date"]
    code = args.code or DEFAULTS["code"]
    cache_dir = Path(args.cache_dir) if args.cache_dir else None
    print(f"default_config={DEFAULTS}")

    if args.mode == "market":
        df = fetch_daily_th_market(key, date, verbose=True)
        print(f"http_api={DAILY_TH_URL}")
        print(f"rows={len(df)}")
        print(f"columns={list(df.columns)}")
        if not df.empty:
            print(df.head(5).to_string(index=False))
        return

    df = fetch_daily_th_bars_for_code(
        code,
        args.start,
        args.end,
        key,
        verbose=True,
        cache_dir=cache_dir,
    )
    print(f"http_api={DAILY_TH_URL}")
    print(f"rows={len(df)}")
    print(f"columns={list(df.columns)}")
    if not df.empty:
        print(df.head(5).to_string(index=False))


if __name__ == "__main__":
    main()
