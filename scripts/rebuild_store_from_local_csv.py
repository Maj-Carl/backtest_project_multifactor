"""清空并重建本地 STOCK_DATA：从本地 CSV 全量导入，保留 A-P 全字段。"""
import argparse
import shutil
from datetime import datetime
from pathlib import Path

import duckdb
import pandas as pd

CSV_USECOLS = [
    "股票代码",
    "日期",
    "开盘价",
    "最高价",
    "最低价",
    "收盘价",
    "成交量(股)",
    "成交额(元)",
    "换手率(%)",
    "涨停价",
    "跌停价",
    "均价",
    "前交易日收盘价",
    "是否停牌",
    "是否ST",
    "复权因子",
]

RENAME_MAP = {
    "股票代码": "code",
    "日期": "date",
    "开盘价": "open",
    "最高价": "high",
    "最低价": "low",
    "收盘价": "close",
    "成交量(股)": "volume",
    "成交额(元)": "amount",
    "换手率(%)": "turnover",
    "涨停价": "high_limit",
    "跌停价": "low_limit",
    "均价": "avg_price",
    "前交易日收盘价": "prev_close",
    "是否停牌": "is_paused",
    "是否ST": "is_st",
    "复权因子": "factor",
}


def _normalize_code(s: pd.Series) -> pd.Series:
    out = s.astype(str).str.strip()
    out = out.str.replace(".SZ", "", regex=False).str.replace(".SH", "", regex=False)
    out = out.str.replace(".BJ", "", regex=False)
    return out.str.extract(r"(\d{6})", expand=False)


def _prepare_catalog(db_path: Path):
    con = duckdb.connect(str(db_path))
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS bar_catalog (
            symbol VARCHAR,
            period VARCHAR,
            adjust VARCHAR,
            ty VARCHAR,
            path VARCHAR,
            first_date DATE,
            last_date DATE,
            row_count BIGINT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(symbol, period, adjust, ty)
        );
        """
    )
    con.close()


def main():
    parser = argparse.ArgumentParser(description="从本地 CSV 重建 STOCK_DATA")
    parser.add_argument("--source-dir", required=True, help="本地CSV目录")
    parser.add_argument("--target-dir", required=True, help="目标仓目录（如 C:\\投资\\STOCK_DATA）")
    parser.add_argument("--no-backup", action="store_true", help="不备份旧目录，直接清空")
    args = parser.parse_args()

    source_dir = Path(args.source_dir)
    target_dir = Path(args.target_dir)
    silver_dir = target_dir / "silver"
    db_path = target_dir / "catalog.duckdb"

    csv_files = sorted(source_dir.glob("*.csv"))
    if not csv_files:
        raise FileNotFoundError(f"未找到 CSV 文件: {source_dir}")

    if target_dir.exists():
        if args.no_backup:
            shutil.rmtree(target_dir)
        else:
            backup_dir = target_dir.parent / f"{target_dir.name}_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            shutil.move(str(target_dir), str(backup_dir))
            print(f"已备份旧目录 -> {backup_dir}")

    silver_dir.mkdir(parents=True, exist_ok=True)
    _prepare_catalog(db_path)

    all_frames = []
    for i, fp in enumerate(csv_files, start=1):
        print(f"[{i}/{len(csv_files)}] 读取 {fp.name}")
        df = pd.read_csv(fp, usecols=CSV_USECOLS, encoding="utf-8-sig")
        df = df.rename(columns=RENAME_MAP)
        df["code"] = _normalize_code(df["code"])
        df = df.dropna(subset=["code", "date"])
        df["date"] = pd.to_datetime(df["date"])
        for c in ["open", "high", "low", "close", "volume", "amount", "turnover", "high_limit", "low_limit", "avg_price", "prev_close", "factor"]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        for c in ["is_paused", "is_st"]:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0).astype(int)
        all_frames.append(df)

    merged = pd.concat(all_frames, ignore_index=True)
    merged = merged.drop_duplicates(subset=["code", "date"], keep="last")
    merged = merged.sort_values(["code", "date"])
    print(f"合并后总行数: {len(merged)}")

    con = duckdb.connect(str(db_path))
    n_codes = 0
    for code, grp in merged.groupby("code", sort=True):
        out_path = silver_dir / f"{code}_1d_0_个股.parquet"
        grp.to_parquet(out_path, index=False)
        con.execute(
            """
            INSERT OR REPLACE INTO bar_catalog
            (symbol, period, adjust, ty, path, first_date, last_date, row_count, updated_at)
            VALUES (?, '1d', '0', '个股', ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            [
                code,
                str(out_path),
                pd.to_datetime(grp["date"]).min().date(),
                pd.to_datetime(grp["date"]).max().date(),
                int(len(grp)),
            ],
        )
        n_codes += 1
        if n_codes % 200 == 0:
            print(f"已写入 {n_codes} 只股票")
    con.close()
    print(f"完成：写入 {n_codes} 只股票，目录 {silver_dir}")


if __name__ == "__main__":
    main()
