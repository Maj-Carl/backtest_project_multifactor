"""
按 Config 配置的日期区间，用接口B（api_stock_kline_daily_th）向本地 Parquet 仓（silver）补数。

接口区分：
- 接口A（主接口）: api_stock_kline_dc，按代码+区间拉历史 K 线，常规补缺首选。
- 接口B（本脚本）: api_stock_kline_daily_th，按交易日拉全市场快照，适合批量补仓。
"""
import argparse
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd

from config.config import Config
from data.fetch.api_keys import DEFAULT_API_KEY_FILE_PATH as API_KEY_FILE_PATH
from data.fetch.api_keys import load_api_key_from_file
from data.fetch.apis.api_kline_daily_th import fetch_daily_th_market as fetch_kline_daily_th_market
from data.storage.bar_store import upsert_daily_th_snapshot_into_silver
from data.fetch.trade_calendar import get_trade_days
from data.universe.builder import build_universe_codes


def _ensure_key() -> str:
    k = load_api_key_from_file()
    if k:
        return k
    raise FileNotFoundError(f"请将 API Key 写入 {API_KEY_FILE_PATH}")


def main():
    parser = argparse.ArgumentParser(
        description="按交易日循环拉 daily_th 全市表，写入 Config.MULTI_STOCK_CACHE_DIR silver"
    )
    parser.add_argument(
        "--start",
        default=None,
        help="起始日，默认 Config.DEFAULT_START_DATE",
    )
    parser.add_argument(
        "--end",
        default=None,
        help="结束日，默认 Config.DEFAULT_END_DATE",
    )
    parser.add_argument(
        "--universe-only",
        action="store_true",
        help="仅写入与回测一致的 universe（配置见 Config），速度快于全市",
    )
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="单日接口失败时不中断，继续下一日",
    )
    parser.add_argument(
        "--max-days",
        type=int,
        default=0,
        help="调试：最多处理 N 个交易日，0 表示不限制",
    )
    parser.add_argument(
        "--prefixes",
        default="",
        help="按代码前缀过滤（逗号分隔），例如 60,00；留空表示不过滤",
    )
    args = parser.parse_args()

    start_s = args.start or Config.DEFAULT_START_DATE
    end_s = args.end or Config.DEFAULT_END_DATE
    cache_dir = Path(Config.MULTI_STOCK_CACHE_DIR)
    api_key = _ensure_key()

    code_filter = None
    if args.universe_only:
        codes = build_universe_codes(
            prefixes=Config.UNIVERSE_PREFIX,
            top_k=Config.UNIVERSE_TOPK,
            min_amount=Config.UNIVERSE_MIN_AMOUNT,
            min_turnover=Config.UNIVERSE_MIN_TURNOVER,
            use_local=Config.UNIVERSE_USE_LOCAL,
            manual_csv_path=Config.UNIVERSE_MANUAL_CSV_PATH or None,
        )
        code_filter = set(str(c).strip().zfill(6) for c in codes if str(c).strip())
        print(f"universe_only: {len(code_filter)} 只股票")

    prefix_tuple = tuple(p.strip() for p in args.prefixes.split(",") if p.strip())
    if prefix_tuple:
        print(f"prefix_filter: {prefix_tuple}")

    bdays = get_trade_days(start_s, end_s, cache_dir=cache_dir, period=Config.DEFAULT_PERIOD, ty="个股")
    if args.max_days and args.max_days > 0:
        bdays = bdays[: args.max_days]

    print(f"区间: {start_s} ~ {end_s}，共 {len(bdays)} 个交易日")
    print(f"仓目录: {cache_dir}")

    t0 = time.perf_counter()
    days_ok = 0
    for i, day in enumerate(bdays, start=1):
        ds = day.strftime("%Y-%m-%d")
        print(f"[{i}/{len(bdays)}] 拉取 {ds} …", flush=True)
        try:
            mdf = fetch_kline_daily_th_market(api_key, ds, verbose=False)
        except Exception as exc:
            print(f"  拉取失败: {exc}")
            if args.continue_on_error:
                continue
            raise

        if prefix_tuple:
            mdf = mdf[mdf["code"].astype(str).str.startswith(prefix_tuple)]
            if mdf.empty:
                print("  过滤后为空，跳过")
                continue

        stats = upsert_daily_th_snapshot_into_silver(
            cache_dir,
            mdf,
            period=Config.DEFAULT_PERIOD,
            adjust="0",
            ty="个股",
            code_filter=code_filter,
            verbose=True,
        )
        print(f"  行数 {stats['records_seen']}, 过滤跳过 {stats['skipped_filter']}, 写入标的 {stats['merged_files']}, 失败 {stats['failures']}")
        days_ok += 1

    print(f"完成。成功处理 {days_ok}/{len(bdays)} 个交易日，耗时 {time.perf_counter() - t0:.1f}s")


if __name__ == "__main__":
    main()
