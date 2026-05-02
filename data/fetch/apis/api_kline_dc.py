"""接口A：api_stock_kline_dc（按代码+区间拉历史K线）。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd
import requests

from utils.logger import get_backtest_logger

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

API_URL = "http://39.98.238.239/api_stock_kline_dc/"
DEFAULTS = {
    "key_file": r"C:\投资\STOCK_API_KE.txt",
    "codes": "000001",
    "period": "1d",
    "start": "2025-04-15",
    "end": "2025-04-30",
    "adjust": "0",
    "ty": "个股",
}


def _load_default_key() -> str | None:
    p = Path(DEFAULTS["key_file"])
    if not p.exists():
        return None
    v = p.read_text(encoding="utf-8-sig").strip()
    return v or None


def fetch_kline_dc_payload(payload: dict, *, verbose: bool = True, max_retries: int = 3) -> dict:
    """请求接口A并返回原始 JSON 载荷。"""
    data = None
    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.post(API_URL, data=payload, timeout=(5, 20))
            resp.raise_for_status()
            data = resp.json()
            break
        except requests.exceptions.Timeout as exc:
            last_error = exc
            if verbose:
                get_backtest_logger().info(
                    "[api_kline_dc] 请求超时，重试中 (%s/%s)...", attempt, max_retries
                )
        except requests.exceptions.RequestException as exc:
            last_error = exc
            if verbose:
                get_backtest_logger().info(
                    "[api_kline_dc] 请求失败，重试中 (%s/%s)...", attempt, max_retries
                )

    if data is None:
        raise ConnectionError(f"获取股票数据失败: {last_error}")
    return data


def fetch_kline_dc_nonempty_payload(payload: dict, *, verbose: bool = True, max_retries: int = 3) -> dict:
    """与 fetch_kline_dc_payload 相同，但 data 为空时抛出 ValueError。"""
    data = fetch_kline_dc_payload(payload, verbose=verbose, max_retries=max_retries)
    if not data.get("data"):
        raise ValueError("远程接口返回空数据，请检查参数或时间区间。")
    return data


def fetch_kline_dc_dataframe(payload: dict, *, verbose: bool = True, max_retries: int = 3) -> pd.DataFrame:
    """请求接口A并转为 DataFrame（不做业务字段归一）。"""
    data = fetch_kline_dc_payload(payload, verbose=verbose, max_retries=max_retries)
    rows = data.get("data")
    cols = data.get("columns")
    if not rows or not cols:
        return pd.DataFrame()
    return pd.DataFrame(data=rows, columns=cols)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="调试接口A(api_stock_kline_dc)。")
    p.add_argument("--key", default=None, help="API key（不传则尝试读取 DEFAULTS['key_file']）")
    p.add_argument("--codes", default=DEFAULTS["codes"], help="代码（支持 | 分隔）")
    p.add_argument("--period", default=DEFAULTS["period"])
    p.add_argument("--start", default=DEFAULTS["start"])
    p.add_argument("--end", default=DEFAULTS["end"])
    p.add_argument("--adjust", default=DEFAULTS["adjust"])
    p.add_argument("--ty", default=DEFAULTS["ty"])
    return p


def main():
    args = _build_parser().parse_args()
    key = args.key or _load_default_key()
    if not key:
        raise SystemExit(f"缺少 API key。请传 --key 或在 {DEFAULTS['key_file']} 写入 key")
    payload = {
        "key": key,
        "codes": args.codes,
        "period": args.period,
        "start_date": args.start,
        "end_date": args.end,
        "adjust": args.adjust,
        "ty": args.ty,
    }
    df = fetch_kline_dc_dataframe(payload, verbose=True)
    print(f"default_config={DEFAULTS}")
    print(f"http_api={API_URL}")
    print(f"rows={len(df)}")
    print(f"columns={list(df.columns)}")
    if not df.empty:
        print(df.head(5).to_string(index=False))


if __name__ == "__main__":
    main()
