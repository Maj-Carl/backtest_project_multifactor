"""单标的日线行情编排入口。"""

from __future__ import annotations

from pathlib import Path

from config.config import Config
from data.fetch.api_keys import load_api_key_from_file
from data.storage.bar_store import load_or_update_bars
from utils.logger import get_backtest_logger


def get_stock_data(
    key=None,
    codes="603978",
    period="1d",
    start_date="2025-03-23",
    end_date="2025-05-23",
    adjust="0",
    ty="个股",
    use_local=True,
    verbose=True,
):
    """单标的：补齐/读取 OHLC（经 bar_store），返回 ``date`` 为索引的 DataFrame。"""
    resolved_key = key or load_api_key_from_file()
    cache_dir = Path(Config.MULTI_STOCK_CACHE_DIR)
    df = load_or_update_bars(
        codes,
        start_date,
        end_date,
        cache_dir=cache_dir,
        period=period,
        adjust=adjust,
        ty=ty,
        key=resolved_key,
        use_local=use_local,
        verbose=verbose,
    )
    if verbose:
        log = get_backtest_logger()
        log.info("共获取到 %s 条数据", len(df))
        log.info("数据前5行:\n%s", df.head().to_string())

    df.set_index("date", inplace=True)
    return df


__all__ = ["get_stock_data"]
