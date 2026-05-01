"""多因子策略命令行入口，支持全量与本地冒烟两种模式。"""
import argparse
from datetime import datetime, timedelta

from backtest_main import main
from config.config import Config
from data.universe.builder import build_universe_codes, UNIVERSE_CACHE_FILE


SMOKE_TOPK_CAP = 5
SMOKE_CALENDAR_DAYS = 45  # 约一个半月，进一步缩短回测与补缺量


def _apply_smoke_mode(smoke_topk: int = SMOKE_TOPK_CAP, smoke_days: int = SMOKE_CALENDAR_DAYS):
    """本地小规模冒烟：保留全能力，仅缩小股票池与时间窗。"""
    Config.SMOKE_TEST = True
    Config.UNIVERSE_TOPK = min(Config.UNIVERSE_TOPK, max(1, int(smoke_topk)))
    try:
        end = datetime.strptime(Config.DEFAULT_END_DATE, "%Y-%m-%d")
        start_orig = datetime.strptime(Config.DEFAULT_START_DATE, "%Y-%m-%d")
        narrowed_start = end - timedelta(days=max(5, int(smoke_days)))
        if narrowed_start > start_orig:
            Config.DEFAULT_START_DATE = narrowed_start.strftime("%Y-%m-%d")
    except ValueError:
        pass
    print(
        "[冒烟模式] SMOKE_TEST=ON（保留在线抽样与报告生成），"
        f"UNIVERSE_TOPK≤{Config.UNIVERSE_TOPK}，日期窗约 {max(5, int(smoke_days))} 天至 END_DATE。"
    )
    print(
        f"  区间: {Config.DEFAULT_START_DATE} ~ {Config.DEFAULT_END_DATE}"
    )


def run(refresh_universe=False):
    Config.STRATEGY_NAME = "PriceVolumeMultiFactorStrategy"
    if refresh_universe:
        codes = build_universe_codes(
            prefixes=Config.UNIVERSE_PREFIX,
            top_k=Config.UNIVERSE_TOPK,
            min_amount=Config.UNIVERSE_MIN_AMOUNT,
            min_turnover=Config.UNIVERSE_MIN_TURNOVER,
            use_local=False,
            manual_csv_path=Config.UNIVERSE_MANUAL_CSV_PATH or None,
        )
        print(f"股票池已刷新: {len(codes)} 只")
    else:
        print(f"股票池缓存文件: {UNIVERSE_CACHE_FILE}")
        print("默认使用本地缓存，不存在时才抓取。")

    main()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="运行多因子回测。")
    parser.add_argument(
        "--refresh-universe",
        action="store_true",
        help="回测前强制刷新股票池（忽略本地缓存）",
    )
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="本地冒烟：小股票池、缩短回测区间（保留在线抽样与报告生成，不改 config 文件默认值）",
    )
    parser.add_argument(
        "--smoke-topk",
        type=int,
        default=SMOKE_TOPK_CAP,
        help=f"冒烟股票池上限（默认 {SMOKE_TOPK_CAP}）",
    )
    parser.add_argument(
        "--smoke-days",
        type=int,
        default=SMOKE_CALENDAR_DAYS,
        help=f"冒烟回测日历天数（默认 {SMOKE_CALENDAR_DAYS}）",
    )
    args = parser.parse_args()
    if getattr(args, "smoke", False):
        _apply_smoke_mode(smoke_topk=args.smoke_topk, smoke_days=args.smoke_days)
    run(refresh_universe=args.refresh_universe)
