"""生成全 A 股票代码清单并写入本地缓存。"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from data.universe.builder import UNIVERSE_CACHE_FILE, build_universe_codes


def main():
    codes = build_universe_codes(
        prefixes=("60", "00", "30"),
        top_k=None,
        min_amount=0,
        min_turnover=0,
        use_local=False,
    )
    print(f"已生成A股代码清单: {len(codes)}")
    print(f"文件位置: {UNIVERSE_CACHE_FILE}")
    print(f"示例: {codes[:10]}")


if __name__ == "__main__":
    main()
