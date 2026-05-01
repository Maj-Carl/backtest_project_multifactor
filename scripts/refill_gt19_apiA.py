"""按离线清单将 missing_days>19 的股票用接口A逐只补一年，并实时打印进度。"""
import multiprocessing as mp
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd

from config.config import Config
from data.fetch.api_keys import load_api_key_from_file as _load_api_key_from_file
from data.storage.bar_store import load_or_update_bars


PER_STOCK_TIMEOUT_S = 90


def _run_one_code(queue: mp.Queue, code: str, cache_dir: Path, key: str):
    try:
        out = load_or_update_bars(
            code,
            Config.DEFAULT_START_DATE,
            Config.DEFAULT_END_DATE,
            cache_dir=cache_dir,
            period="1d",
            adjust="0",
            ty="个股",
            key=key,
            use_local=True,
            verbose=False,
        )
        if out is None or out.empty:
            queue.put(("fail", "empty"))
        else:
            queue.put(("ok", f"rows={len(out)}"))
    except Exception as exc:  # pragma: no cover
        queue.put(("fail", str(exc).replace("\n", " ")[:300]))


def main():
    report = PROJECT_ROOT / "reports" / "coverage_incomplete_60_00_1d0.csv"
    if not report.exists():
        raise FileNotFoundError(f"缺失清单不存在: {report}")

    df = pd.read_csv(report, dtype={"code": str}, encoding="utf-8-sig")
    todo = sorted(df[df["missing_days"] > 19]["code"].astype(str).unique().tolist())
    key = _load_api_key_from_file()
    if not key:
        raise ValueError("缺少 API Key，请检查 C:\\投资\\STOCK_API_KE.txt")

    cache_dir = Path(Config.MULTI_STOCK_CACHE_DIR)
    log_path = PROJECT_ROOT / "reports" / "refill_gt19_with_apiA.log"
    fail_csv = PROJECT_ROOT / "reports" / "refill_gt19_with_apiA_failed.csv"

    ok = 0
    failures: list[tuple[str, str]] = []

    print(f"todo_count={len(todo)}")
    print(f"range={Config.DEFAULT_START_DATE}~{Config.DEFAULT_END_DATE}")
    print(f"cache_dir={cache_dir}")
    with log_path.open("w", encoding="utf-8") as logf:
        logf.write(f"todo_count={len(todo)}\n")
        for i, code in enumerate(todo, start=1):
            print(f"[{i}/{len(todo)}] {code} ...", flush=True)
            q: mp.Queue = mp.Queue()
            p = mp.Process(target=_run_one_code, args=(q, code, cache_dir, key), daemon=True)
            p.start()
            p.join(PER_STOCK_TIMEOUT_S)
            if p.is_alive():
                p.terminate()
                p.join(3)
                msg = f"timeout>{PER_STOCK_TIMEOUT_S}s"
                failures.append((code, msg))
                logf.write(f"FAIL {code} {msg}\n")
                print(f"  -> FAIL {code} {msg}", flush=True)
            else:
                if q.empty():
                    msg = "no_result"
                    failures.append((code, msg))
                    logf.write(f"FAIL {code} {msg}\n")
                    print(f"  -> FAIL {code} {msg}", flush=True)
                else:
                    st, msg = q.get()
                    if st == "ok":
                        ok += 1
                        logf.write(f"OK {code} {msg}\n")
                        print(f"  -> OK {code} {msg}", flush=True)
                    else:
                        failures.append((code, msg))
                        logf.write(f"FAIL {code} {msg}\n")
                        print(f"  -> FAIL {code} {msg}", flush=True)

            if i % 20 == 0 or i == len(todo):
                print(
                    f"progress {i}/{len(todo)} ok={ok} fail={len(failures)}",
                    flush=True,
                )
                logf.flush()

    if failures:
        pd.DataFrame(failures, columns=["code", "error"]).to_csv(
            fail_csv, index=False, encoding="utf-8-sig"
        )
        print(f"failed_csv={fail_csv}")
    print(f"FINAL ok={ok} fail={len(failures)} log={log_path}")


if __name__ == "__main__":
    main()
