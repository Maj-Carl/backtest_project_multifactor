#!/usr/bin/env python3
"""一次性清理「本仓库内」的回测产出与 Python 缓存文件。

默认不删除 Config.MULTI_STOCK_CACHE_DIR（常指向盘外 STOCK_DATA），避免误清空行情 Parquet / DuckDB。
可选仅在指定目录下删除边角文件（如 smart_merge_failed_codes.txt、locks/*.lock）。

覆盖范围（默认开启，可用 --no-* 关闭单项）
  • reports/ 下 *.html、*.htm 回测报告
  • logs/ 下 *.log 及轮转日志
  • 全仓库 __pycache__（跳过 .git / venv / .venv 路径内）
  • 常见工具缓存：.pytest_cache、htmlcov、.coverage、.mypy_cache、.ruff_cache

常用命令（均在项目根目录执行）
  • 常规清理（真删）::
        python scripts/cleanup_project.py

  • 预演，只打印将要删除的内容，不真实删除::
        python scripts/cleanup_project.py --dry-run

  • 顺带清空本仓库内遗留的 data/multi_cache/（回测若用外盘仓则不受影响）::
        python scripts/cleanup_project.py --include-local-multi-cache

  • 只清理外盘缓存根目录里的边角（失败代码列表、残留 locks），不误删 silver parquet::
        python scripts/cleanup_project.py --cache-dir-delete-junk "C:\\投资\\STOCK_DATA"

按需关闭某类清理
  • 不删报告 HTML:: 加 --no-reports
  • 不删日志:: 加 --no-logs
  • 不删 __pycache__:: 加 --no-pycache
  • 不删工具缓存目录:: 加 --no-tool-caches
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

SKIP_TOPLEVEL_NAMES = {".git", ".venv", "venv", ".env"}


def _iter_pycache_dirs(root: Path) -> list[Path]:
    skip_parts = {"venv", ".venv", ".git"}
    out: list[Path] = []
    for p in root.rglob("__pycache__"):
        if any(part in skip_parts for part in p.parts):
            continue
        out.append(p)
    return sorted(out, key=lambda x: len(str(x)), reverse=True)


def _unlink_dir(path: Path, dry_run: bool) -> None:
    if dry_run:
        print(f"[dry-run] rm -rf {path}")
        return
    shutil.rmtree(path, ignore_errors=True)


def _unlink_file(path: Path, dry_run: bool) -> bool:
    if not path.is_file():
        return False
    if dry_run:
        print(f"[dry-run] rm {path}")
        return True
    try:
        path.unlink()
        return True
    except OSError as exc:
        print(f"[WARN] 无法删除文件 {path}: {exc}")
        return False


def cleanup_reports(root: Path, dry_run: bool) -> tuple[int, int]:
    rdir = root / "reports"
    if not rdir.is_dir():
        return 0, 0
    n_files, n_bytes = 0, 0
    for fp in sorted(rdir.rglob("*")):
        if fp.is_dir():
            continue
        if fp.suffix.lower() in {".html", ".htm"}:
            nb = fp.stat().st_size if fp.exists() else 0
            if _unlink_file(fp, dry_run):
                n_files += 1
                n_bytes += nb
    return n_files, n_bytes


def cleanup_logs(root: Path, dry_run: bool) -> tuple[int, int]:
    ldir = root / "logs"
    if not ldir.is_dir():
        return 0, 0
    n_files, n_bytes = 0, 0
    for fp in sorted(ldir.iterdir()):
        if not fp.is_file():
            continue
        if fp.suffix.lower() != ".log" and ".log." not in fp.name:
            continue
        nb = fp.stat().st_size
        if _unlink_file(fp, dry_run):
            n_files += 1
            n_bytes += nb
    return n_files, n_bytes


def cleanup_pycache(root: Path, dry_run: bool) -> int:
    n = 0
    for d in _iter_pycache_dirs(root):
        _unlink_dir(d, dry_run)
        n += 1
    return n


def cleanup_tool_caches(root: Path, dry_run: bool) -> int:
    n = 0
    names = (
        ".pytest_cache",
        "htmlcov",
        ".coverage",
        ".mypy_cache",
        ".ruff_cache",
    )
    for name in names:
        p = root / name
        if p.is_dir():
            _unlink_dir(p, dry_run)
            n += 1
        elif p.is_file():
            _unlink_file(p, dry_run)
            n += 1
    return n


def cleanup_multicache_under_data(root: Path, dry_run: bool) -> int:
    mc = root / "data" / "multi_cache"
    if not mc.is_dir():
        return 0
    n = 0
    for child in mc.iterdir():
        if child.name in SKIP_TOPLEVEL_NAMES:
            continue
        if child.is_dir():
            _unlink_dir(child, dry_run)
            n += 1
        elif child.is_file():
            _unlink_file(child, dry_run)
            n += 1
    return n


def cleanup_external_cache_junk(cache_root: Path, dry_run: bool) -> tuple[int, int]:
    """只删已知「运行边角」文件名，不误删 parquet / duckdb."""
    junk_names = {"smart_merge_failed_codes.txt"}
    n_files, n_bytes = 0, 0
    if not cache_root.is_dir():
        return 0, 0
    for name in junk_names:
        fp = cache_root / name
        if fp.is_file():
            nb = fp.stat().st_size
            if _unlink_file(fp, dry_run):
                n_files += 1
                n_bytes += nb
    locks = cache_root / "locks"
    if locks.is_dir():
        for lf in locks.glob("*.lock"):
            nb = lf.stat().st_size if lf.exists() else 0
            if _unlink_file(lf, dry_run):
                n_files += 1
                n_bytes += nb
        try:
            if not dry_run and locks.exists() and not any(locks.iterdir()):
                locks.rmdir()
        except OSError:
            pass
    return n_files, n_bytes


def main() -> int:
    parser = argparse.ArgumentParser(description="清理本仓库运行产物与缓存（默认不动外部行情仓目录）")
    parser.add_argument("--dry-run", action="store_true", help="只打印将要执行的操作")
    parser.add_argument(
        "--no-reports",
        action="store_true",
        help="不清理 reports/*.html",
    )
    parser.add_argument(
        "--no-logs",
        action="store_true",
        help="不清理 logs/ 下轮转日志",
    )
    parser.add_argument(
        "--no-pycache",
        action="store_true",
        help="不删除任何 __pycache__",
    )
    parser.add_argument(
        "--no-tool-caches",
        action="store_true",
        help="不删 .pytest_cache / htmlcov / .coverage / .mypy_cache / .ruff_cache",
    )
    parser.add_argument(
        "--include-local-multi-cache",
        action="store_true",
        help="清空本仓库内的 data/multi_cache（回测常用 Config 仓若在外部磁盘则不受影响）",
    )
    parser.add_argument(
        "--cache-dir-delete-junk",
        metavar="PATH",
        default=None,
        help=(
            "在指定缓存根目录删除运维边角文件 "
            "(如 smart_merge_failed_codes.txt、空的 locks/*.lock)，不删除 parquet/catalog"
        ),
    )
    args = parser.parse_args()
    root = PROJECT_ROOT
    dry = bool(args.dry_run)

    if dry:
        print(f"[dry-run] 项目根: {root}\n")

    total_files = total_bytes = 0

    if not args.no_reports:
        nf, nb = cleanup_reports(root, dry)
        total_files += nf
        total_bytes += nb
        print(f"报告 HTML：删除 {nf} 个文件（约 {nb / 1024:.1f} KB）")

    if not args.no_logs:
        nf, nb = cleanup_logs(root, dry)
        total_files += nf
        total_bytes += nb
        print(f"日志：删除 {nf} 个文件（约 {nb / 1024:.1f} KB）")

    if not args.no_pycache:
        nc = cleanup_pycache(root, dry)
        print(f"__pycache__：移除 {nc} 个目录")

    if not args.no_tool_caches:
        nt = cleanup_tool_caches(root, dry)
        print(f"工具缓存目录/文件：处理 {nt} 项")

    if args.include_local_multi_cache:
        nm = cleanup_multicache_under_data(root, dry)
        print(f"data/multi_cache：移除 {nm} 项子路径")

    if args.cache_dir_delete_junk:
        ext_root = Path(args.cache_dir_delete_junk).expanduser()
        if not ext_root.is_absolute():
            ext_root = (root / ext_root).resolve()
        nf, nb = cleanup_external_cache_junk(ext_root, dry)
        print(f"外部缓存目录边角文件 [{ext_root}]：删除 {nf} 个文件（约 {nb / 1024:.1f} KB）")

    print("\n完成。" + (" （dry-run，未真实删除）" if dry else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
