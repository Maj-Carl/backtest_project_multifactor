"""股票池构建模块：从多源拉取并过滤可交易股票清单。"""

import math
import time
from pathlib import Path

import pandas as pd
import requests

from utils.logger import get_debug_logger

# 回测使用的股票池清单默认读写此文件（与 Config 无第二套路径）。
UNIVERSE_CACHE_FILE = Path(__file__).resolve().parent / "a_share_codes.csv"
EASTMONEY_API = "https://push2.eastmoney.com/api/qt/clist/get"
FALLBACK_CODES = [
    "600000", "600036", "600519", "600276", "600031",
    "600887", "600309", "600905", "601166", "601318",
    "601688", "601888", "601899", "601985", "601857",
    "000001", "000002", "000063", "000333", "000651",
    "000725", "000858", "000938", "000977", "002415",
]


def _fetch_all_a_codes_by_akshare():
    try:
        import akshare as ak
    except ImportError:
        return None

    try:
        spot_df = ak.stock_info_a_code_name()
    except Exception:
        return None

    if spot_df is None or spot_df.empty:
        return None

    code_col = "code" if "code" in spot_df.columns else "symbol" if "symbol" in spot_df.columns else None
    name_col = "name" if "name" in spot_df.columns else None
    if code_col is None:
        return None

    out = pd.DataFrame()
    out["code"] = spot_df[code_col].astype(str).str.extract(r"(\d{6})", expand=False)
    out["name"] = spot_df[name_col].astype(str) if name_col else ""
    out["amount"] = 0.0
    out["turnover"] = 0.0
    out = out.dropna(subset=["code"])
    out["code"] = out["code"].astype(str).str.zfill(6)
    out = out.drop_duplicates(subset=["code"])
    return out


def _normalize_code_frame(df):
    normalized = df.copy()
    lowered = {col.lower(): col for col in normalized.columns}
    if "code" not in lowered:
        raise ValueError("股票池CSV缺少 code 列。")
    code_col = lowered["code"]
    normalized["code"] = normalized[code_col].astype(str).str.extract(r"(\d{6})", expand=False)
    normalized = normalized.dropna(subset=["code"])
    normalized["code"] = normalized["code"].astype(str).str.zfill(6)

    if "name" in lowered:
        normalized["name"] = normalized[lowered["name"]].fillna("").astype(str)
    else:
        normalized["name"] = ""
    if "amount" in lowered:
        normalized["amount"] = pd.to_numeric(normalized[lowered["amount"]], errors="coerce").fillna(0)
    else:
        normalized["amount"] = 0.0
    if "turnover" in lowered:
        normalized["turnover"] = pd.to_numeric(normalized[lowered["turnover"]], errors="coerce").fillna(0)
    else:
        normalized["turnover"] = 0.0

    return normalized[["code", "name", "amount", "turnover"]].drop_duplicates(subset=["code"])


def _fetch_page(page_num, page_size=500):
    params = {
        "pn": page_num,
        "pz": page_size,
        "po": 1,
        "np": 1,
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
        "fltt": 2,
        "invt": 2,
        "fid": "f6",
        "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",
        "fields": "f12,f14,f6,f8",
    }
    last_error = None
    for attempt in range(1, 4):
        try:
            resp = requests.get(
                EASTMONEY_API,
                params=params,
                timeout=(5, 15),
                headers={"User-Agent": "Mozilla/5.0"},
            )
            resp.raise_for_status()
            payload = resp.json()
            return payload.get("data") or {}
        except requests.RequestException as exc:
            last_error = exc
            if attempt < 3:
                time.sleep(0.8)
    raise RuntimeError(f"抓取股票池分页失败: page={page_num}, error={last_error}")


def build_universe_codes(
    prefixes=("60", "00"),
    top_k=300,
    min_amount=100000000,
    min_turnover=0.5,
    use_local=True,
    manual_csv_path=None,
):
    """manual_csv_path 仅适用于 ``run_multifactor.py --manual-csv``；勿在 Config 中配置隐式路径。"""
    du = get_debug_logger("universe")
    du.debug(
        "build_universe_codes enter prefixes=%s top_k=%s use_local=%s manual_csv_path=%s",
        prefixes,
        top_k,
        use_local,
        manual_csv_path,
    )
    UNIVERSE_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)

    manual_path = Path(manual_csv_path) if manual_csv_path else None
    if manual_path and manual_path.exists():
        manual_df = pd.read_csv(manual_path, dtype=str, encoding="utf-8-sig")
        df = _normalize_code_frame(manual_df)
        df.to_csv(UNIVERSE_CACHE_FILE, index=False, encoding="utf-8-sig")
    elif use_local and UNIVERSE_CACHE_FILE.exists():
        df = pd.read_csv(UNIVERSE_CACHE_FILE, dtype={"code": str}, encoding="utf-8-sig")
        df = _normalize_code_frame(df)
    else:
        try:
            ak_df = _fetch_all_a_codes_by_akshare()
            if ak_df is not None and not ak_df.empty:
                df = _normalize_code_frame(ak_df)
                df.to_csv(UNIVERSE_CACHE_FILE, index=False, encoding="utf-8-sig")
            else:
                first_page = _fetch_page(1, 500)
                total = int(first_page.get("total", 0))
                diff = first_page.get("diff", []) or []
                pages = max(1, math.ceil(total / 500)) if total else 1

                rows = []
                for item in diff:
                    rows.append(item)

                target_rows = max(top_k * 4, top_k + 200) if top_k else pages * 500
                for page in range(2, pages + 1):
                    page_data = _fetch_page(page, 500)
                    for item in page_data.get("diff", []) or []:
                        rows.append(item)
                    if len(rows) >= target_rows:
                        break

                df = pd.DataFrame(rows)
                df.rename(
                    columns={
                        "f12": "code",
                        "f14": "name",
                        "f6": "amount",
                        "f8": "turnover",
                    },
                    inplace=True,
                )
                if "code" not in df.columns:
                    raise RuntimeError("抓取股票池失败：返回数据缺少 code 字段。")
                df = _normalize_code_frame(df)
                df.to_csv(UNIVERSE_CACHE_FILE, index=False, encoding="utf-8-sig")
        except Exception:
            if UNIVERSE_CACHE_FILE.exists():
                df = pd.read_csv(UNIVERSE_CACHE_FILE, dtype={"code": str}, encoding="utf-8-sig")
                df = _normalize_code_frame(df)
            else:
                fallback_df = pd.DataFrame({"code": FALLBACK_CODES, "name": "", "amount": 0, "turnover": 0})
                df = _normalize_code_frame(fallback_df)
                df.to_csv(UNIVERSE_CACHE_FILE, index=False, encoding="utf-8-sig")

    df["code"] = df["code"].astype(str).str.zfill(6)
    df = df[df["code"].str.startswith(tuple(prefixes))]
    if "name" in df.columns:
        df = df[~df["name"].str.contains("ST", na=False)]
    if "amount" in df.columns and (df["amount"] > 0).any():
        df = df[df["amount"] >= min_amount]
    if "turnover" in df.columns and (df["turnover"] > 0).any():
        df = df[df["turnover"] >= min_turnover]

    df = df.sort_values(by=["amount", "turnover"], ascending=False)
    if top_k:
        df = df.head(top_k)
    codes = df["code"].tolist()
    du.debug("build_universe_codes exit n=%s sample=%s", len(codes), codes[:12])
    return codes
