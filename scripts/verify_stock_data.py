"""
对比接口区间数据与本地 Parquet 仓数据是否一致。
用法示例:
  python scripts/verify_stock_data.py --code 600000 --start 2025-04-15 --end 2026-04-15
"""
import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.config import Config
from data.storage.bar_store import compare_local_vs_remote


def main():
    parser = argparse.ArgumentParser(description="校验本地日线与接口是否一致")
    parser.add_argument("--code", required=True, help="股票代码，多个用逗号分隔")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--cache-dir", default=Config.MULTI_STOCK_CACHE_DIR)
    parser.add_argument("--period", default=Config.DEFAULT_PERIOD)
    parser.add_argument("--adjust", default="0")
    parser.add_argument("--ty", default="个股")
    args = parser.parse_args()

    cache_dir = Path(args.cache_dir)
    codes = [c.strip().zfill(6) for c in args.code.split(",") if c.strip()]
    all_ok = True
    for code in codes:
        r = compare_local_vs_remote(
            code,
            args.start,
            args.end,
            cache_dir=cache_dir,
            period=args.period,
            adjust=args.adjust,
            ty=args.ty,
            verbose=True,
        )
        ok = bool(r.get("ok"))
        all_ok = all_ok and ok
        print(f"=== {code} === {r}")

    raise SystemExit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
