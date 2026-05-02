"""日志系统：提供回测日志、交易日志和性能日志能力。"""
# utils/logger.py
import logging
import logging.handlers
import os
import re
from typing import Optional


class LoggerConfig:
    """日志配置类"""
    # 日志级别
    LOG_LEVEL = logging.INFO

    # 调试模式：为 True 时 ``get_debug_logger(category)`` 写入 logs/debug/<category>.log
    DEBUG_MODE = False

    # 日志格式
    LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    DATE_FORMAT = '%Y-%m-%d %H:%M:%S'

    # 分类调试日志格式（带模块名便于检索）
    DEBUG_LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'

    # 文件路径
    LOG_DIR = "logs"
    LOG_FILE = "backtest.log"
    # 主回测流水 logger 名称（与终端一致，写入 LOG_FILE）
    BACKTEST_LOGGER_NAME = "backtest"

    # 文件大小限制 (10MB)
    MAX_BYTES = 10 * 1024 * 1024
    BACKUP_COUNT = 5  # 保留5个备份文件

    # 是否输出到控制台
    CONSOLE_OUTPUT = True

    @classmethod
    def get_log_path(cls):
        """获取完整日志路径"""
        os.makedirs(cls.LOG_DIR, exist_ok=True)
        return os.path.join(cls.LOG_DIR, cls.LOG_FILE)


def cleanup_log_files(log_dir: Optional[str] = None):
    """
    清理日志目录中的历史日志文件（含轮转日志）。
    每次运行前调用，可避免日志无限累积。

    注意：会移除所有已注册 logger 的 handler。常规入口请使用
    ``bootstrap_application_logging``，勿在 cleanup 之后忘记重新挂载 handler。
    """
    target_dir = log_dir or LoggerConfig.LOG_DIR
    os.makedirs(target_dir, exist_ok=True)

    # Windows 下若文件被 logger handler 占用，删除会失败。
    # 先释放已存在 logger 的全部 handler。
    for logger_obj in list(logging.Logger.manager.loggerDict.values()):
        if isinstance(logger_obj, logging.Logger):
            for handler in list(logger_obj.handlers):
                try:
                    handler.close()
                except Exception:
                    pass
                logger_obj.removeHandler(handler)

    root_logger = logging.getLogger()
    for handler in list(root_logger.handlers):
        try:
            handler.close()
        except Exception:
            pass
        root_logger.removeHandler(handler)

    for file_name in os.listdir(target_dir):
        file_path = os.path.join(target_dir, file_name)
        if not os.path.isfile(file_path):
            continue
        # 清理 *.log 与 *.log.N 轮转文件
        if ".log" in file_name:
            try:
                os.remove(file_path)
            except OSError:
                pass

    debug_sub = os.path.join(target_dir, "debug")
    if os.path.isdir(debug_sub):
        for file_name in os.listdir(debug_sub):
            file_path = os.path.join(debug_sub, file_name)
            if os.path.isfile(file_path) and ".log" in file_name:
                try:
                    os.remove(file_path)
                except OSError:
                    pass

    for extra in ("perf_cprofile_run.txt",):
        ep = os.path.join(target_dir, extra)
        if os.path.isfile(ep):
            try:
                os.remove(ep)
            except OSError:
                pass


_LOGGING_BOOTSTRAPPED = False


def init_debug_logging(enabled: bool) -> None:
    """与 ``Config.DEBUG_MODE`` 对齐；须在进程早期调用，``get_debug_logger`` 才按开关落盘。"""
    LoggerConfig.DEBUG_MODE = bool(enabled)


def bootstrap_application_logging(*, debug_mode: bool) -> None:
    """清理旧日志并一次性挂载主回测 / 交易 / 性能 handler。

    应在任意 ``get_backtest_logger().info`` 之前调用（且进程内通常只调用一次）。
    """
    global _LOGGING_BOOTSTRAPPED
    if _LOGGING_BOOTSTRAPPED:
        return
    cleanup_log_files()
    init_debug_logging(debug_mode)
    setup_logger(LoggerConfig.BACKTEST_LOGGER_NAME)
    get_trade_logger()
    get_performance_logger()
    _LOGGING_BOOTSTRAPPED = True


def get_backtest_logger() -> logging.Logger:
    """主回测与数据装载终端流水：写入 ``logs/backtest.log``，并按配置同步到控制台。"""
    return setup_logger(LoggerConfig.BACKTEST_LOGGER_NAME)


def _sanitize_debug_category(category: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9_-]+", "_", (category or "misc").strip())
    s = s.strip("_") or "misc"
    return s[:48]


def get_debug_logger(category: str = "general") -> logging.Logger:
    """按分类写入 ``logs/debug/<category>.log``（仅 ``DEBUG_MODE`` 为 True 时记录 DEBUG 级别）。

    建议分类名：``universe``（股票池）、``batch``（多标装载进度）、``bars``（单标补缺）、
    ``pipeline``（回测主流程节点）、``sampling``（在线抽样）等。
    """
    cat = _sanitize_debug_category(category)
    name = f"debug.{cat}"
    log = logging.getLogger(name)
    log.propagate = False

    if log.handlers:
        return log

    if not LoggerConfig.DEBUG_MODE:
        log.setLevel(logging.CRITICAL + 1)
        log.addHandler(logging.NullHandler())
        return log

    log.setLevel(logging.DEBUG)
    debug_dir = os.path.join(LoggerConfig.LOG_DIR, "debug")
    os.makedirs(debug_dir, exist_ok=True)
    log_path = os.path.join(debug_dir, f"{cat}.log")
    formatter = logging.Formatter(LoggerConfig.DEBUG_LOG_FORMAT, datefmt=LoggerConfig.DATE_FORMAT)
    fh = logging.handlers.RotatingFileHandler(
        log_path,
        maxBytes=LoggerConfig.MAX_BYTES,
        backupCount=LoggerConfig.BACKUP_COUNT,
        encoding="utf-8",
    )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(formatter)
    log.addHandler(fh)
    return log


def setup_logger(
        name: str = __name__,
        log_level: Optional[int] = None,
        log_file: Optional[str] = None,
        console_output: Optional[bool] = None
) -> logging.Logger:
    """
    设置并返回配置好的logger

    Args:
        name: logger名称，通常使用模块名(__name__)
        log_level: 日志级别，默认使用LoggerConfig.LOG_LEVEL
        log_file: 日志文件路径，默认使用LoggerConfig.get_log_path()
        console_output: 是否输出到控制台，默认使用LoggerConfig.CONSOLE_OUTPUT

    Returns:
        配置好的logger实例
    """
    # 使用配置或传入参数
    level = log_level if log_level is not None else LoggerConfig.LOG_LEVEL
    log_path = log_file if log_file is not None else LoggerConfig.get_log_path()
    console = console_output if console_output is not None else LoggerConfig.CONSOLE_OUTPUT

    # 创建logger
    logger = logging.getLogger(name)
    logger.setLevel(level)

    # 避免重复添加handler
    if logger.handlers:
        return logger

    # 创建formatter
    formatter = logging.Formatter(
        LoggerConfig.LOG_FORMAT,
        datefmt=LoggerConfig.DATE_FORMAT
    )

    # 文件处理器 - 按大小轮转
    file_handler = logging.handlers.RotatingFileHandler(
        log_path,
        maxBytes=LoggerConfig.MAX_BYTES,
        backupCount=LoggerConfig.BACKUP_COUNT,
        encoding='utf-8'
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # 控制台处理器
    if console:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(level)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    return logger


def get_performance_logger():
    """获取性能专用的logger - 不继承根logger的设置"""
    # 创建独立的logger，不继承root logger
    logger = logging.getLogger("performance")
    logger.setLevel(logging.INFO)
    logger.propagate = False  # 重要：不传播到父logger

    # 如果已经有handler，直接返回
    if logger.handlers:
        return logger

    # 性能日志使用单独的文件
    perf_file = os.path.join(LoggerConfig.LOG_DIR, "performance.log")

    # 确保目录存在
    os.makedirs(LoggerConfig.LOG_DIR, exist_ok=True)

    # 文件处理器
    file_handler = logging.handlers.RotatingFileHandler(
        perf_file,
        maxBytes=LoggerConfig.MAX_BYTES,
        backupCount=LoggerConfig.BACKUP_COUNT,
        encoding='utf-8'
    )

    # 性能日志格式（更简洁）
    formatter = logging.Formatter(
        '%(asctime)s - %(message)s',
        datefmt=LoggerConfig.DATE_FORMAT
    )
    file_handler.setFormatter(formatter)

    # 添加handler
    logger.addHandler(file_handler)

    return logger


def _perf_time_left_column(elapsed_s: float | None) -> str:
    """性能日志左侧「耗时」列：有秒数则右对齐数字，无则占位，便于扫一眼对比。"""
    if elapsed_s is None:
        return "耗时=        (n/a)"
    return f"耗时={elapsed_s:>12.4f}s"


_RSS_INNER_W = 10  # RSS_MB= 右侧固定宽度，与数值行对齐


def _perf_rss_column_value(rss_mb: float | None, rss_error: str) -> str:
    """第二列 RSS：有采样值写 MB；采样失败写占位；非内存类统一 ``n/a`` 对齐。"""
    if rss_mb is not None:
        inner = f"{rss_mb:>{_RSS_INNER_W}.1f}"
    elif rss_error:
        inner = f"{'(err)':>{_RSS_INNER_W}}"
    else:
        inner = f"{'n/a':>{_RSS_INNER_W}}"
    return f"RSS_MB={inner}"


def log_performance_event(
    origin: str,
    *,
    kind: str,
    step: str,
    code: str = "",
    elapsed_s: float | None = None,
    metrics: str = "",
    rss_mb: float | None = None,
    rss_error: str = "",
) -> None:
    """写入 ``performance.log`` 的统一格式，便于对照源码与入口脚本。

    行首为 **耗时**；**第二列恒为 RSS_MB**（非内存类填 ``n/a`` 占位；内存类写数值；失败时占位 + ``RSS_err=``）。

    - ``origin``：相对项目根的文件路径风格，例如 ``backtest_main.py``。
    - ``kind``：主阶段 / 数据子阶段 / 批量行情 / 环境 / 里程碑 / 诊断 / 汇总 / 内存快照。
    - ``step``：用一句话说明在做什么。
    - ``code``：涉及的模块、类或函数（文件路径:符号），便于打开对应源码。
    - ``metrics``：补充键值（无耗时字段时放统计摘要、环境键值等）。
    - ``rss_mb``：可选；进程 RSS（MB），仅内存类行传入。
    - ``rss_error``：RSS 采样失败时的简短原因（与成功时的 ``rss_mb`` 互斥；失败时仍占第二列占位符）。
    """
    parts = [_perf_time_left_column(elapsed_s), _perf_rss_column_value(rss_mb, rss_error)]
    if rss_error:
        err = rss_error.replace("\n", " ").strip()
        if len(err) > 160:
            err = err[:157] + "..."
        tail = f"RSS_err={err}"
        metrics = f"{metrics} | {tail}" if metrics else tail
    parts.extend(
        [
            f"来源={origin}",
            f"类型={kind}",
            f"步骤={step}",
        ]
    )
    if code:
        parts.append(f"代码={code}")
    if metrics:
        parts.append(metrics)
    get_performance_logger().info(" | ".join(parts))


def perf_memory_note(
    label: str,
    *,
    origin: str = "backtest_main.py",
    proc: str = "main()",
) -> None:
    """将当前进程 RSS（MB）写入 performance.log；受 ``Config.PERF_MEMORY_SNAPSHOT`` 控制。

    即使 ``psutil`` 不可用或采样抛错，仍会写一行 **内存快照**（``RSS_MB=—`` + ``RSS_err=``），避免静默无输出。
    """
    try:
        from config.config import Config

        if not getattr(Config, "PERF_MEMORY_SNAPSHOT", True):
            return
    except Exception:
        return

    rss_mb: float | None = None
    rss_error = ""
    try:
        import psutil

        rss_mb = psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)
    except Exception as exc:
        rss_error = f"{type(exc).__name__}: {exc}"

    log_performance_event(
        origin,
        kind="内存快照",
        step=label,
        code=f"utils/logger.py:perf_memory_note | 入口函数={proc} | psutil.Process.memory_info().rss",
        rss_mb=rss_mb,
        rss_error=rss_error if rss_mb is None else "",
    )


def get_trade_logger():
    """获取交易专用的logger - 不继承根logger的设置"""
    # 创建独立的logger，不继承root logger
    logger = logging.getLogger("trading")
    logger.setLevel(logging.INFO)
    logger.propagate = False  # 重要：不传播到父logger

    # 如果已经有handler，直接返回
    if logger.handlers:
        return logger

    # 交易日志使用单独的文件
    trade_file = os.path.join(LoggerConfig.LOG_DIR, "trading.log")

    # 确保目录存在
    os.makedirs(LoggerConfig.LOG_DIR, exist_ok=True)

    # 文件处理器
    file_handler = logging.handlers.RotatingFileHandler(
        trade_file,
        maxBytes=LoggerConfig.MAX_BYTES,
        backupCount=LoggerConfig.BACKUP_COUNT,
        encoding='utf-8'
    )

    # 交易日志格式（CSV格式，便于分析）
    formatter = logging.Formatter(
        '%(asctime)s,%(message)s',
        datefmt=LoggerConfig.DATE_FORMAT
    )
    file_handler.setFormatter(formatter)

    # 添加handler
    logger.addHandler(file_handler)

    return logger


def get_optimize_logger():
    """获取优化策略专用的logger - 不继承根logger的设置"""
    # 创建独立的logger，不继承root logger
    logger = logging.getLogger("optimizing")
    logger.setLevel(logging.INFO)
    logger.propagate = False  # 重要：不传播到父logger

    # 如果已经有handler，直接返回
    if logger.handlers:
        return logger

    # 优化日志使用单独的文件
    optimize_file = os.path.join(LoggerConfig.LOG_DIR, "optimizing.log")

    # 确保目录存在
    os.makedirs(LoggerConfig.LOG_DIR, exist_ok=True)

    # 文件处理器
    file_handler = logging.handlers.RotatingFileHandler(
        optimize_file,
        maxBytes=LoggerConfig.MAX_BYTES,
        backupCount=LoggerConfig.BACKUP_COUNT,
        encoding='utf-8'
    )

    # 优化日志格式（CSV格式，便于分析）
    formatter = logging.Formatter(
        '%(asctime)s,%(message)s',
        datefmt=LoggerConfig.DATE_FORMAT
    )
    file_handler.setFormatter(formatter)

    # 添加handler
    logger.addHandler(file_handler)

    return logger

