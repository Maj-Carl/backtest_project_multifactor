"""根据全 A 清单生成手动股票池模板。"""
import argparse
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def main():
    parser = argparse.ArgumentParser(description="从全A股票池生成手动策略股票池。")
    parser.add_argument("--source", default="data/universe/a_share_codes.csv", help="全A股票池CSV路径")
    parser.add_argument("--target", default="data/universe/manual_universe_template.csv", help="输出手动股票池CSV路径")
    parser.add_argument("--prefixes", default="60,00", help="保留代码前缀，逗号分隔，例如 60,00,30")
    parser.add_argument("--topk", type=int, default=300, help="输出数量上限，<=0 表示不限制")
    parser.add_argument("--keep-st", action="store_true", help="保留ST股票（默认剔除）")
    args = parser.parse_args()

    source_path = Path(args.source)
    if not source_path.is_absolute():
        source_path = PROJECT_ROOT / source_path
    target_path = Path(args.target)
    if not target_path.is_absolute():
        target_path = PROJECT_ROOT / target_path

    if not source_path.exists():
        raise FileNotFoundError(f"找不到全A股票池文件: {source_path}")

    df = pd.read_csv(source_path, dtype={"code": str}, encoding="utf-8-sig")
    if "code" not in df.columns:
        raise ValueError("源CSV缺少 code 列。")

    df["code"] = df["code"].astype(str).str.extract(r"(\d{6})", expand=False)
    df = df.dropna(subset=["code"])
    df["code"] = df["code"].astype(str).str.zfill(6)
    if "name" not in df.columns:
        df["name"] = ""
    else:
        df["name"] = df["name"].fillna("").astype(str)

    prefix_list = tuple(p.strip() for p in args.prefixes.split(",") if p.strip())
    if prefix_list:
        df = df[df["code"].str.startswith(prefix_list)]

    if not args.keep_st:
        df = df[~df["name"].str.contains("ST", na=False)]

    df = df.drop_duplicates(subset=["code"])
    df = df.sort_values(by="code")
    if args.topk and args.topk > 0:
        df = df.head(args.topk)

    out = df[["code", "name"]].copy()
    out["amount"] = 0
    out["turnover"] = 0

    target_path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(target_path, index=False, encoding="utf-8-sig")
    print(f"已生成手动股票池: {target_path}")
    print(f"数量: {len(out)}")
    print(f"前10只: {out['code'].head(10).tolist()}")


if __name__ == "__main__":
    main()
