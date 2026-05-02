"""项目全局配置：集中管理回测、数据与策略参数。"""
# config/config.py
import os
import datetime


class Config:
    # 回测参数
    INITIAL_CASH = 100000.0
    COMMISSION = 0.0003
    SLIPPAGE = 0.001

    # 数据参数
    DEFAULT_START_DATE = '2025-04-15'
    DEFAULT_END_DATE = '2026-04-15'
    DEFAULT_PERIOD = '1d'
    # 本地主数据源（new_dataa）是无复权口径。
    # DEFAULT_ADJUST 取值说明：
    # 0 = raw（无复权）：使用原始价格；适合与原始成交额/量等字段保持一致做核对。
    # 1 = fwd（前复权）：历史价格按最新复权因子对齐；策略回测最常用，收益曲线更平滑可比。
    # 2 = bwd（后复权）：历史价格按当期因子累计；适合长期序列连续性分析与历史价格形态观察。
    DEFAULT_ADJUST = "0"
    DATA_IS_UNADJUSTED = True

    # 策略配置：当前工程仅保留多因子策略
    STRATEGY_NAME = "PriceVolumeMultiFactorStrategy"

    STRATEGY_PARAMS = {
        "PriceVolumeMultiFactorStrategy": {
            "holding_count": 5,
            "rank_buffer": 2,
            "score_delta": 0.20,
            "min_hold_days": 5,
            "rebalance_cooldown": 2,
            "w_mom20": 0.35,
            "w_mom60": 0.25,
            "w_vol20": -0.20,
            "w_liq20": 0.20,
        },
    }

    # 多因子股票池配置
    UNIVERSE_PREFIX = ("60", "00")
    UNIVERSE_TOPK = 3000
    UNIVERSE_MIN_AMOUNT = 100000000
    UNIVERSE_MIN_TURNOVER = 0.5
    UNIVERSE_USE_LOCAL = True
    # 股票池唯一持久化文件见 data/universe/a_share_codes.csv。
    # 自定义清单：`python run_multifactor.py --manual-csv <路径>`，或将脚本输出直接写入 a_share_codes.csv。
    MULTI_STOCK_CACHE_DIR = r"C:\投资\STOCK_DATA"
    BENCHMARK_SYMBOL = "1.000300"
    # 基准（指数）数据源固定使用接口A(api_stock_kline_dc)。
    BENCHMARK_FORCE_API_A = True
    DATA_SAMPLING_CHECK_ENABLED = True
    DATA_SAMPLING_CHECK_POINTS = 5
    DATA_SAMPLING_CHECK_SEED = 42
    DATA_SAMPLING_CHECK_STRICT = True
    DATA_SAMPLING_CHECK_TIMEOUT_S = 20.0

    # 本地冒烟：`python run_multifactor.py --smoke` 会运行时置 True，勿手改日常使用
    SMOKE_TEST = False
    # 调试：`python run_multifactor.py --debug` 会置 True；分类日志写入 logs/debug/*.log
    DEBUG_MODE = False

    # 性能诊断（写入 logs/performance.log；依赖 psutil 记录 RSS）
    PERF_MEMORY_SNAPSHOT = True
    # 为 True 时对 cerebro.run 做 cProfile，结果写入 logs/perf_cprofile_run.txt（体量可能较大）
    PERF_CPROFILE = False

    # 文件路径
    DATA_DIR = "data"
    STRATEGIES_DIR = "strategies"
    REPORTS_DIR = "reports"  # 新增报告目录

    @classmethod
    def get_report_path(cls):
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        report_filename = f"{cls.get_strategy_name()}_{timestamp}.html"
        return os.path.join(cls.REPORTS_DIR, report_filename)

    @classmethod
    def get_strategy_name(cls):
        return cls.STRATEGY_NAME

    @classmethod
    def get_strategy_params(cls):
        return cls.STRATEGY_PARAMS.get(cls.STRATEGY_NAME, {})