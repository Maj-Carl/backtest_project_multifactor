"""多标的日线行情编排入口。"""

from __future__ import annotations

import time
from pathlib import Path

from data.storage.bar_store import load_or_update_bars, sample_check_local_vs_remote


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
    """批量获取多标的行情数据，返回 {code: DataFrame}。"""
    if not codes:
        return {}

    cache_dir = Path(cache_dir_path) if cache_dir_path else Path("./data/multi_cache")
    cache_dir.mkdir(parents=True, exist_ok=True)

    result = {}
    failed_codes = []
    sampling_deadline = time.perf_counter() + float(sampling_check_timeout_s)
    sampling_timeout_reached = False
    sampling_api_available = None
    sampling_skipped_message_printed = False
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
            )

            if sampling_check_enabled and not sampling_timeout_reached:
                if time.perf_counter() >= sampling_deadline:
                    sampling_timeout_reached = True
                    if verbose:
                        print(f"在线抽样校验达到时间上限 {sampling_check_timeout_s}s，后续跳过校验。")
                else:
                    if sampling_api_available is None:
                        try:
                            remaining = max(0.5, sampling_deadline - time.perf_counter())
                            probe = sample_check_local_vs_remote(
                                code_key,
                                df,
                                cache_dir=cache_dir,
                                period=period,
                                adjust=adjust,
                                ty=ty,
                                key=key,
                                sample_points=1,
                                seed=sampling_check_seed,
                                deadline_ts=sampling_deadline,
                                per_request_timeout_s=min(4.0, remaining),
                            )
                            if probe["checked"] > 0 and not probe.get("inconclusive"):
                                sampling_api_available = True
                            else:
                                sampling_api_available = False
                                if verbose:
                                    print("在线抽样校验已跳过：接口无有效返回或网络不稳，继续使用本地数据。")
                        except Exception:
                            sampling_api_available = False
                            if verbose:
                                print("在线抽样校验已跳过：接口不可用或网络异常，继续使用本地数据。")

                    if sampling_api_available:
                        remaining = max(0.5, sampling_deadline - time.perf_counter())
                        check_result = sample_check_local_vs_remote(
                            code_key,
                            df,
                            cache_dir=cache_dir,
                            period=period,
                            adjust=adjust,
                            ty=ty,
                            key=key,
                            sample_points=sampling_check_points,
                            seed=sampling_check_seed,
                            deadline_ts=sampling_deadline,
                            per_request_timeout_s=min(4.0, remaining),
                        )
                        if check_result.get("inconclusive") and verbose:
                            print(
                                f"[{code_key}] 在线抽样未能完成比对（"
                                f"{check_result.get('inconclusive_reason') or 'unknown'}）；严格抽样未跑通时仍先用本地数据。"
                            )
                        if check_result["mismatch_count"] > 0:
                            msg = (
                                f"[{code_key}] 在线抽样校验失败: "
                                f"checked={check_result['checked']}, "
                                f"mismatch={check_result['mismatch_count']}, "
                                f"sample={check_result['mismatches'][:3]}"
                            )
                            if sampling_check_strict:
                                raise RuntimeError(msg)
                            if verbose:
                                print(msg)
                    elif not sampling_skipped_message_printed and verbose:
                        print("在线抽样校验未执行（接口不可用），已继续本地数据流程。")
                        sampling_skipped_message_printed = True

            df.set_index("date", inplace=True)
            result[code_key] = df
        except Exception as exc:
            if continue_on_error:
                if verbose:
                    print(f"[{code_key}] 拉取失败（已跳过）: {exc}")
                failed_codes.append(code_key)
            else:
                raise

    if continue_on_error and failed_codes:
        fail_log = cache_dir / "smart_merge_failed_codes.txt"
        with open(fail_log, "a", encoding="utf-8") as f:
            for c in failed_codes:
                f.write(c + "\n")

    return result


__all__ = ["get_multiple_stock_data"]
