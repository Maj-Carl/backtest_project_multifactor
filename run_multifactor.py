"""多因子策略命令行入口，支持全量与本地冒烟两种模式。"""
import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

from backtest_main import main
from config.config import Config
from utils.logger import bootstrap_application_logging, get_backtest_logger

_PROJECT_ROOT = Path(__file__).resolve().parent


def _resolve_manual_csv(path_str: str) -> Path:
    """支持绝对路径，或以项目根目录为基准的相对路径。"""
    p = Path(path_str)
    if p.is_absolute():
        return p.resolve()
    cand = (_PROJECT_ROOT / p).resolve()
    if cand.exists():
        return cand
    cwd_cand = (Path.cwd() / p).resolve()
    if cwd_cand.exists():
        return cwd_cand
    return cand


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
    log = get_backtest_logger()
    log.info(
        "[冒烟模式] SMOKE_TEST=ON（保留在线抽样与报告生成），"
        "UNIVERSE_TOPK≤%s，日期窗约 %s 天至 END_DATE。",
        Config.UNIVERSE_TOPK,
        max(5, int(smoke_days)),
    )
    log.info("  区间: %s ~ %s", Config.DEFAULT_START_DATE, Config.DEFAULT_END_DATE)


def run(refresh_universe=False, manual_csv_path=None):
    Config.STRATEGY_NAME = "PriceVolumeMultiFactorStrategy"
    main(manual_csv_path=manual_csv_path, refresh_universe=refresh_universe)


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
    parser.add_argument(
        "--manual-csv",
        metavar="PATH",
        default=None,
        help="使用指定 CSV 作为本次股票池来源（写入 a_share_codes.csv 后再按 Config 过滤）；"
        "不设此项则仅使用缓存或在线构建",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="调试模式：分类 DEBUG 日志写入 logs/debug/*.log（与控制台无关）",
    )
    parser.add_argument(
        "--perf-cprofile",
        action="store_true",
        help="对 cerebro.run 启用 cProfile，结果写入 logs/perf_cprofile_run.txt（可能较大）",
    )
    args = parser.parse_args()
    if getattr(args, "perf_cprofile", False):
        Config.PERF_CPROFILE = True
    if getattr(args, "debug", False):
        Config.DEBUG_MODE = True
    bootstrap_application_logging(debug_mode=getattr(Config, "DEBUG_MODE", False))
    manual_csv = None
    if getattr(args, "manual_csv", None):
        resolved = _resolve_manual_csv(args.manual_csv)
        if not resolved.is_file():
            get_backtest_logger().error("找不到手动股票池文件: %s", resolved)
            sys.exit(2)
        manual_csv = str(resolved)
    if getattr(args, "smoke", False):
        _apply_smoke_mode(smoke_topk=args.smoke_topk, smoke_days=args.smoke_days)
    run(refresh_universe=args.refresh_universe, manual_csv_path=manual_csv)
