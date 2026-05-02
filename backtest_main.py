"""多因子回测主流程：装配数据、执行回测、生成结果。"""
# backtest_main.py
import os
import platform
import sys
import time
import requests
# 自定义模块
from data.orchestration.batch_symbols import get_multiple_stock_data
from data.features.price_factors import add_factor_columns
from data.factors.ic_report import (
    align_strategy_weights_by_ic_summary,
    build_ic_daily_from_multi,
    ic_summary_from_daily,
    maybe_write_factor_ic_report,
    truncate_ic_daily_for_align,
)
from data.factors.panel import apply_cross_section_to_multi_data
from data.orchestration.single_symbol import get_stock_data
from data.universe.builder import build_universe_codes, UNIVERSE_CACHE_FILE
import strategies as strategy_module
from config.config import Config
from backtest.backtest_engine import BacktestEngine
from reports.report_generator import ReportGenerator
from utils.logger import (
    LoggerConfig,
    get_trade_logger,
    bootstrap_application_logging,
    get_backtest_logger,
    get_debug_logger,
    log_performance_event,
    perf_memory_note,
)


def get_multifactor_data_feeds(manual_csv_path=None):
    """构建多因子策略所需的多标的 data feed 列表，并可选返回用于 IC 符号对齐的摘要表。

    manual_csv_path: 仅由 ``run_multifactor.py --manual-csv`` 传入；规范化后会写入 a_share_codes.csv。

    Returns:
        (feeds, ic_align_summary): ic_align_summary 在关闭 FACTOR_IC_ALIGN_WEIGHTS 时为 None。
    """
    t_u0 = time.perf_counter()
    codes = build_universe_codes(
        prefixes=Config.UNIVERSE_PREFIX,
        top_k=Config.UNIVERSE_TOPK,
        min_amount=Config.UNIVERSE_MIN_AMOUNT,
        min_turnover=Config.UNIVERSE_MIN_TURNOVER,
        use_local=Config.UNIVERSE_USE_LOCAL,
        manual_csv_path=manual_csv_path,
    )
    _perf_data_sub(
        "读取并过滤股票池代码列表",
        "data/universe/builder.py:build_universe_codes",
        t_u0,
    )
    if not codes:
        raise RuntimeError("股票池为空，无法运行多因子策略。")

    plog = get_debug_logger("pipeline")
    plog.debug(
        "universe built: n=%s top_k=%s manual_csv_path=%s",
        len(codes),
        Config.UNIVERSE_TOPK,
        manual_csv_path,
    )
    logger.info("多因子股票池数量: %s", len(codes))
    t_b0 = time.perf_counter()
    _adj = getattr(Config, "BACKTEST_ADJUST", Config.DEFAULT_ADJUST)
    multi_data = get_multiple_stock_data(
        codes=codes,
        period=Config.DEFAULT_PERIOD,
        start_date=Config.DEFAULT_START_DATE,
        end_date=Config.DEFAULT_END_DATE,
        adjust=_adj,
        ty='个股',
        use_local=True,
        verbose=True,
        cache_dir_path=Config.MULTI_STOCK_CACHE_DIR,
        continue_on_error=False,
        sampling_check_enabled=Config.DATA_SAMPLING_CHECK_ENABLED,
        sampling_check_points=Config.DATA_SAMPLING_CHECK_POINTS,
        sampling_check_seed=Config.DATA_SAMPLING_CHECK_SEED,
        sampling_check_strict=Config.DATA_SAMPLING_CHECK_STRICT,
        sampling_check_timeout_s=Config.DATA_SAMPLING_CHECK_TIMEOUT_S,
    )
    _perf_data_sub(
        "批量装载 K 线（阶段 A 逐只 + 阶段 B 在线抽样）",
        "data/orchestration/batch_symbols.py:get_multiple_stock_data",
        t_b0,
    )
    if not multi_data:
        raise RuntimeError("未获取到任何多标的行情数据。")

    plog.debug("batch load done: n_symbols=%s", len(multi_data))
    with_factors = {}
    for code, df in multi_data.items():
        factor_df = add_factor_columns(df)
        if not factor_df.empty:
            with_factors[code] = factor_df
    need_ic = getattr(Config, "FACTOR_IC_REPORT", False) or getattr(
        Config, "FACTOR_IC_ALIGN_WEIGHTS", False
    )
    ic_daily = None
    if need_ic:
        ic_daily = build_ic_daily_from_multi(with_factors)
    maybe_write_factor_ic_report(
        with_factors,
        Config.REPORTS_DIR,
        enabled=getattr(Config, "FACTOR_IC_REPORT", False),
        ic_daily_precomputed=ic_daily,
    )
    ic_align_summary = None
    if getattr(Config, "FACTOR_IC_ALIGN_WEIGHTS", False) and ic_daily is not None and not ic_daily.empty:
        ratio = float(getattr(Config, "FACTOR_IC_ALIGN_PREFIX_RATIO", 1.0))
        sub = truncate_ic_daily_for_align(ic_daily, ratio)
        ic_align_summary = ic_summary_from_daily(sub)
    cs_data = apply_cross_section_to_multi_data(
        with_factors,
        winsor_low=getattr(Config, "FACTOR_WINSOR_LOW", 0.01),
        winsor_high=getattr(Config, "FACTOR_WINSOR_HIGH", 0.99),
        min_names_per_day=getattr(Config, "FACTOR_CS_MIN_NAMES", 40),
    )
    feeds = []
    t_f0 = time.perf_counter()
    for code, factor_df in cs_data.items():
        if factor_df.empty:
            continue
        feed = strategy_module.MultiFactorPandasData(dataname=factor_df, name=code)
        feeds.append(feed)
    _perf_data_sub(
        "计算因子列并构造 backtrader 数据源对象",
        "data/features/price_factors.py + data/factors/panel.py → strategies.MultiFactorPandasData",
        t_f0,
    )

    if not feeds:
        raise RuntimeError("有效多标的 data feed 为空，请检查数据区间或股票池过滤条件。")
    plog.debug("feeds built: n=%s", len(feeds))
    return feeds, ic_align_summary


def get_benchmark_data():
    """获取基准数据"""
    try:
        benchmark_df = get_stock_data(
            codes=Config.BENCHMARK_SYMBOL,
            period=Config.DEFAULT_PERIOD,
            start_date=Config.DEFAULT_START_DATE,
            end_date=Config.DEFAULT_END_DATE,
            adjust=getattr(Config, "BACKTEST_ADJUST", Config.DEFAULT_ADJUST),
            ty='指数',
            use_local=True,
            verbose=False
        )
    except (requests.exceptions.RequestException, ConnectionError, TimeoutError) as exc:
        raise RuntimeError(f"基准数据拉取失败: {exc}") from exc
    except ValueError as exc:
        raise RuntimeError(f"基准数据校验失败: {exc}") from exc

    return benchmark_df

# 主回测流水（与 logs/backtest.log 对齐；完整 handler 在 bootstrap_application_logging 后挂载）
logger = get_backtest_logger()
trade_logger = get_trade_logger()


def _perf_phase(step: str, code: str, t0: float):
    """主流程阶段耗时，写入 performance.log。"""
    t1 = time.perf_counter()
    dt = t1 - t0
    log_performance_event(
        "backtest_main.py",
        kind="主阶段",
        step=step,
        code=code,
        elapsed_s=dt,
    )
    return dt, t1


def _perf_data_sub(
    step: str,
    code: str,
    t0: float,
    *,
    scope: str = "get_multifactor_data_feeds()",
):
    """数据管线内子阶段耗时（均在 backtest_main 的数据装配路径）。"""
    t1 = time.perf_counter()
    dt = t1 - t0
    log_performance_event(
        "backtest_main.py",
        kind="数据子阶段",
        step=step,
        code=f"{scope} | {code}",
        elapsed_s=dt,
    )
    return dt, t1


def main(manual_csv_path=None, refresh_universe=False):
    try:
        bootstrap_application_logging(debug_mode=getattr(Config, "DEBUG_MODE", False))
        if manual_csv_path:
            logger.info(
                "手动股票池 CSV: %s → 规范化写入 %s",
                manual_csv_path,
                UNIVERSE_CACHE_FILE,
            )
        dt_pool = 0.0
        if refresh_universe:
            tq = time.perf_counter()
            n_u = len(
                build_universe_codes(
                    prefixes=Config.UNIVERSE_PREFIX,
                    top_k=Config.UNIVERSE_TOPK,
                    min_amount=Config.UNIVERSE_MIN_AMOUNT,
                    min_turnover=Config.UNIVERSE_MIN_TURNOVER,
                    use_local=False,
                    manual_csv_path=manual_csv_path,
                )
            )
            logger.info("股票池已刷新: %s 只", n_u)
            dt_pool, _ = _perf_data_sub(
                "入口：按配置强制刷新股票池（写缓存前）",
                "data/universe/builder.py:build_universe_codes(use_local=False)",
                tq,
                scope="main()",
            )
        elif not manual_csv_path:
            logger.info("股票池缓存文件: %s", UNIVERSE_CACHE_FILE)
            logger.info("默认使用本地缓存，不存在时才抓取。")
        else:
            logger.info("将用 --manual-csv 更新缓存后回测（见上）。")

        get_debug_logger("pipeline").debug(
            "main start manual_csv_path=%s refresh_universe=%s",
            manual_csv_path,
            refresh_universe,
        )
        logger.info("=" * 50)
        logger.info("开始回测程序")
        wall0 = time.perf_counter()
        log_performance_event(
            "backtest_main.py",
            kind="里程碑",
            step="多因子回测主流程计时起点",
            code="入口脚本通常为 run_multifactor.py → backtest_main.main()；亦可 python backtest_main.py",
        )
        log_performance_event(
            "backtest_main.py",
            kind="环境",
            step="Python 解释器与 Config 关键项（用于对照运行环境）",
            code="config/config.py:Config",
            metrics=(
                f"python={sys.version.split()[0]} | platform={platform.platform()} | "
                f"UNIVERSE_TOPK={Config.UNIVERSE_TOPK} | "
                f"区间={Config.DEFAULT_START_DATE}~{Config.DEFAULT_END_DATE} | "
                f"MULTI_STOCK_CACHE_DIR={Config.MULTI_STOCK_CACHE_DIR}"
            ),
        )
        perf_memory_note("环境摘要写入后", proc="main()")

        t = wall0
        # 1. 初始化回测引擎
        engine = BacktestEngine()

        # 2. 多标的数据与 Feed（先于策略：全市场截面 IC 可对权重做符号对齐）
        data_feeds, ic_align_summary = get_multifactor_data_feeds(manual_csv_path=manual_csv_path)
        dt_feeds, t = _perf_phase(
            "整段：股票池 + 批量行情 + 因子列 + Feed 列表（见数据子阶段明细）",
            "backtest_main.py:get_multifactor_data_feeds()",
            t,
        )
        perf_memory_note("数据管线完成后", proc="main()")

        strategy_name = "PriceVolumeMultiFactorStrategy"
        strategy_class = strategy_module.PriceVolumeMultiFactorStrategy
        strategy_params = dict(Config.get_strategy_params())
        if getattr(Config, "FACTOR_IC_ALIGN_WEIGHTS", False) and ic_align_summary is not None:
            strategy_params, signs = align_strategy_weights_by_ic_summary(
                strategy_params,
                ic_align_summary,
                min_days=int(getattr(Config, "FACTOR_IC_ALIGN_MIN_DAYS", 40)),
                min_abs_mean=float(getattr(Config, "FACTOR_IC_ALIGN_MIN_ABS_MEAN", 0.0)),
            )
            if signs:
                logger.info(
                    "[因子IC对齐] 已按全市场截面 mean_ic 符号调整权重: %s | 完整参数: %s",
                    signs,
                    strategy_params,
                )
            else:
                logger.info("[因子IC对齐] 无因子满足 min_days/min_abs_mean，保持 Config 原权重。")
        engine.add_strategy(strategy_class, **strategy_params)
        logger.info("当前策略: %s, 参数: %s", strategy_name, strategy_params)
        dt_engine, t = _perf_phase(
            "创建回测引擎并注册多因子策略",
            "backtest/backtest_engine.py:BacktestEngine + add_strategy → strategies.PriceVolumeMultiFactorStrategy",
            t,
        )

        for data_feed in data_feeds:
            engine.add_data(data_feed)
        dt_adddata, t = _perf_phase(
            "向 Cerebro 注册每个标的的 PandasData",
            "backtest/backtest_engine.py:add_data（循环 MultiFactorPandasData）",
            t,
        )

        # 4. 添加分析器
        engine.add_analyzers()
        dt_analyzers, t = _perf_phase(
            "注册 PyFolio 等分析器",
            "backtest/backtest_engine.py:add_analyzers → cerebro.addanalyzer",
            t,
        )

        # 5. 运行回测
        #print(f'初始资金: {engine.cerebro.broker.getvalue():.2f}')

        logger.info(f"初始资金: {Config.INITIAL_CASH}")
        logger.info(f"回测期间: {Config.DEFAULT_START_DATE} 到 {Config.DEFAULT_END_DATE}")
        logger.info("=" * 50)

        t_run0 = time.perf_counter()
        if getattr(Config, "PERF_CPROFILE", False):
            import cProfile
            import pstats
            from io import StringIO

            pr = cProfile.Profile()
            pr.enable()
            try:
                strats = engine.run_backtest()
            finally:
                pr.disable()
            stream = StringIO()
            ps = pstats.Stats(pr, stream=stream).sort_stats(pstats.SortKey.CUMULATIVE)
            ps.print_stats(50)
            os.makedirs(LoggerConfig.LOG_DIR, exist_ok=True)
            outp = os.path.join(LoggerConfig.LOG_DIR, "perf_cprofile_run.txt")
            with open(outp, "w", encoding="utf-8") as fh:
                fh.write(stream.getvalue())
            log_performance_event(
                "backtest_main.py",
                kind="诊断",
                step="cProfile 已落盘（包裹 engine.run_backtest）",
                code="标准库 cProfile + pstats（累计时间 Top 50）",
                metrics=f"输出文件={outp}",
            )
        else:
            strats = engine.run_backtest()
        dt_run, t = _perf_phase(
            "Cerebro 回测主循环（逐 bar 驱动策略）",
            "backtest/backtest_engine.py:run_backtest → cerebro.run",
            t_run0,
        )
        perf_memory_note("Cerebro 主循环结束后", proc="main()")
        strat = strats[0]

        # 6. 生成报告
        t_pf0 = time.perf_counter()
        pyfolio_analyzer = strat.analyzers.pyfolio
        returns, positions, transactions, gross_lev = pyfolio_analyzer.get_pf_items()
        returns.index = returns.index.tz_convert(None)
        dt_pyfolio, t = _perf_phase(
            "从分析器抽取收益、持仓、成交供报告使用",
            "strategies 回测结果: strat.analyzers.pyfolio.get_pf_items()",
            t_pf0,
        )

        benchmark_returns = None
        t_bench0 = time.perf_counter()
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
        dt_bench, t = _perf_phase(
            "拉取基准指数并做收益序列对齐",
            "backtest_main.py:get_benchmark_data + reports/report_generator.py:ReportGenerator.align_strategy_returns_to_benchmark",
            t_bench0,
        )

        report_gen.benchmark_returns = benchmark_returns

        t_html0 = time.perf_counter()
        report_path = report_gen.generate_html_report()
        report_gen.open_in_browser(report_path)
        dt_html, t = _perf_phase(
            "生成 HTML 绩效报告并尝试用系统浏览器打开",
            "reports/report_generator.py:ReportGenerator.generate_html_report + open_in_browser",
            t_html0,
        )
        dt_report = dt_pyfolio + dt_bench + dt_html

        logger.info("回测完成")

        logger.info("最终资金: %.2f", engine.cerebro.broker.getvalue())
        logger.info("HTML 报告已生成: %s", report_path)

        wall = time.perf_counter() - wall0
        perf_memory_note("报告与浏览器处理完成后、汇总前", proc="main()")
        log_performance_event(
            "backtest_main.py",
            kind="里程碑",
            step="多因子回测主流程计时结束",
            code="backtest_main.py:main() 内 wall 时钟统计闭合点",
        )
        log_performance_event(
            "backtest_main.py",
            kind="汇总",
            step="本轮回测注册的标的数量",
            metrics=f"feeds={len(data_feeds)}",
        )
        log_performance_event(
            "backtest_main.py",
            kind="汇总",
            step="wall 总耗时（从「回测开始」里程碑到本节点）",
            code="time.perf_counter() 差分",
            elapsed_s=wall,
        )
        accounted = (
            dt_pool
            + dt_engine
            + dt_feeds
            + dt_adddata
            + dt_analyzers
            + dt_run
            + dt_report
        )
        gap = max(0.0, wall - accounted)
        if wall > 0:

            def _pct(dt: float) -> float:
                return 100.0 * dt / wall

            log_performance_event(
                "backtest_main.py",
                kind="汇总",
                step="占比 1/2｜股票池 → 分析器（占 wall）",
                code="百分比 = 各阶段秒数 / wall；与上文「主阶段」耗时一一对应",
                metrics=(
                    f"股票池刷新={_pct(dt_pool):>5.1f}% | 引擎+策略={_pct(dt_engine):>5.1f}% | "
                    f"数据管线={_pct(dt_feeds):>5.1f}% | 追加数据源={_pct(dt_adddata):>5.1f}% | "
                    f"分析器={_pct(dt_analyzers):>5.1f}%"
                ),
            )
            log_performance_event(
                "backtest_main.py",
                kind="汇总",
                step="占比 2/2｜回测主循环 → 报告与间隙（占 wall）",
                metrics=(
                    f"cerebro运行={_pct(dt_run):>5.1f}% | pyfolio={_pct(dt_pyfolio):>5.1f}% | "
                    f"基准对齐={_pct(dt_bench):>5.1f}% | HTML={_pct(dt_html):>5.1f}% | "
                    f"未计间隙={_pct(gap):>5.1f}%"
                ),
            )

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