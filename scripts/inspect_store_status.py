"""查看 DuckDB catalog 中的本地行情仓状态。"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.config import Config
from data.storage.bar_store import catalog_db_path


def main():
    cache_dir = Path(Config.MULTI_STOCK_CACHE_DIR)
    db_path = catalog_db_path(cache_dir)
    if not db_path.exists():
        print(f"未找到 catalog: {db_path}")
        return

    import duckdb

    con = duckdb.connect(str(db_path))
    total = con.execute("SELECT COUNT(*) FROM bar_catalog").fetchone()[0]
    print(f"catalog 股票数: {total}")
    rows = con.execute(
        """
        SELECT symbol, period, adjust, ty, first_date, last_date, row_count
        FROM bar_catalog
        ORDER BY symbol
        LIMIT 20
        """
    ).fetchall()
    for r in rows:
        print(r)
    con.close()


if __name__ == "__main__":
    main()
