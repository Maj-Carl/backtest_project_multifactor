"""
本地行情仓库（优化版）：
- Parquet 作为主存储（更快、更省空间）
- DuckDB 作为元数据目录（catalog）
- 本地优先，不足则自动补缺（前补/后补/中间断档）
"""
from __future__ import annotations

import logging
import os
import random
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

import pandas as pd
import requests

from data.fetch.api_keys import load_api_key
from data.fetch.apis.api_kline_daily_th import fetch_daily_th_bars_for_code, fetch_daily_th_market
from data.fetch.apis.api_kline_dc import fetch_kline_dc_nonempty_payload
from data.fetch.trade_calendar import get_trade_days
from data.storage.column_normalize import normalize_legacy_columns
from data.universe.builder import UNIVERSE_CACHE_FILE
from utils.logger import get_backtest_logger, get_debug_logger


class BackfillFirstSegmentEmptyError(ValueError):
    """日历缺口下第一段接口 B 补缺对该标的仍无行：已从 a_share_codes.csv 剔除，调用方应跳过本标的。"""

    def __init__(self, code: str):
        self.code = str(code).zfill(6)
        super().__init__(
            f"[{self.code}] 第一段 daily_th 补缺为空；"
            f"为保证回测数据在回测交易日区间的完整性，已将 {self.code} 从股票池 "
            f"{UNIVERSE_CACHE_FILE.name} 中剔除"
        )


def remove_codes_from_universe_cache(codes_to_remove: Sequence[str]) -> int:
    """从持久化股票池 CSV 中批量移除代码。返回删除的行数。"""
    if not codes_to_remove:
        return 0
    bad = {_normalize_symbol(str(c)) for c in codes_to_remove}
    path = UNIVERSE_CACHE_FILE
    if not path.exists():
        return 0
    try:
        df = pd.read_csv(path, dtype={"code": str}, encoding="utf-8-sig")
    except Exception:
        get_backtest_logger().warning("读取股票池缓存失败，跳过剔池: %s", path)
        return 0
    if "code" not in df.columns:
        return 0
    work = df.copy()
    work["_c6"] = (
        work["code"].astype(str).str.extract(r"(\d{6})", expand=False).fillna("").str.zfill(6)
    )
    n0 = len(work)
    work = work[~work["_c6"].isin(bad)].drop(columns=["_c6"], errors="ignore")
    removed = n0 - len(work)
    if removed <= 0:
        return 0
    if work.empty:
        get_backtest_logger().warning(
            "[行情补缺] 批量剔池后股票池缓存已无代码，仍写入空表（请尽快重建股票池）",
        )
    work.to_csv(path, index=False, encoding="utf-8-sig")
    return removed


def _run_info(msg: str, *, verbose: bool = True) -> None:
    if verbose:
        get_backtest_logger().info(msg)

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
    """写入 ``ingest_runs`` 审计行。

    仅在已写 silver 并更新 ``bar_catalog`` 后由 ``load_or_update_bars`` 触发。
    """
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


# 阶段 A（get_multiple_stock_data 内逐只 load_or_update_bars）分项累计秒数，供 performance.log 拆解。
_PHASE_A_MICRO: Dict[str, float] = {
    "read_s": 0.0,
    "gaps_s": 0.0,
    "fetch_s": 0.0,
    "persist_s": 0.0,
    "lock_s": 0.0,
    "merge_s": 0.0,
    "dedupe_s": 0.0,
    "log_s": 0.0,
    "other_s": 0.0,
    "symbols": 0.0,
}


def reset_phase_a_microtimings() -> None:
    """在批量装载开始前清零（由 ``batch_symbols.get_multiple_stock_data`` 调用）。"""
    global _PHASE_A_MICRO
    _PHASE_A_MICRO = {
        "read_s": 0.0,
        "gaps_s": 0.0,
        "fetch_s": 0.0,
        "persist_s": 0.0,
        "lock_s": 0.0,
        "merge_s": 0.0,
        "dedupe_s": 0.0,
        "log_s": 0.0,
        "other_s": 0.0,
        "symbols": 0.0,
    }


def take_phase_a_microtimings() -> Dict[str, float]:
    """取出累计值并清零，供阶段 A 结束后写性能日志。"""
    global _PHASE_A_MICRO
    out = dict(_PHASE_A_MICRO)
    reset_phase_a_microtimings()
    return out


def _record_phase_a_symbol_timings(
    read_s: float,
    gaps_s: float,
    fetch_s: float,
    persist_s: float,
    lock_s: float,
    merge_s: float,
    dedupe_s: float,
    log_s: float,
    wall_s: float,
) -> None:
    parts = (
        read_s
        + gaps_s
        + fetch_s
        + persist_s
        + lock_s
        + merge_s
        + dedupe_s
        + log_s
    )
    other = max(0.0, wall_s - parts)
    _PHASE_A_MICRO["read_s"] += read_s
    _PHASE_A_MICRO["gaps_s"] += gaps_s
    _PHASE_A_MICRO["fetch_s"] += fetch_s
    _PHASE_A_MICRO["persist_s"] += persist_s
    _PHASE_A_MICRO["lock_s"] += lock_s
    _PHASE_A_MICRO["merge_s"] += merge_s
    _PHASE_A_MICRO["dedupe_s"] += dedupe_s
    _PHASE_A_MICRO["log_s"] += log_s
    _PHASE_A_MICRO["other_s"] += other
    _PHASE_A_MICRO["symbols"] += 1.0


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


def compute_need_fetch_segments(
    local: Optional[pd.DataFrame],
    req_start: pd.Timestamp,
    req_end: pd.Timestamp,
    cache_dir: Path,
    period: str,
    ty: str,
) -> List[Tuple[pd.Timestamp, pd.Timestamp]]:
    """与 ``load_or_update_bars`` 内缺口切段逻辑一致（供批处理预取交易日）。"""
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


def _read_parquet_for_gap_plan(canon: Path, code_z: str) -> Optional[pd.DataFrame]:
    """仅读 date（及 code）用于缺口日历计算，减轻与正式装载的双读 IO。"""
    if not canon.exists():
        return None
    try:
        df = pd.read_parquet(canon, columns=["date", "code"])
    except Exception:
        try:
            df = pd.read_parquet(canon, columns=["date"])
        except Exception:
            return None
    df["date"] = pd.to_datetime(df["date"])
    if "code" in df.columns:
        df["code"] = df["code"].astype(str).map(_normalize_symbol)
        df = df[df["code"] == code_z]
    df = df.drop_duplicates(subset=["date"], keep="last").sort_values("date")
    return df


def _build_code_to_missing_trade_dates(
    codes: Sequence[str],
    start_date: str,
    end_date: str,
    *,
    cache_dir: Path,
    period: str,
    ty: str,
    use_local: bool,
) -> Dict[str, Set[str]]:
    """每标的在请求区间内、接口 B 补缺路径上仍缺的交易日（YYYY-MM-DD）。"""
    if period != "1d" or ty != "个股":
        return {}
    req_start = pd.Timestamp(start_date)
    req_end = pd.Timestamp(end_date)
    storage_adjust = "0"
    out: Dict[str, Set[str]] = {}
    for raw in codes:
        code_z = _normalize_symbol(
            str(raw).strip().zfill(6) if str(raw).strip().isdigit() else raw
        )
        canon = canonical_bar_path(cache_dir, code_z, period, storage_adjust, ty)
        merged: Optional[pd.DataFrame] = None
        if use_local and canon.exists():
            merged = _read_parquet_for_gap_plan(canon, code_z)
        segs = compute_need_fetch_segments(
            merged, req_start, req_end, cache_dir, period, ty
        )
        need: Set[str] = set()
        for seg_start, seg_end in segs:
            for d in get_trade_days(
                seg_start,
                seg_end,
                cache_dir=cache_dir,
                period=period,
                ty=ty,
            ):
                need.add(d.strftime("%Y-%m-%d"))
        if need:
            out[code_z] = need
    return out


def incremental_daily_th_prune_and_fill_cache(
    codes: Sequence[str],
    start_date: str,
    end_date: str,
    *,
    cache_dir: Path,
    api_key: str,
    period: str,
    ty: str,
    use_local: bool,
) -> Tuple[Dict[str, pd.DataFrame], List[str]]:
    """按「当前缺口并集最早日」逐日拉全日快照；最早缺口日无行则剔池，每轮后并集自然收缩。"""
    if period != "1d" or ty != "个股":
        return {}, []

    needed = _build_code_to_missing_trade_dates(
        codes,
        start_date,
        end_date,
        cache_dir=cache_dir,
        period=period,
        ty=ty,
        use_local=use_local,
    )
    if not needed:
        return {}, []

    pruned: List[str] = []
    pruned_set: Set[str] = set()
    session_mkt: Dict[str, pd.DataFrame] = {}

    total_pairs = sum(len(s) for s in needed.values())
    max_guard = max(total_pairs * 2 + len(needed) * 200, 5000)
    iter_count = 0

    while iter_count < max_guard:
        iter_count += 1
        active = [c for c in needed if c not in pruned_set and needed[c]]
        if not active:
            break

        union_sorted = sorted(set().union(*(needed[c] for c in active)))
        if not union_sorted:
            break
        d = union_sorted[0]

        if d not in session_mkt:
            try:
                session_mkt[d] = fetch_daily_th_market(api_key, d, verbose=False)
            except Exception as exc:
                get_backtest_logger().warning(
                    "[接口B] 增量全日 date=%s 失败，跳过该日并继续: %s", d, exc
                )
                for c in active:
                    needed[c].discard(d)
                continue

        mkt = session_mkt[d]
        for c in list(active):
            if c in pruned_set or c not in needed or not needed[c] or d not in needed[c]:
                continue
            first_missing = min(needed[c])
            sub = (
                mkt[mkt["code"].astype(str).map(_normalize_symbol) == c]
                if len(mkt) > 0
                else pd.DataFrame()
            )
            if d == first_missing and sub.empty:
                pruned_set.add(c)
                pruned.append(c)
                needed[c] = set()
                get_backtest_logger().warning(
                    "[行情补缺] 增量：%s 在最早缺口日 %s 全日快照中无行；"
                    "为保证回测数据在回测交易日区间的完整性，已将 %s 从股票池 %s 中剔除",
                    c,
                    d,
                    c,
                    UNIVERSE_CACHE_FILE.name,
                )
                continue
            needed[c].discard(d)

    active_remain = [c for c in needed if c not in pruned_set and needed[c]]
    if active_remain:
        get_backtest_logger().warning(
            "[接口B] 增量全日未在迭代上限内跑完（上限=%s），剩余缺口仍走逐只装载",
            max_guard,
        )

    return session_mkt, pruned


def _slice_from_daily_th_prefetch(
    code_z: str,
    seg_start: str,
    seg_end: str,
    *,
    prefetch: Dict[str, pd.DataFrame],
    cache_dir: Path,
) -> pd.DataFrame:
    """从批预取全日快照中拼出某标的区间 K 线（列裁剪与 ``fetch_daily_th_bars_for_code`` 一致）。"""
    parts: list[pd.DataFrame] = []
    for d in get_trade_days(
        seg_start,
        seg_end,
        cache_dir=cache_dir,
        period="1d",
        ty="个股",
    ):
        ds = d.strftime("%Y-%m-%d")
        mkt = prefetch.get(ds)
        if mkt is None or mkt.empty:
            continue
        sub = mkt[mkt["code"].astype(str).map(_normalize_symbol) == code_z]
        if not sub.empty:
            parts.append(sub)
    if not parts:
        return pd.DataFrame()
    out = pd.concat(parts, ignore_index=True)
    keep = [
        c
        for c in (
            "code",
            "date",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "amount",
            "turnover",
            "factor",
            "is_paused",
            "is_st",
            "high_limit",
            "low_limit",
            "avg_price",
            "prev_close",
        )
        if c in out.columns
    ]
    return out[keep].drop_duplicates(subset=["date"], keep="last").sort_values("date")


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
    daily_th_prefetch: Optional[Dict[str, pd.DataFrame]] = None,
) -> pd.DataFrame:
    if period == "1d" and ty == "个股":
        if verbose:
            _run_info(
                f"[{_normalize_symbol(code)}] 使用 daily_th 补缺 {start_date}~{end_date}"
            )
        cdir = cache_dir if cache_dir is not None else Path(".")
        if daily_th_prefetch is not None:
            df = _slice_from_daily_th_prefetch(
                _normalize_symbol(code),
                start_date,
                end_date,
                prefetch=daily_th_prefetch,
                cache_dir=cdir,
            )
        else:
            df = fetch_daily_th_bars_for_code(
                _normalize_symbol(code),
                start_date,
                end_date,
                api_key,
                verbose=verbose,
                cache_dir=cdir,
            )
        if df.empty:
            get_backtest_logger().warning(
                "[接口B] 区间未得到该标的行 code=%s %s~%s（可能未上市、停牌或全日快照中无此代码）",
                _normalize_symbol(code),
                start_date,
                end_date,
            )
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
    log_each_symbol: bool = True,
    ingest_snapshots: Optional[List[dict[str, Any]]] = None,
    daily_th_prefetch: Optional[Dict[str, pd.DataFrame]] = None,
) -> pd.DataFrame:
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "silver").mkdir(parents=True, exist_ok=True)
    requested_adjust = str(adjust)
    # 远端请求与本地 parquet 文件名中的 adjust 段恒为 "0"（不复权）；出口再按 requested_adjust 折算。
    storage_adjust = "0"
    canon = canonical_bar_path(cache_dir, code, period, storage_adjust, ty)
    code_z = _normalize_symbol(code)
    run_id = uuid.uuid4().hex
    lock_path: Optional[Path] = None

    req_start = pd.Timestamp(start_date)
    req_end = pd.Timestamp(end_date)

    merged: Optional[pd.DataFrame] = None

    try:
        t_wall0 = time.perf_counter()
        dt_read = 0.0
        dt_gaps = 0.0
        dt_fetch = 0.0
        dt_persist = 0.0
        dt_lock = 0.0
        dt_merge = 0.0
        dt_dedupe = 0.0
        dt_log = 0.0
        merged_remote_rows = False

        t0 = time.perf_counter()
        lock_path = _acquire_symbol_lock(cache_dir, code_z, period, storage_adjust, ty)
        dt_lock = time.perf_counter() - t0

        if use_local and canon.exists():
            t0 = time.perf_counter()
            merged = _read_bars_parquet(canon)
            if "code" in merged.columns:
                merged = merged[merged["code"].astype(str).map(_normalize_symbol) == code_z]
            dt_read = time.perf_counter() - t0

        api_key = _ensure_api_key(key)

        t0 = time.perf_counter()
        segments = compute_need_fetch_segments(
            merged, req_start, req_end, cache_dir, period, ty
        )
        dt_gaps = time.perf_counter() - t0
        calendar_had_gaps = bool(segments)

        dbg = get_debug_logger("bars")
        if dbg.isEnabledFor(logging.DEBUG):
            seg_preview = [
                (str(a.date()), str(b.date())) for a, b in segments[:8]
            ]
            dbg.debug(
                "load_or_update_bars code=%s canon_exists=%s use_local=%s n_segments=%s "
                "range=%s~%s seg_preview=%s",
                code_z,
                canon.exists(),
                use_local,
                len(segments),
                start_date,
                end_date,
                seg_preview,
            )

        fetch_verbose = bool(verbose and log_each_symbol)
        for si, (seg_start, seg_end) in enumerate(segments):
            if seg_start > seg_end:
                continue
            if dbg.isEnabledFor(logging.DEBUG):
                dbg.debug(
                    "segment %s/%s code=%s %s~%s",
                    si + 1,
                    len(segments),
                    code_z,
                    seg_start.date(),
                    seg_end.date(),
                )
            if fetch_verbose:
                _run_info(
                    f"[{code_z}] 向接口补缺: {seg_start.date()} ~ {seg_end.date()}",
                    verbose=True,
                )
            t0 = time.perf_counter()
            chunk = _fetch_slice_remote(
                code_z,
                seg_start.strftime("%Y-%m-%d"),
                seg_end.strftime("%Y-%m-%d"),
                api_key,
                period,
                storage_adjust,
                ty,
                fetch_verbose,
                cache_dir=cache_dir,
                daily_th_prefetch=daily_th_prefetch,
            )
            dt_fetch += time.perf_counter() - t0
            if chunk.empty:
                if (
                    period == "1d"
                    and ty == "个股"
                    and si == 0
                    and calendar_had_gaps
                ):
                    if ingest_snapshots is not None:
                        ingest_snapshots.append(
                            {
                                "code": code_z,
                                "ingest_message": "first_segment_daily_th_empty_pruned_universe",
                                "merged_rows": int(len(merged)) if merged is not None else 0,
                            }
                        )
                    get_backtest_logger().warning(
                        "[行情补缺] %s 第一段接口 B 对该标的无行，终止本标的补仓；"
                        "为保证回测数据在回测交易日区间的完整性，已将 %s 从股票池 %s 中剔除"
                        "（股票池 CSV 在本阶段结束后批量写入）",
                        code_z,
                        code_z,
                        UNIVERSE_CACHE_FILE.name,
                    )
                    raise BackfillFirstSegmentEmptyError(code_z)
                continue
            merged_remote_rows = True
            t1 = time.perf_counter()
            if merged is None:
                merged = chunk
            else:
                merged = pd.concat([merged, chunk], ignore_index=True)
            dt_merge += time.perf_counter() - t1

        if merged is None or merged.empty:
            raise ValueError(f"[{code_z}] 无本地数据且接口未返回数据。")

        t0 = time.perf_counter()
        merged = merged.drop_duplicates(subset=["date"], keep="last").sort_values("date")
        dt_dedupe += time.perf_counter() - t0
        if merged_remote_rows:
            t0 = time.perf_counter()
            tmp_path = canon.with_suffix(".parquet.tmp")
            merged.to_parquet(tmp_path, index=False)
            os.replace(tmp_path, canon)
            _update_catalog(cache_dir, code_z, period, storage_adjust, ty, canon, merged)
            dt_persist = time.perf_counter() - t0
            if verbose and log_each_symbol:
                _run_info(
                    f"[{code_z}] 已写入本地档案（接口补缺并入）: {canon} "
                    f"（全表 {len(merged)} 行）"
                )
            ingest_msg = "persist_remote_merge"
        elif not calendar_had_gaps:
            if verbose and log_each_symbol:
                _run_info(
                    f"[{code_z}] 请求区间内交易日已齐备，沿用本地档案，未写入磁盘 "
                    f"（全表 {len(merged)} 行）"
                )
            ingest_msg = "skip_write_no_calendar_gap"
        else:
            if verbose and log_each_symbol:
                _run_info(
                    f"[{code_z}] 已请求接口补缺但未获得有效行，未写入磁盘 "
                    f"（全表 {len(merged)} 行）"
                )
            ingest_msg = "skip_write_remote_empty_chunks"

        if ingest_snapshots is not None:
            ingest_snapshots.append(
                {"code": code_z, "ingest_message": ingest_msg, "merged_rows": int(len(merged))}
            )

        if dbg.isEnabledFor(logging.DEBUG):
            dbg.debug(
                "load_or_update_bars done code=%s ingest=%s merged_rows=%s",
                code_z,
                ingest_msg,
                len(merged),
            )

        t0 = time.perf_counter()
        out = merged[(merged["date"] >= req_start) & (merged["date"] <= req_end)].copy()
        dt_dedupe += time.perf_counter() - t0

        if out.empty:
            raise ValueError(f"[{code_z}] 合并后仍无数据落在请求区间 {start_date}~{end_date}。")
        if merged_remote_rows:
            t0 = time.perf_counter()
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
                message=ingest_msg,
                rows_after=len(merged),
            )
            dt_log += time.perf_counter() - t0
        _record_phase_a_symbol_timings(
            dt_read,
            dt_gaps,
            dt_fetch,
            dt_persist,
            dt_lock,
            dt_merge,
            dt_dedupe,
            dt_log,
            time.perf_counter() - t_wall0,
        )
        return _apply_adjust_to_ohlc(out, requested_adjust)
    except Exception as exc:
        if merged_remote_rows:
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


def _union_sorted_dates_from_loaded(code_to_df: Dict[str, pd.DataFrame]) -> list[pd.Timestamp]:
    union: Set[pd.Timestamp] = set()
    for df in code_to_df.values():
        if df is None or df.empty:
            continue
        dd = df
        if "date" not in dd.columns:
            if dd.index.name == "date":
                dd = dd.reset_index()
            else:
                continue
        union.update(pd.to_datetime(dd["date"]).dt.normalize())
    return sorted(union)


def pick_sample_trade_dates_union(
    sorted_unique_dates: list[pd.Timestamp],
    sample_points: int,
    seed: int,
) -> list[str]:
    """首尾交易日固定，中间再在并集日历上随机取样；总抽样日数至多 ``sample_points``。"""
    n = len(sorted_unique_dates)
    if n == 0:
        return []
    first_dt, last_dt = sorted_unique_dates[0], sorted_unique_dates[-1]
    middle_k = max(0, int(sample_points) - 2)
    chosen_ts: List[pd.Timestamp]
    if n <= 2 or middle_k == 0:
        chosen_ts = sorted({first_dt, last_dt})
    else:
        mid_pool = sorted_unique_dates[1:-1]
        take = min(middle_k, len(mid_pool))
        rng = random.Random(int(seed) + 7919)
        chosen_mids = rng.sample(mid_pool, take) if take else []
        chosen_ts = sorted({first_dt, last_dt}.union(set(chosen_mids)))
    return [t.strftime("%Y-%m-%d") for t in chosen_ts]


def _latest_factor_from_local_loaded(df: pd.DataFrame) -> Optional[float]:
    """与 ``_apply_adjust_to_ohlc`` 前复权分支一致：取按日期排序后 factor 的最后一项。"""
    if df.empty or "factor" not in df.columns:
        return None
    loc = df.sort_values("date").copy()
    fac = pd.to_numeric(loc["factor"], errors="coerce").dropna()
    if fac.empty:
        return None
    v = float(fac.iloc[-1])
    return v if not (pd.isna(v) or v == 0) else None


def _align_remote_snapshot_row_ohlc(
    remote_row: pd.Series,
    *,
    adjust: str,
    latest_fac_from_local_curve: Optional[float],
) -> pd.Series:
    """接口 B 日线行为「原始 OHLC」+ factor；按下述规则与本地 ``load_or_update_bars`` 出口口径对齐。

    - adjust=0：不改 OHLC；
    - adjust=1（前复权）：``oh * factor_day / factor_latest_local`` ，其中 factor_latest_local 为该票本批装载序列的最后一行因子；
    - adjust=2（后复权）：``oh * factor_day`` 。
    """
    out = remote_row.copy()
    aj = str(adjust)
    if aj == "0":
        return out
    fac_r = pd.to_numeric(out.get("factor"), errors="coerce")
    if pd.isna(fac_r):
        raise ValueError("全日快照该行缺少可用 factor")

    if aj == "2":
        mul = float(fac_r)
    elif aj == "1":
        if latest_fac_from_local_curve is None:
            raise ValueError("本地序列缺少末尾 factor（前复权对照）")
        den = float(latest_fac_from_local_curve)
        if den == 0 or pd.isna(den):
            raise ValueError("本地末尾 factor 无效")
        mul = float(fac_r) / den
    else:
        return out

    for col in ("open", "high", "low", "close"):
        if col in out.index:
            rv = pd.to_numeric(out[col], errors="coerce")
            out[col] = float(rv) * mul if not pd.isna(rv) else rv
    return out


def _cross_section_ohlcv_mismatches(
    local_row: pd.Series,
    remote_row: pd.Series,
    *,
    price_rtol: float = 1e-5,
    price_atol: float = 0.01,
) -> list[dict]:
    mism: list[dict] = []
    for col in ("open", "high", "low", "close", "volume"):
        if col not in local_row or col not in remote_row:
            continue
        try:
            v1 = float(local_row[col])
            v2 = float(remote_row[col])
        except (TypeError, ValueError):
            continue
        if abs(v1 - v2) > price_atol + price_rtol * max(abs(v1), abs(v2), 1.0):
            mism.append({"col": col, "local": v1, "remote": v2})
    return mism


def batch_online_sample_check_daily_th_cross_section(
    code_to_loaded_df: Dict[str, pd.DataFrame],
    *,
    key: Optional[str] = None,
    sample_points: int = 5,
    seed: int = 42,
    deadline_ts: Optional[float] = None,
    strict: bool = True,
    verbose: bool = True,
    period: str = "1d",
    adjust: str = "0",
    ty: str = "个股",
    per_market_fetch_timeout_s: float = 40.0,
    price_rtol: float = 1e-5,
    price_atol: float = 0.01,
) -> dict:
    """按交易日抽样拉接口B全日快照，与本批每只本地行逐只比对 OHLC/V（轻装验收）。

    任意 ``adjust∈{"0","1","2"}``：接口 B 行内 ``factor`` 与本地装载序列对齐后折算远端 OHLC，再与已由
    ``_apply_adjust_to_ohlc`` 处理过的本地行比较（volume 仍直比）。
    """
    skipped: Dict[str, Any] = {"skipped": True, "reason": "", "sample_dates": []}
    if period != "1d" or ty != "个股":
        skipped["reason"] = "当前在线抽样仅支持 period=1d 且 ty=个股（接口 B）。"
        if verbose:
            _run_info(f"[在线抽样] {skipped['reason']}")
        return skipped

    aj = str(adjust)
    if aj not in ("0", "1", "2"):
        skipped["reason"] = f"不支持的 adjust={adjust!r}，抽样跳过。"
        if verbose:
            _run_info(f"[在线抽样] {skipped['reason']}")
        return skipped

    if not code_to_loaded_df:
        skipped["reason"] = "无一只有效本地 DataFrame。"
        if verbose:
            _run_info(f"[在线抽样] {skipped['reason']}")
        return skipped

    sorted_dates = _union_sorted_dates_from_loaded(code_to_loaded_df)
    sample_dates = pick_sample_trade_dates_union(sorted_dates, sample_points, seed)
    if not sample_dates:
        skipped["reason"] = "并集日历为空。"
        if verbose:
            _run_info(f"[在线抽样] {skipped['reason']}")
        return skipped

    if verbose:
        _run_info(
            f"[在线抽样] 抽样种子 seed={int(seed)}"
            "（对应 pick_sample_trade_dates_union 内 Random(seed+7919)，见 Config.DATA_SAMPLING_CHECK_SEED）"
        )

    try:
        api_key = _ensure_api_key(key)
    except ValueError as exc:
        skipped["reason"] = str(exc)
        if verbose:
            _run_info(f"[在线抽样] {skipped['reason']}，跳过阶段 B。")
        return skipped

    probe_ds = sample_dates[0]
    try:
        rem = (
            None
            if deadline_ts is None
            else max(0.5, deadline_ts - time.perf_counter())
        )
        read_t = min(float(per_market_fetch_timeout_s), rem) if rem is not None else float(per_market_fetch_timeout_s)
        fetch_daily_th_market(
            api_key,
            probe_ds,
            verbose=False,
            timeout=(3.0, max(5.0, read_t)),
            use_cache=False,
        )
    except (ValueError, requests.RequestException, OSError, KeyError) as exc:
        skipped["reason"] = f"按日探针失败（{exc}），阶段 B 整段跳过"
        if verbose:
            _run_info(f"[在线抽样] {skipped['reason']}")
        skipped["sample_dates"] = sample_dates[:1]
        return skipped

    total_cells = 0
    total_checked = 0
    mismatches_agg: List[dict] = []
    stopped_deadline = False
    completed_sample_dates: List[str] = []

    latest_fac_by_code: Dict[str, Optional[float]] = {}
    for cz, sdf in sorted(code_to_loaded_df.items()):
        cz_k = _normalize_symbol(str(cz))
        locprep = sdf
        if locprep is None or locprep.empty:
            latest_fac_by_code[cz_k] = None
            continue
        if "date" not in locprep.columns:
            if locprep.index.name == "date":
                locprep = locprep.reset_index()
            else:
                latest_fac_by_code[cz_k] = None
                continue
        latest_fac_by_code[cz_k] = _latest_factor_from_local_loaded(locprep.sort_values("date"))

    def _resolve_local_slice(code_z: str, df: pd.DataFrame) -> Optional[pd.DataFrame]:
        if df is None or df.empty:
            return None
        loc = df
        if "date" not in loc.columns:
            if loc.index.name == "date":
                loc = loc.reset_index()
            else:
                return None
        return loc.assign(_dn=pd.to_datetime(loc["date"]).dt.normalize())

    for ds in sample_dates:
        if deadline_ts is not None and time.perf_counter() >= deadline_ts:
            stopped_deadline = True
            break
        rem = (
            None
            if deadline_ts is None
            else max(0.5, deadline_ts - time.perf_counter())
        )
        try:
            read_t = (
                float(per_market_fetch_timeout_s)
                if rem is None
                else min(float(per_market_fetch_timeout_s), rem + 5.0)
            )
            mkt = fetch_daily_th_market(
                api_key,
                ds,
                verbose=False,
                timeout=(3.0, max(5.0, read_t)),
                use_cache=False,
            )
        except (ValueError, requests.RequestException, OSError, KeyError) as exc:
            if strict:
                raise RuntimeError(f"[在线抽样] 日期 {ds} 拉取全日快照失败: {exc}") from exc
            if verbose:
                _run_info(f"[在线抽样] 跳过日期 {ds}（拉取失败: {exc}）")
            continue

        completed_sample_dates.append(ds)
        day_ts = pd.Timestamp(ds).normalize()
        mkt = mkt.assign(_rn=mkt["code"].astype(str).map(_normalize_symbol))
        uniq_remote = mkt.drop_duplicates(subset=["_rn"], keep="last").set_index("_rn")

        for code_z, df_loc in sorted(code_to_loaded_df.items()):
            code_z = _normalize_symbol(code_z)
            slab = _resolve_local_slice(code_z, df_loc)
            if slab is None:
                continue
            lr = slab.loc[slab["_dn"] == day_ts]
            if lr.empty:
                continue
            total_cells += 1
            row_l = lr.iloc[-1]

            try:
                row_r = uniq_remote.loc[code_z]
            except KeyError:
                msg = f"[在线抽样校验失败] 日期 {ds} 标的 {code_z}：全日快照缺失该标的"
                if strict:
                    raise RuntimeError(msg) from None
                if verbose:
                    _run_info(msg)
                continue

            if isinstance(row_r, pd.DataFrame):
                row_r = row_r.iloc[-1]

            try:
                row_r_eff = _align_remote_snapshot_row_ohlc(
                    row_r,
                    adjust=aj,
                    latest_fac_from_local_curve=latest_fac_by_code.get(code_z),
                )
            except ValueError as exc:
                msg = f"[在线抽样] 日期 {ds} 标的 {code_z}：无法用 factor 折算远端 OHLC（{exc}）"
                if strict:
                    raise RuntimeError(msg) from exc
                if verbose:
                    _run_info(msg + "（已跳过该组合）")
                continue

            mm = _cross_section_ohlcv_mismatches(
                row_l, row_r_eff, price_rtol=price_rtol, price_atol=price_atol
            )
            total_checked += 1
            if mm:
                for m in mm:
                    mismatches_agg.append({"date": ds, "code": code_z, **m})
                if verbose:
                    _run_info(f"[在线抽样校验失败] {ds} {code_z} 例: {mm[:2]}")
                if strict:
                    raise RuntimeError(
                        f"[在线抽样校验失败] date={ds} code={code_z} "
                        f"mismatches={mm[:5]}"
                    ) from None

    if verbose:
        planned_n = len(sample_dates)
        done_n = len(completed_sample_dates)
        dates_joined = ", ".join(sample_dates)
        if stopped_deadline and done_n < planned_n:
            timeout_note = f"（计划 {planned_n} 天，因 wall clock 上限仅拉取并完成 {done_n} 天）"
        else:
            timeout_note = ""
        _run_info(
            f"[在线抽样] 已抽样检查 {done_n} 天{timeout_note}，"
            f"共计 {total_checked} 个数据点（标的×抽样日），抽样日：{dates_joined}"
        )

    return {
        "skipped": False,
        "sample_dates": sample_dates,
        "completed_sample_dates": completed_sample_dates,
        "total_cells_with_local_bar": total_cells,
        "checked_pairs": total_checked,
        "mismatch_records": mismatches_agg[:20],
        "stopped_early_deadline": stopped_deadline,
    }
