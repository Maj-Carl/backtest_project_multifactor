"""批量预抓取股票池数据到本地 Parquet 仓。"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.config import Config
from data.orchestration.batch_symbols import get_multiple_stock_data
from data.universe.builder import build_universe_codes


def main():
    codes = build_universe_codes(
        prefixes=Config.UNIVERSE_PREFIX,
        top_k=3041,
        min_amount=0,
        min_turnover=0,
        use_local=True,
        manual_csv_path=None,
    )
    print(f"待预抓取代码数量: {len(codes)}")

    data_map = get_multiple_stock_data(
        codes=codes,
        period=Config.DEFAULT_PERIOD,
        start_date=Config.DEFAULT_START_DATE,
        end_date=Config.DEFAULT_END_DATE,
        adjust="0",
        ty="个股",
        use_local=True,
        verbose=True,
        cache_dir_path=Config.MULTI_STOCK_CACHE_DIR,
        continue_on_error=True,
        sampling_check_enabled=False,
    )
    print(f"完成缓存数量: {len(data_map)}")
    print(f"缓存目录: {Config.MULTI_STOCK_CACHE_DIR}")


if __name__ == "__main__":
    main()
