"""多因子回测主流程：装配数据、执行回测、生成结果。"""
# backtest_main.py
import sys
import time
import requests
# 自定义模块
from data.orchestration.batch_symbols import get_multiple_stock_data
from data.features.price_factors import add_factor_columns
from data.orchestration.single_symbol import get_stock_data
from data.universe.builder import build_universe_codes
import strategies as strategy_module
from config.config import Config
from backtest.backtest_engine import BacktestEngine
from reports.report_generator import ReportGenerator
from utils.logger import setup_logger, get_trade_logger, get_performance_logger, cleanup_log_files

def get_multifactor_data_feeds():
    """构建多因子策略所需的多标的 data feed 列表。"""
    codes = build_universe_codes(
        prefixes=Config.UNIVERSE_PREFIX,
        top_k=Config.UNIVERSE_TOPK,
        min_amount=Config.UNIVERSE_MIN_AMOUNT,
        min_turnover=Config.UNIVERSE_MIN_TURNOVER,
        use_local=Config.UNIVERSE_USE_LOCAL,
        manual_csv_path=Config.UNIVERSE_MANUAL_CSV_PATH or None,
    )
    if not codes:
        raise RuntimeError("股票池为空，无法运行多因子策略。")

    logger.info("多因子股票池数量: %s", len(codes))
    multi_data = get_multiple_stock_data(
        codes=codes,
        period=Config.DEFAULT_PERIOD,
        start_date=Config.DEFAULT_START_DATE,
        end_date=Config.DEFAULT_END_DATE,
        adjust=Config.DEFAULT_ADJUST,
        ty='个股',
        use_local=True,
        verbose=not getattr(Config, "SMOKE_TEST", False),
        cache_dir_path=Config.MULTI_STOCK_CACHE_DIR,
        continue_on_error=getattr(Config, "SMOKE_TEST", False),
        sampling_check_enabled=Config.DATA_SAMPLING_CHECK_ENABLED,
        sampling_check_points=Config.DATA_SAMPLING_CHECK_POINTS,
        sampling_check_seed=Config.DATA_SAMPLING_CHECK_SEED,
        sampling_check_strict=Config.DATA_SAMPLING_CHECK_STRICT,
        sampling_check_timeout_s=Config.DATA_SAMPLING_CHECK_TIMEOUT_S,
    )
    if not multi_data:
        raise RuntimeError("未获取到任何多标的行情数据。")

    feeds = []
    for code, df in multi_data.items():
        factor_df = add_factor_columns(df)
        if factor_df.empty:
            continue
        feed = strategy_module.MultiFactorPandasData(dataname=factor_df, name=code)
        feeds.append(feed)

    if not feeds:
        raise RuntimeError("有效多标的 data feed 为空，请检查数据区间或股票池过滤条件。")
    return feeds


def get_benchmark_data():
    """获取基准数据"""
    try:
        benchmark_df = get_stock_data(
            codes=Config.BENCHMARK_SYMBOL,
            period=Config.DEFAULT_PERIOD,
            start_date=Config.DEFAULT_START_DATE,
            end_date=Config.DEFAULT_END_DATE,
            adjust=Config.DEFAULT_ADJUST,
            ty='指数',
            use_local=True,
            verbose=False
        )
    except (requests.exceptions.RequestException, ConnectionError, TimeoutError) as exc:
        raise RuntimeError(f"基准数据拉取失败: {exc}") from exc
    except ValueError as exc:
        raise RuntimeError(f"基准数据校验失败: {exc}") from exc

    return benchmark_df

# 初始化日志
logger = setup_logger(__name__)
trade_logger = get_trade_logger()
perf_logger = get_performance_logger()


def main():
    try:
        # 每次运行前清理历史日志，避免日志持续累积
        cleanup_log_files()
        logger.info("=" * 50)
        logger.info("开始回测程序")
        # 记录性能开始
        perf_logger.info("回测开始")
        start_time = time.perf_counter()

        # 1. 初始化回测引擎
        engine = BacktestEngine()

        # 2. 添加多因子策略
        strategy_name = "PriceVolumeMultiFactorStrategy"
        strategy_class = strategy_module.PriceVolumeMultiFactorStrategy
        strategy_params = Config.get_strategy_params()
        engine.add_strategy(
            strategy_class,
            **strategy_params,
        )
        logger.info(f"当前策略: {strategy_name}, 参数: {strategy_params}")

        # 记录数据接口性能
        perf_logger.info("调取数据开始")
        # 3. 添加多标的数据
        data_feeds = get_multifactor_data_feeds()
        for data_feed in data_feeds:
            engine.add_data(data_feed)

        elapsed_time = time.perf_counter() - start_time
        perf_logger.info(f"调取数据耗时{elapsed_time:.4f}秒")

        # 4. 添加分析器
        engine.add_analyzers()

        # 5. 运行回测
        #print(f'初始资金: {engine.cerebro.broker.getvalue():.2f}')

        logger.info(f"初始资金: {Config.INITIAL_CASH}")
        logger.info(f"回测期间: {Config.DEFAULT_START_DATE} 到 {Config.DEFAULT_END_DATE}")
        logger.info("=" * 50)

        strats = engine.run_backtest()
        strat = strats[0]

        # 6. 生成报告
        pyfolio_analyzer = strat.analyzers.pyfolio
        returns, positions, transactions, gross_lev = pyfolio_analyzer.get_pf_items()
        returns.index = returns.index.tz_convert(None)

        # 获取基准数据（接口波动时降级为无基准报告）
        benchmark_returns = None
        try:
            benchmark_df = get_benchmark_data()
            benchmark_returns = ReportGenerator.prepare_benchmark_data(benchmark_df)
            report_gen = ReportGenerator(returns)
            report_gen.returns, benchmark_returns = report_gen.align_strategy_returns_to_benchmark(
                report_gen.returns,
                benchmark_returns,
            )
        except RuntimeError as exc:
            logger.warning("基准数据不可用，降级为无基准报告: %s", exc)
            report_gen = ReportGenerator(returns)

        # 生成报告
        report_gen.benchmark_returns = benchmark_returns

        report_path = report_gen.generate_html_report()
        report_gen.open_in_browser(report_path)

        logger.info("回测完成")

        print(f'最终资金: {engine.cerebro.broker.getvalue():.2f}')
        print(f"HTML 报告已生成: {report_path}")

        # 可选：绘图
        # engine.cerebro.plot(style='bar')

        elapsed_time = time.perf_counter() - start_time
        perf_logger.info("回测结束")
        perf_logger.info(f"执行耗时{elapsed_time:.4f}秒")

    except RuntimeError as e:
        logger.error(f"运行时错误: {e}", exc_info=True)
        sys.exit(2)
    except (ValueError, TypeError) as e:
        logger.error(f"配置或参数错误: {e}", exc_info=True)
        sys.exit(3)
    except Exception as e:
        logger.error(f"程序出错: {e}", exc_info=True)
        sys.exit(1)

if __name__ == '__main__':
    main()