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
    UNIVERSE_TOPK = 200
    UNIVERSE_MIN_AMOUNT = 100000000
    UNIVERSE_MIN_TURNOVER = 0.5
    UNIVERSE_USE_LOCAL = True
    UNIVERSE_MANUAL_CSV_PATH = "data/universe/manual_universe_template.csv"
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