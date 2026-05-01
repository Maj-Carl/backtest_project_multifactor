"""多标的日线行情编排入口。"""

from __future__ import annotations

import time
from collections import Counter
from pathlib import Path
from typing import Any

from data.storage.bar_store import (
    batch_online_sample_check_daily_th_cross_section,
    load_or_update_bars,
)


def get_multiple_stock_data(
    codes,
    key=None,
    period="1d",
    start_date="2025-03-23",
    end_date="2025-05-23",
    adjust="0",
    ty="个股",
    use_local=True,
    verbose=True,
    cache_dir_path=None,
    continue_on_error=False,
    sampling_check_enabled=False,
    sampling_check_points=5,
    sampling_check_seed=42,
    sampling_check_strict=True,
    sampling_check_timeout_s=20.0,
):
    """批量获取多标的行情数据，返回 {code: DataFrame}。

    阶段 A：逐只装载/补缺；阶段 B（可选）：按抽样交易日拉全日快照，与本批每只本地 OHLC/V 做对账。
    """
    if not codes:
        return {}

    cache_dir = Path(cache_dir_path) if cache_dir_path else Path("./data/multi_cache")
    cache_dir.mkdir(parents=True, exist_ok=True)

    result = {}
    failed_codes = []
    staged_local_for_sampling: dict[str, Any] = {}
    ingest_snapshots: list[dict[str, Any]] | None = [] if verbose else None
    if verbose:
        mode = "本地优先（不足再拉接口）" if use_local else "不向本地读取（use_local=False）"
        print(
            f"[行情装载] 正在加载 {len(codes)} 只标的 {period}/{ty} 行情，"
            f"区间 {start_date}～{end_date}，silver 仓库 {cache_dir.resolve()}。"
            f"{mode}",
            flush=True,
        )

    for code in codes:
        code_key = str(code).zfill(6)
        try:
            df = load_or_update_bars(
                code_key,
                start_date,
                end_date,
                cache_dir=cache_dir,
                period=period,
                adjust=adjust,
                ty=ty,
                key=key,
                use_local=use_local,
                verbose=verbose,
                log_each_symbol=False,
                ingest_snapshots=ingest_snapshots,
            )
            staged_local_for_sampling[code_key] = df
            df = df.copy()
            df.set_index("date", inplace=True)
            result[code_key] = df
        except Exception as exc:
            if continue_on_error:
                if verbose:
                    print(f"[{code_key}] 拉取失败（已跳过）: {exc}")
                failed_codes.append(code_key)
            else:
                raise

    # 阶段 A 收尾必须先于在线抽样打印，避免终端上「在线抽样」看起来早于「装载完成」。
    if verbose and ingest_snapshots is not None:
        n_total = len(ingest_snapshots)
        ctr = Counter(s["ingest_message"] for s in ingest_snapshots)
        n_persist = ctr.get("persist_remote_merge", 0)
        n_local_ok = ctr.get("skip_write_no_calendar_gap", 0)
        n_remote_miss = ctr.get("skip_write_remote_empty_chunks", 0)
        line = (
            f"[行情装载] 阶段 A 已完成 | 收尾汇总：已成功准备 {n_total}/{len(codes)} 只——"
            f"接口补缺并写盘 {n_persist} 只；"
            f"请求区间内日历已齐备、未改写磁盘 {n_local_ok} 只；"
            f"检测到缺口但未拉到有效远端行 {n_remote_miss} 只。"
        )
        if n_persist > 0:
            wrote_codes = sorted(
                s["code"]
                for s in ingest_snapshots
                if s["ingest_message"] == "persist_remote_merge"
            )
            preview = ",".join(wrote_codes[:24])
            if len(wrote_codes) > 24:
                preview += f" …等{n_persist}只"
            line += f"（本批写盘：{preview}）"
        if failed_codes:
            bad = ",".join(failed_codes[:16])
            if len(failed_codes) > 16:
                bad += f" …共{len(failed_codes)}只"
            line += f" 另：拉取失败已跳过 {len(failed_codes)} 只：{bad}"
        print(line, flush=True)

    if (
        sampling_check_enabled
        and staged_local_for_sampling
        and not (continue_on_error and not result)
    ):
        if verbose:
            print(
                f"[在线抽样] 阶段 B 开始（阶段 A 已结束）：本批 "
                f"{len(staged_local_for_sampling)} 只，将进行接口 B 按日快照校验。",
                flush=True,
            )
        sampling_deadline = time.perf_counter() + float(sampling_check_timeout_s)
        batch_online_sample_check_daily_th_cross_section(
            staged_local_for_sampling,
            key=key,
            sample_points=int(sampling_check_points),
            seed=int(sampling_check_seed),
            deadline_ts=sampling_deadline,
            strict=bool(sampling_check_strict),
            verbose=verbose,
            period=str(period),
            adjust=str(adjust),
            ty=str(ty),
        )

    if continue_on_error and failed_codes:
        fail_log = cache_dir / "smart_merge_failed_codes.txt"
        with open(fail_log, "a", encoding="utf-8") as f:
            for c in failed_codes:
                f.write(c + "\n")

    return result


__all__ = ["get_multiple_stock_data"]
