"""
本地行情仓库（优化版）：
- Parquet 作为主存储（更快、更省空间）
- DuckDB 作为元数据目录（catalog）
- 本地优先，不足则自动补缺（前补/后补/中间断档）
"""
from __future__ import annotations

import os
import random
import time
import uuid
from pathlib import Path
from typing import Optional, Set, Tuple

import pandas as pd
import requests

from data.fetch.api_keys import load_api_key
from data.fetch.apis.api_kline_daily_th import fetch_daily_th_bars_for_code
from data.fetch.apis.api_kline_dc import fetch_kline_dc_nonempty_payload
from data.fetch.trade_calendar import get_trade_days
from data.storage.column_normalize import normalize_legacy_columns

try:
    import duckdb
except ImportError:  # pragma: no cover
    duckdb = None


def _safe_ty(ty: str) -> str:
    return ty.replace("/", "_")


def _normalize_symbol(code: str) -> str:
    s = str(code).strip()
    return s.zfill(6) if s.isdigit() else s


def canonical_bar_path(cache_dir: Path, code: str, period: str, adjust: str, ty: str) -> Path:
    code = _normalize_symbol(code)
    return cache_dir / "silver" / f"{code}_{period}_{adjust}_{_safe_ty(ty)}.parquet"


def catalog_db_path(cache_dir: Path) -> Path:
    return cache_dir / "catalog.duckdb"


def _ensure_catalog(cache_dir: Path):
    if duckdb is None:
        return
    db_path = catalog_db_path(cache_dir)
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
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS ingest_runs (
            run_id VARCHAR PRIMARY KEY,
            symbol VARCHAR,
            period VARCHAR,
            adjust VARCHAR,
            ty VARCHAR,
            request_start DATE,
            request_end DATE,
            status VARCHAR,
            message VARCHAR,
            rows_after BIGINT,
            started_at TIMESTAMP,
            finished_at TIMESTAMP
        );
        """
    )
    con.close()


def _update_catalog(cache_dir: Path, code: str, period: str, adjust: str, ty: str, path: Path, df: pd.DataFrame):
    if duckdb is None or df.empty:
        return
    _ensure_catalog(cache_dir)
    con = duckdb.connect(str(catalog_db_path(cache_dir)))
    first_date = pd.to_datetime(df["date"]).min().date()
    last_date = pd.to_datetime(df["date"]).max().date()
    row_count = int(len(df))
    con.execute(
        """
        INSERT OR REPLACE INTO bar_catalog
        (symbol, period, adjust, ty, path, first_date, last_date, row_count, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        [_normalize_symbol(code), period, adjust, ty, str(path), first_date, last_date, row_count],
    )
    con.close()


def _log_ingest_run(
    cache_dir: Path,
    *,
    run_id: str,
    code: str,
    period: str,
    adjust: str,
    ty: str,
    request_start: str,
    request_end: str,
    status: str,
    message: str,
    rows_after: int,
):
    if duckdb is None:
        return
    _ensure_catalog(cache_dir)
    con = duckdb.connect(str(catalog_db_path(cache_dir)))
    con.execute(
        """
        INSERT OR REPLACE INTO ingest_runs
        (run_id, symbol, period, adjust, ty, request_start, request_end, status, message, rows_after, started_at, finished_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """,
        [
            run_id,
            _normalize_symbol(code),
            period,
            adjust,
            ty,
            request_start,
            request_end,
            status,
            message[:5000] if message else "",
            int(rows_after),
        ],
    )
    con.close()


def _acquire_symbol_lock(cache_dir: Path, code: str, period: str, adjust: str, ty: str) -> Path:
    lock_dir = cache_dir / "locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / f"{_normalize_symbol(code)}_{period}_{adjust}_{_safe_ty(ty)}.lock"
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode("utf-8"))
        os.close(fd)
    except FileExistsError as exc:
        raise RuntimeError(f"[{_normalize_symbol(code)}] 正在被其他进程写入，稍后重试。") from exc
    return lock_path


def _release_symbol_lock(lock_path: Optional[Path]):
    if lock_path is None:
        return
    try:
        if lock_path.exists():
            lock_path.unlink()
    except OSError:
        pass


def _read_bars_parquet(path: Path) -> pd.DataFrame:
    df = pd.read_parquet(path)
    if "date" not in df.columns:
        raise ValueError(f"Parquet 缺少 date 列: {path}")
    df["date"] = pd.to_datetime(df["date"])
    if "code" in df.columns:
        df["code"] = df["code"].astype(str).str.zfill(6)
    df = df.drop_duplicates(subset=["date"], keep="last")
    df.sort_values("date", inplace=True)
    return df


def _get_expected_trade_days(
    cache_dir: Path,
    period: str,
    ty: str,
    req_start: pd.Timestamp,
    req_end: pd.Timestamp,
) -> pd.DatetimeIndex:
    return get_trade_days(
        req_start,
        req_end,
        cache_dir=cache_dir,
        period=period,
        ty=ty,
    )


def _fetch_slice_remote(
    code: str,
    start_date: str,
    end_date: str,
    api_key: str,
    period: str,
    adjust: str,
    ty: str,
    verbose: bool,
    cache_dir: Optional[Path] = None,
) -> pd.DataFrame:
    if period == "1d" and ty == "个股":
        if verbose:
            print(f"[{_normalize_symbol(code)}] 使用 daily_th 补缺 {start_date}~{end_date}")
        df = fetch_daily_th_bars_for_code(
            _normalize_symbol(code),
            start_date,
            end_date,
            api_key,
            verbose=verbose,
            cache_dir=cache_dir,
        )
        if df.empty:
            return pd.DataFrame()
        df = normalize_legacy_columns(df)
        if "code" in df.columns:
            df["code"] = df["code"].astype(str).str.zfill(6)
        return df.drop_duplicates(subset=["date"], keep="last").sort_values("date")

    payload = {
        "key": api_key,
        "codes": code,
        "period": period,
        "start_date": start_date,
        "end_date": end_date,
        "adjust": adjust,
        "ty": ty,
    }
    try:
        data = fetch_kline_dc_nonempty_payload(payload, verbose=verbose, max_retries=3)
    except ValueError:
        raise
    else:
        df = pd.DataFrame(data=data["data"], columns=data["columns"])
    df = normalize_legacy_columns(df)
    if "code" in df.columns:
        df["code"] = df["code"].astype(str).str.zfill(6)
    df = df.drop_duplicates(subset=["date"], keep="last")
    df.sort_values("date", inplace=True)
    return df


def _fetch_slice_remote_once(
    code: str,
    start_date: str,
    end_date: str,
    api_key: str,
    period: str,
    adjust: str,
    ty: str,
    timeout_s: float,
    cache_dir: Optional[Path] = None,
) -> pd.DataFrame:
    if period == "1d" and ty == "个股":
        df = fetch_daily_th_bars_for_code(
            _normalize_symbol(code),
            start_date,
            end_date,
            api_key,
            verbose=False,
            cache_dir=cache_dir,
        )
        if df.empty:
            return pd.DataFrame()
        if "code" in df.columns:
            df["code"] = df["code"].astype(str).map(_normalize_symbol)
        return df.drop_duplicates(subset=["date"], keep="last").sort_values("date")

    payload = {
        "key": api_key,
        "codes": code,
        "period": period,
        "start_date": start_date,
        "end_date": end_date,
        "adjust": adjust,
        "ty": ty,
    }
    resp = requests.post(
        url="http://39.98.238.239/api_stock_kline_dc/",
        data=payload,
        timeout=(3, max(1.0, float(timeout_s))),
    )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("data"):
        return pd.DataFrame()
    df = pd.DataFrame(data=data["data"], columns=data["columns"])
    df = normalize_legacy_columns(df)
    if "code" in df.columns:
        df["code"] = df["code"].astype(str).map(_normalize_symbol)
    return df.drop_duplicates(subset=["date"], keep="last").sort_values("date")


def _ensure_api_key(key: Optional[str]) -> str:
    k = key or load_api_key()
    if not k:
        raise ValueError("缺少 API Key。")
    return k


def _apply_adjust_to_ohlc(df: pd.DataFrame, adjust: str) -> pd.DataFrame:
    if str(adjust) == "0":
        return df
    if str(adjust) not in {"1", "2"}:
        return df
    if "factor" not in df.columns:
        return df

    out = df.copy()
    fac = pd.to_numeric(out["factor"], errors="coerce")
    if fac.dropna().empty:
        return out

    if str(adjust) == "2":
        mul = fac
    else:
        latest = fac.dropna().iloc[-1]
        if latest == 0 or pd.isna(latest):
            return out
        mul = fac / latest

    for c in ("open", "high", "low", "close"):
        if c in out.columns:
            out[c] = pd.to_numeric(out[c], errors="coerce") * mul
    return out


def load_or_update_bars(
    code: str,
    start_date: str,
    end_date: str,
    *,
    cache_dir: Path,
    period: str = "1d",
    adjust: str = "0",
    ty: str = "个股",
    key: Optional[str] = None,
    use_local: bool = True,
    verbose: bool = True,
) -> pd.DataFrame:
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "silver").mkdir(parents=True, exist_ok=True)
    requested_adjust = str(adjust)
    storage_adjust = "0" if (period == "1d" and ty == "个股") else requested_adjust
    canon = canonical_bar_path(cache_dir, code, period, storage_adjust, ty)
    code_z = _normalize_symbol(code)
    run_id = uuid.uuid4().hex
    lock_path: Optional[Path] = None

    req_start = pd.Timestamp(start_date)
    req_end = pd.Timestamp(end_date)

    merged: Optional[pd.DataFrame] = None

    try:
        lock_path = _acquire_symbol_lock(cache_dir, code_z, period, storage_adjust, ty)

        if use_local and canon.exists():
            merged = _read_bars_parquet(canon)
            if "code" in merged.columns:
                merged = merged[merged["code"].astype(str).map(_normalize_symbol) == code_z]

        api_key = _ensure_api_key(key)

        def need_fetch_segments(local: Optional[pd.DataFrame]) -> list[Tuple[pd.Timestamp, pd.Timestamp]]:
            if local is None or local.empty:
                return [(req_start, req_end)]

            local_in_range = local[(local["date"] >= req_start) & (local["date"] <= req_end)].copy()
            have_dates = set(pd.to_datetime(local_in_range["date"]).dt.normalize().tolist())

            expected = _get_expected_trade_days(cache_dir, period, ty, req_start, req_end)
            missing = [d for d in expected if d.normalize() not in have_dates]
            if not missing:
                return []

            segs: list[Tuple[pd.Timestamp, pd.Timestamp]] = []
            seg_start = missing[0]
            prev = missing[0]
            for d in missing[1:]:
                if (d - prev).days <= 3:
                    prev = d
                    continue
                segs.append((seg_start, prev))
                seg_start = d
                prev = d
            segs.append((seg_start, prev))
            return segs

        segments = need_fetch_segments(merged)

        for seg_start, seg_end in segments:
            if seg_start > seg_end:
                continue
            if verbose:
                print(f"[{code_z}] 向接口补缺: {seg_start.date()} ~ {seg_end.date()}")
            chunk = _fetch_slice_remote(
                code_z,
                seg_start.strftime("%Y-%m-%d"),
                seg_end.strftime("%Y-%m-%d"),
                api_key,
                period,
                storage_adjust,
                ty,
                verbose,
            )
            if chunk.empty:
                continue
            if merged is None:
                merged = chunk
            else:
                merged = pd.concat([merged, chunk], ignore_index=True)

        if merged is None or merged.empty:
            raise ValueError(f"[{code_z}] 无本地数据且接口未返回数据。")

        merged = merged.drop_duplicates(subset=["date"], keep="last").sort_values("date")
        tmp_path = canon.with_suffix(".parquet.tmp")
        merged.to_parquet(tmp_path, index=False)
        os.replace(tmp_path, canon)
        _update_catalog(cache_dir, code_z, period, storage_adjust, ty, canon, merged)
        if verbose:
            print(f"[{code_z}] 已更新本地档案: {canon} （共 {len(merged)} 行）")

        out = merged[(merged["date"] >= req_start) & (merged["date"] <= req_end)].copy()
        if out.empty:
            raise ValueError(f"[{code_z}] 合并后仍无数据落在请求区间 {start_date}~{end_date}。")
        _log_ingest_run(
            cache_dir,
            run_id=run_id,
            code=code_z,
            period=period,
            adjust=storage_adjust,
            ty=ty,
            request_start=start_date,
            request_end=end_date,
            status="success",
            message="",
            rows_after=len(merged),
        )
        return _apply_adjust_to_ohlc(out, requested_adjust)
    except Exception as exc:
        _log_ingest_run(
            cache_dir,
            run_id=run_id,
            code=code_z,
            period=period,
            adjust=storage_adjust,
            ty=ty,
            request_start=start_date,
            request_end=end_date,
            status="failed",
            message=str(exc),
            rows_after=len(merged) if merged is not None else 0,
        )
        raise
    finally:
        _release_symbol_lock(lock_path)


def upsert_daily_th_snapshot_into_silver(
    cache_dir: Path,
    market_df: pd.DataFrame,
    *,
    period: str = "1d",
    adjust: str = "0",
    ty: str = "个股",
    code_filter: Optional[Set[str]] = None,
    verbose: bool = False,
) -> dict:
    if market_df.empty:
        return {"records_seen": 0, "skipped_filter": 0, "merged_files": 0, "failures": 0}

    work = market_df.copy()
    if "code" not in work.columns or "date" not in work.columns:
        raise ValueError("market_df 需包含 code、date 列")
    work["code"] = work["code"].astype(str).map(_normalize_symbol)
    work["date"] = pd.to_datetime(work["date"])

    counts = {"records_seen": int(len(work)), "skipped_filter": 0, "merged_files": 0, "failures": 0}

    for code_z, grp in work.groupby("code"):
        if code_filter is not None and code_z not in code_filter:
            counts["skipped_filter"] += int(len(grp))
            continue

        row_df = grp.sort_values("date").tail(1)
        lock_path: Optional[Path] = None
        try:
            lock_path = _acquire_symbol_lock(cache_dir, code_z, period, adjust, ty)
            canon = canonical_bar_path(cache_dir, code_z, period, adjust, ty)
            if canon.exists():
                merged = _read_bars_parquet(canon)
                if "code" in merged.columns:
                    merged = merged[merged["code"].astype(str).map(_normalize_symbol) == code_z]
            else:
                merged = pd.DataFrame()

            incoming = row_df.copy()
            if merged is not None and not merged.empty:
                incoming = incoming.reindex(columns=merged.columns)
            else:
                incoming["code"] = code_z
                base_cols = [
                    "date",
                    "code",
                    "open",
                    "high",
                    "low",
                    "close",
                    "volume",
                    "amount",
                    "turnover",
                ]
                use_cols = [c for c in base_cols if c in incoming.columns]
                incoming = incoming[use_cols]

            if merged is None or merged.empty:
                combined = incoming
            else:
                combined = pd.concat([merged, incoming], ignore_index=True, sort=False)

            combined = combined.drop_duplicates(subset=["date"], keep="last").sort_values("date")
            tmp_path = canon.with_suffix(".parquet.tmp")
            combined.to_parquet(tmp_path, index=False)
            os.replace(tmp_path, canon)
            _update_catalog(cache_dir, code_z, period, adjust, ty, canon, combined)
            counts["merged_files"] += 1
        except Exception as exc:
            counts["failures"] += 1
            if verbose:
                print(f"[{code_z}] daily_th 写入失败: {exc}")
        finally:
            _release_symbol_lock(lock_path)

    return counts


def compare_local_vs_remote(
    code: str,
    start_date: str,
    end_date: str,
    *,
    cache_dir: Path,
    period: str = "1d",
    adjust: str = "0",
    ty: str = "个股",
    key: Optional[str] = None,
    price_rtol: float = 1e-5,
    price_atol: float = 0.01,
    verbose: bool = True,
) -> dict:
    code_z = _normalize_symbol(code)
    canon = canonical_bar_path(cache_dir, code_z, period, adjust, ty)
    if not canon.exists():
        return {"ok": False, "error": f"本地无档案: {canon}"}

    local = _read_bars_parquet(canon)
    if "code" in local.columns:
        local = local[local["code"].astype(str).map(_normalize_symbol) == code_z]
    req_start = pd.Timestamp(start_date)
    req_end = pd.Timestamp(end_date)
    local = local[(local["date"] >= req_start) & (local["date"] <= req_end)].copy()

    api_key = _ensure_api_key(key)
    remote = _fetch_slice_remote(
        code_z,
        start_date,
        end_date,
        api_key,
        period,
        adjust,
        ty,
        verbose=False,
        cache_dir=cache_dir,
    )
    if remote.empty:
        return {"ok": False, "error": "接口返回为空"}

    if "code" in remote.columns:
        remote = remote[remote["code"].astype(str).map(_normalize_symbol) == code_z]

    L = local.set_index("date").sort_index()
    R = remote.set_index("date").sort_index()
    common = L.index.intersection(R.index)
    if len(common) == 0:
        return {"ok": False, "error": "本地与接口无重叠交易日"}

    check_cols = [c for c in ["open", "high", "low", "close", "volume"] if c in L.columns and c in R.columns]
    mismatches = []
    for d in common:
        row_l = L.loc[d]
        row_r = R.loc[d]
        for col in check_cols:
            try:
                v1, v2 = float(row_l[col]), float(row_r[col])
            except (TypeError, ValueError):
                continue
            if abs(v1 - v2) > price_atol + price_rtol * max(abs(v1), abs(v2), 1.0):
                mismatches.append({"date": str(d.date()), "col": col, "local": v1, "remote": v2})

    ok = len(mismatches) == 0
    sample = mismatches[:10]
    if verbose:
        print(f"校验 {code_z} {start_date}~{end_date}: 重叠 {len(common)} 日, 不一致 {len(mismatches)} 处")
        if sample:
            print("示例:", sample[:3])
    return {
        "ok": ok,
        "overlap_days": len(common),
        "mismatch_count": len(mismatches),
        "sample_mismatches": sample,
    }


def sample_check_local_vs_remote(
    code: str,
    local_df: pd.DataFrame,
    *,
    cache_dir: Optional[Path] = None,
    period: str = "1d",
    adjust: str = "0",
    ty: str = "个股",
    key: Optional[str] = None,
    sample_points: int = 5,
    seed: int = 42,
    price_rtol: float = 1e-5,
    price_atol: float = 0.01,
    deadline_ts: Optional[float] = None,
    per_request_timeout_s: float = 4.0,
) -> dict:
    if local_df.empty:
        return {"checked": 0, "mismatch_count": 0, "mismatches": []}

    df = local_df.copy()
    if "date" not in df.columns:
        if df.index.name == "date":
            df = df.reset_index()
        else:
            raise ValueError("local_df 缺少 date 列。")
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date")
    if len(df) == 0:
        return {"checked": 0, "mismatch_count": 0, "mismatches": []}

    candidate = [0, len(df) // 2, len(df) - 1]
    code_z = _normalize_symbol(code)
    code_seed = sum(ord(c) for c in code_z)
    rng = random.Random(seed + code_seed)
    if len(df) > 3:
        others = list(range(1, max(len(df) - 1, 1)))
        rng.shuffle(others)
        candidate.extend(others[: max(0, sample_points - 3)])
    sampled_idx = []
    seen = set()
    for idx in candidate:
        if idx < 0 or idx >= len(df):
            continue
        if idx in seen:
            continue
        sampled_idx.append(idx)
        seen.add(idx)
        if len(sampled_idx) >= sample_points:
            break

    api_key = _ensure_api_key(key)
    mismatches = []
    checked = 0
    network_failures = 0
    for idx in sampled_idx:
        if deadline_ts is not None and time.perf_counter() >= deadline_ts:
            break
        row = df.iloc[idx]
        d = pd.to_datetime(row["date"]).strftime("%Y-%m-%d")
        try:
            remote = _fetch_slice_remote_once(
                code_z,
                d,
                d,
                api_key,
                period,
                adjust,
                ty,
                timeout_s=per_request_timeout_s,
                cache_dir=cache_dir,
            )
        except (requests.RequestException, TimeoutError, OSError):
            network_failures += 1
            continue
        if remote.empty:
            continue
        remote_row = remote.iloc[-1]
        checked += 1
        for col in ["open", "high", "low", "close", "volume"]:
            if col not in row or col not in remote_row:
                continue
            try:
                v1 = float(row[col])
                v2 = float(remote_row[col])
            except (TypeError, ValueError):
                continue
            if abs(v1 - v2) > price_atol + price_rtol * max(abs(v1), abs(v2), 1.0):
                mismatches.append({"date": d, "col": col, "local": v1, "remote": v2})

    inconclusive = checked == 0 and len(sampled_idx) > 0
    inconclusive_reason = None
    if inconclusive:
        inconclusive_reason = "network" if network_failures else "no_remote_rows"

    return {
        "checked": checked,
        "mismatch_count": len(mismatches),
        "mismatches": mismatches[:10],
        "inconclusive": inconclusive,
        "inconclusive_reason": inconclusive_reason,
        "network_failures": network_failures,
    }
