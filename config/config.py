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
    # 拉数与本地 silver 一律不复权（adjust=0 落盘）。仅在读出时按本字段用 factor 折算 OHLC。
    # 取值：0=不折算；1=前复权；2=后复权（与 bar_store._apply_adjust_to_ohlc 一致）。多因子常用 "1"。
    BACKTEST_ADJUST = "1"

    # 截面因子：分位去极值 + 对 log_size 中性 + z-score
    FACTOR_WINSOR_LOW = 0.01
    FACTOR_WINSOR_HIGH = 0.99
    FACTOR_CS_MIN_NAMES = 40
    FACTOR_IC_REPORT = True
    # 全市场截面：按日滚动 IC 符号对齐 w_*（见 data/factors/ic_report.py:build_rolling_ic_weight_signs）。
    FACTOR_IC_ALIGN_WEIGHTS = True
    # 用过去 window 根「有 IC 的交易日」的滚动均值（且 shift(1)）预计算 sign_*；=0 则关闭 IC 符号对齐。
    FACTOR_IC_ALIGN_ROLLING_WINDOW = 60
    FACTOR_IC_ALIGN_MIN_DAYS = 40
    FACTOR_IC_ALIGN_MIN_ABS_MEAN = 0.0

    # 策略配置：当前工程仅保留多因子策略
    STRATEGY_NAME = "PriceVolumeMultiFactorStrategy"

    STRATEGY_PARAMS = {
        "PriceVolumeMultiFactorStrategy": {
            "holding_count": 12,
            "rank_buffer": 3,
            "score_delta": 0.22,
            "min_hold_days": 5,
            "rebalance_cooldown": 2,
            "w_mom20": 0.24,
            "w_mom60": 0.18,
            "w_vol20": -0.26,
            "w_liq20": 0.14,
            "w_rev20": 0.10,
            "w_dvol20": -0.12,
            "w_amihud20": -0.10,
            "weight_scheme": "exp_score",
            "max_single_weight": 0.12,
            "min_single_weight": 0.02,
            "target_vol_enabled": True,
            "target_vol_annual": 0.18,
            "defense_enabled": True,
            "defense_dd_trigger": 0.12,
            "defense_gross_exposure": 0.58,
            "defense_dd_deep": 0.22,
            "defense_gross_exposure_deep": 0.38,
        },
    }

    # 多因子股票池配置
    UNIVERSE_PREFIX = ("60", "00")
    UNIVERSE_TOPK = 3500
    UNIVERSE_MIN_AMOUNT = 100000000
    UNIVERSE_MIN_TURNOVER = 0.5
    UNIVERSE_USE_LOCAL = True
    # 股票池唯一持久化文件见 data/universe/a_share_codes.csv。
    # 自定义清单：`python run_multifactor.py --manual-csv <路径>`，或将脚本输出直接写入 a_share_codes.csv。
    MULTI_STOCK_CACHE_DIR = r"C:\投资\STOCK_DATA"
    BENCHMARK_SYMBOL = "1.000001"  # 上证综指（东财带前缀代码）
    # 基准（指数）数据源固定使用接口A(api_stock_kline_dc)。
    BENCHMARK_FORCE_API_A = True
    DATA_SAMPLING_CHECK_ENABLED = True
    DATA_SAMPLING_CHECK_POINTS = 5
    DATA_SAMPLING_CHECK_SEED = 42
    DATA_SAMPLING_CHECK_STRICT = True
    DATA_SAMPLING_CHECK_TIMEOUT_S = 20.0

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