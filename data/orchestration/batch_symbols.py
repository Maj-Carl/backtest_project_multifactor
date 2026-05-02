"""多标的日线行情编排入口。"""

from __future__ import annotations

import logging
import time
from collections import Counter
from pathlib import Path
from typing import Any

from data.fetch.api_keys import load_api_key
from data.universe.builder import UNIVERSE_CACHE_FILE
from data.storage.bar_store import (
    BackfillFirstSegmentEmptyError,
    batch_online_sample_check_daily_th_cross_section,
    incremental_daily_th_prune_and_fill_cache,
    load_or_update_bars,
    remove_codes_from_universe_cache,
    reset_phase_a_microtimings,
    take_phase_a_microtimings,
)
from utils.logger import (
    get_backtest_logger,
    get_debug_logger,
    log_performance_event,
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
    total_n = len(codes)

    def _progress_interval(n: int) -> int:
        if n <= 20:
            return 1
        return max(1, min(200, n // 50))

    report_every = _progress_interval(total_n)
    db = get_debug_logger("batch")
    if db.isEnabledFor(logging.DEBUG):
        db.debug(
            "phase A start n=%s period=%s ty=%s range=%s~%s cache_dir=%s use_local=%s",
            total_n,
            period,
            ty,
            start_date,
            end_date,
            str(cache_dir.resolve()),
            use_local,
        )

    if verbose:
        mode = "本地优先（不足再拉接口）" if use_local else "不向本地读取（use_local=False）"
        get_backtest_logger().info(
            "[行情装载] 正在加载 %s 只标的 %s/%s 行情，区间 %s～%s，silver 仓库 %s。%s",
            total_n,
            period,
            ty,
            start_date,
            end_date,
            cache_dir.resolve(),
            mode,
        )

    reset_phase_a_microtimings()
    daily_th_prefetch = None
    universe_pruned_codes: list[str] = []
    inc_pruned_set: set[str] = set()
    if period == "1d" and ty == "个股":
        try:
            api_k = key or load_api_key()
            if not api_k:
                raise ValueError("缺少 API Key。")
            daily_th_prefetch, inc_pruned = incremental_daily_th_prune_and_fill_cache(
                codes,
                start_date,
                end_date,
                cache_dir=cache_dir,
                api_key=api_k,
                period=period,
                ty=ty,
                use_local=use_local,
            )
            universe_pruned_codes.extend(inc_pruned)
            inc_pruned_set = set(inc_pruned)
            if not daily_th_prefetch:
                daily_th_prefetch = None
            elif verbose:
                get_backtest_logger().info(
                    "[行情装载] 接口 B 增量全日：已缓存 %s 个交易日快照（剔池 %s 只后继续逐只装载）",
                    len(daily_th_prefetch),
                    len(inc_pruned),
                )
        except Exception as exc:
            get_backtest_logger().warning(
                "[行情装载] 接口 B 增量预取未启用，回退逐标的请求: %s",
                exc,
            )
            daily_th_prefetch = None
            inc_pruned_set = set()

    t_load = time.perf_counter()
    for idx, code in enumerate(codes):
        code_key = str(code).zfill(6)
        if code_key in inc_pruned_set:
            failed_codes.append(code_key)
        else:
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
                    daily_th_prefetch=daily_th_prefetch,
                )
                staged_local_for_sampling[code_key] = df
                df = df.copy()
                df.set_index("date", inplace=True)
                result[code_key] = df
            except BackfillFirstSegmentEmptyError as exc:
                universe_pruned_codes.append(code_key)
                get_backtest_logger().warning(
                    "[%s] 第一段补缺为空，跳过本标的；"
                    "为保证回测数据在回测交易日区间的完整性，已将 %s 从股票池 %s 中剔除"
                    "（股票池 CSV 在阶段 A 结束后批量写入）: %s",
                    code_key,
                    code_key,
                    UNIVERSE_CACHE_FILE.name,
                    exc,
                )
                failed_codes.append(code_key)
            except Exception as exc:
                if continue_on_error:
                    if verbose:
                        get_backtest_logger().info(
                            "[%s] 拉取失败（已跳过）: %s", code_key, exc
                        )
                    failed_codes.append(code_key)
                else:
                    raise

        done = idx + 1
        if verbose and (done == 1 or done == total_n or done % report_every == 0):
            elapsed = time.perf_counter() - t_load
            pct = 100.0 * done / total_n
            rate = done / elapsed if elapsed > 0 else 0.0
            eta_s = (total_n - done) / rate if rate > 0 else 0.0
            get_backtest_logger().info(
                "[行情装载] 进度 %s/%s (%.1f%%) | 当前 %s | 已用 %.1fs | 预计剩余 %.1fs",
                done,
                total_n,
                pct,
                code_key,
                elapsed,
                eta_s,
            )
        if db.isEnabledFor(logging.DEBUG) and (
            done == 1 or done == total_n or done % report_every == 0
        ):
            elapsed = time.perf_counter() - t_load
            rate = done / elapsed if elapsed > 0 else 0.0
            eta_s = (total_n - done) / rate if rate > 0 else 0.0
            db.debug(
                "progress %s/%s code=%s elapsed_s=%.3f eta_s=%.3f",
                done,
                total_n,
                code_key,
                elapsed,
                eta_s,
            )

    if universe_pruned_codes:
        uniq = list(dict.fromkeys(universe_pruned_codes))
        n_rm = remove_codes_from_universe_cache(uniq)
        preview = ",".join(uniq[:16])
        if len(uniq) > 16:
            preview += f" …等共{len(uniq)}只"
        else:
            preview = f"{preview}（共{len(uniq)}只）" if uniq else ""
        get_backtest_logger().warning(
            "[行情补缺] 阶段 A 结束：为保证回测数据在回测交易日区间的完整性，已将 %s 从股票池 %s 中剔除"
            "（本次从缓存文件删除 %s 行）",
            preview,
            UNIVERSE_CACHE_FILE.name,
            n_rm,
        )

    t_after_phase_a = time.perf_counter()
    micro = take_phase_a_microtimings()
    nsym = int(micro["symbols"]) or 0
    if nsym > 0:
        parts_sum = (
            micro["read_s"]
            + micro["gaps_s"]
            + micro["fetch_s"]
            + micro["persist_s"]
            + micro["lock_s"]
            + micro["merge_s"]
            + micro["dedupe_s"]
            + micro["log_s"]
            + micro["other_s"]
        )
        log_performance_event(
            "data/orchestration/batch_symbols.py",
            kind="批量行情",
            step="阶段 A 子耗时累计（各只 load_or_update_bars 内分项之和）",
            code=(
                "read/gaps/fetch/persist 见上 | lock=_acquire_symbol_lock | "
                "merge=循环内 concat 并入远端块 | dedupe=drop_duplicates+sort+请求区间切片 | "
                "log=DuckDB ingest_runs（仅写 silver 并更新 bar_catalog 后）| other=余项（verbose 分支、DEBUG 等）"
            ),
            metrics=(
                f"n_sym={nsym} | read={micro['read_s']:.3f}s | gaps={micro['gaps_s']:.3f}s | "
                f"fetch={micro['fetch_s']:.3f}s | persist={micro['persist_s']:.3f}s || "
                f"lock={micro['lock_s']:.3f}s | merge={micro['merge_s']:.3f}s | "
                f"dedupe={micro['dedupe_s']:.3f}s | log={micro['log_s']:.3f}s | "
                f"other={micro['other_s']:.3f}s || sum_parts={parts_sum:.3f}s | "
                f"wall_阶段A={t_after_phase_a - t_load:.3f}s"
            ),
        )
    log_performance_event(
        "data/orchestration/batch_symbols.py",
        kind="批量行情",
        step="阶段 A 结束：逐标的从本地/远程装载并补缺",
        code="data/storage/bar_store.py:load_or_update_bars（本函数内 for 循环）",
        elapsed_s=t_after_phase_a - t_load,
        metrics=f"n_codes={total_n} | ok={len(result)} | failed={len(failed_codes)}",
    )

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
        get_backtest_logger().info("%s", line)

    if db.isEnabledFor(logging.DEBUG):
        db.debug(
            "phase A end ok=%s failed=%s elapsed_s=%.3f",
            len(result),
            len(failed_codes),
            time.perf_counter() - t_load,
        )

    if (
        sampling_check_enabled
        and staged_local_for_sampling
        and not (continue_on_error and not result)
    ):
        t_phase_b0 = time.perf_counter()
        if verbose:
            get_backtest_logger().info(
                "[在线抽样] 阶段 B 开始（阶段 A 已结束）：本批 %s 只，将进行接口 B 按日快照校验。",
                len(staged_local_for_sampling),
            )
        sampling_deadline = time.perf_counter() + float(sampling_check_timeout_s)
        smp = get_debug_logger("sampling")
        if smp.isEnabledFor(logging.DEBUG):
            smp.debug(
                "phase B start n_symbols=%s sample_points=%s timeout_s=%s",
                len(staged_local_for_sampling),
                sampling_check_points,
                sampling_check_timeout_s,
            )
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
        log_performance_event(
            "data/orchestration/batch_symbols.py",
            kind="批量行情",
            step="阶段 B 结束：按抽样交易日拉全日快照并与本地 OHLCV 对账",
            code="data/storage/bar_store.py:batch_online_sample_check_daily_th_cross_section",
            elapsed_s=time.perf_counter() - t_phase_b0,
            metrics=f"symbols={len(staged_local_for_sampling)}",
        )
        if smp.isEnabledFor(logging.DEBUG):
            smp.debug("phase B finished")

    if continue_on_error and failed_codes:
        fail_log = cache_dir / "smart_merge_failed_codes.txt"
        with open(fail_log, "a", encoding="utf-8") as f:
            for c in failed_codes:
                f.write(c + "\n")

    return result


__all__ = ["get_multiple_stock_data"]
