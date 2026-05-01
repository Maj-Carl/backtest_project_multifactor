"""多因子参数扫描脚本：批量回测并输出推荐参数。"""
import itertools
import os
import sys
import time
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backtest.backtest_engine import BacktestEngine
from config.config import Config
from data.orchestration.batch_symbols import get_multiple_stock_data
from data.features.price_factors import add_factor_columns
from data.universe.builder import build_universe_codes
from strategies.multifactor_strategy import MultiFactorPandasData, PriceVolumeMultiFactorStrategy

FALLBACK_CODES = [
    "600000", "600036", "600519", "600276", "600031",
    "600887", "600309", "600905", "601166", "601318",
    "601688", "601888", "601899", "601985", "601857",
    "000001", "000002", "000063", "000333", "000651",
    "000725", "000858", "000938", "000977", "002415",
]


def _prepare_factor_data(top_k):
    try:
        codes = build_universe_codes(
            prefixes=Config.UNIVERSE_PREFIX,
            top_k=top_k,
            min_amount=Config.UNIVERSE_MIN_AMOUNT,
            min_turnover=Config.UNIVERSE_MIN_TURNOVER,
            use_local=Config.UNIVERSE_USE_LOCAL,
            manual_csv_path=Config.UNIVERSE_MANUAL_CSV_PATH or None,
        )
    except RuntimeError:
        codes = FALLBACK_CODES[: max(top_k, 10)]
    if not codes:
        raise RuntimeError("股票池为空，无法参数扫描。")

    multi_data = get_multiple_stock_data(
        codes=codes,
        period=Config.DEFAULT_PERIOD,
        start_date=Config.DEFAULT_START_DATE,
        end_date=Config.DEFAULT_END_DATE,
        adjust="0",
        ty="个股",
        use_local=True,
        verbose=False,
        cache_dir_path=Config.MULTI_STOCK_CACHE_DIR,
    )
    prepared = {}
    for code, df in multi_data.items():
        factor_df = add_factor_columns(df).dropna()
        if not factor_df.empty:
            prepared[code] = factor_df
    return prepared


def _run_single(prepared_data, holding_count, score_delta):
    engine = BacktestEngine()
    engine.add_strategy(
        PriceVolumeMultiFactorStrategy,
        holding_count=holding_count,
        score_delta=score_delta,
        rank_buffer=Config.STRATEGY_PARAMS["PriceVolumeMultiFactorStrategy"]["rank_buffer"],
        min_hold_days=Config.STRATEGY_PARAMS["PriceVolumeMultiFactorStrategy"]["min_hold_days"],
        rebalance_cooldown=Config.STRATEGY_PARAMS["PriceVolumeMultiFactorStrategy"]["rebalance_cooldown"],
        w_mom20=Config.STRATEGY_PARAMS["PriceVolumeMultiFactorStrategy"]["w_mom20"],
        w_mom60=Config.STRATEGY_PARAMS["PriceVolumeMultiFactorStrategy"]["w_mom60"],
        w_vol20=Config.STRATEGY_PARAMS["PriceVolumeMultiFactorStrategy"]["w_vol20"],
        w_liq20=Config.STRATEGY_PARAMS["PriceVolumeMultiFactorStrategy"]["w_liq20"],
    )
    engine.add_analyzers()
    for code, df in prepared_data.items():
        feed = MultiFactorPandasData(dataname=df.copy(), name=code)
        engine.add_data(feed)

    strats = engine.run_backtest()
    strat = strats[0]
    final_value = engine.cerebro.broker.getvalue()
    annual = strat.analyzers.AnnualReturn.get_analysis()
    annual_return = sum(annual.values()) / len(annual) if annual else 0.0
    drawdown = strat.analyzers.DrawDown.get_analysis().get("max", {}).get("drawdown", 0.0)
    sharpe = strat.analyzers.SharpeRatio.get_analysis().get("sharperatio", 0.0) or 0.0
    return {
        "final_value": float(final_value),
        "avg_annual_return": float(annual_return),
        "max_drawdown": float(drawdown),
        "sharpe": float(sharpe),
    }


def main():
    start = time.perf_counter()
    top_k_grid = [80, 120]
    holding_count_grid = [3, 5, 8]
    score_delta_grid = [0.2, 0.3, 0.4]

    results = []
    for top_k in top_k_grid:
        prepared_data = _prepare_factor_data(top_k=top_k)
        for holding_count, score_delta in itertools.product(holding_count_grid, score_delta_grid):
            metrics = _run_single(prepared_data=prepared_data, holding_count=holding_count, score_delta=score_delta)
            row = {"top_k": top_k, "holding_count": holding_count, "score_delta": score_delta, **metrics}
            results.append(row)
            print(
                f"top_k={top_k}, holding={holding_count}, delta={score_delta} -> "
                f"value={metrics['final_value']:.2f}, sharpe={metrics['sharpe']:.4f}, "
                f"mdd={metrics['max_drawdown']:.2f}"
            )

    result_df = pd.DataFrame(results)
    result_df.sort_values(by=["sharpe", "final_value"], ascending=False, inplace=True)
    os.makedirs("reports", exist_ok=True)
    result_path = "reports/multifactor_tuning_results.csv"
    result_df.to_csv(result_path, index=False, encoding="utf-8-sig")

    best = result_df.iloc[0].to_dict() if not result_df.empty else {}
    print("\n参数扫描完成。")
    print(f"结果文件: {result_path}")
    if best:
        print(
            "推荐参数: "
            f"top_k={int(best['top_k'])}, "
            f"holding_count={int(best['holding_count'])}, "
            f"score_delta={best['score_delta']}"
        )
    print(f"总耗时: {time.perf_counter() - start:.2f} 秒")


if __name__ == "__main__":
    main()
