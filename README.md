# backtest_project_429

仅保留多因子策略的 A 股回测工程，使用本地 Parquet 行情仓 + DuckDB catalog。

## 首次上手（3 步）

1) 安装依赖

- `python -m pip install -r requirements.txt`

2) 配置 API Key

- 在本机创建文件：`C:\投资\STOCK_API_KE.txt`
- 文件内容仅放你的 key（纯文本一行）

3) 先跑快速冒烟

- `python run_multifactor.py --smoke`
- 如需更快：`python run_multifactor.py --smoke --smoke-topk 3 --smoke-days 20`

## 目录结构

根目录建议仅保留入口与模块目录：

- `run_multifactor.py`：主入口（全量 / 冒烟 / `--manual-csv` 自定义股票池）
- `backtest_main.py`：回测主流程编排
- `config/`：全局配置
- `data/`：数据获取、行情仓与股票池构建
- `strategies/`：多因子策略实现
- `backtest/`：Backtrader 引擎封装
- `reports/`：报告生成与输出文件
- `utils/`：日志等通用工具
- `scripts/`：仅保留 `cleanup_project.py`（清理本仓库运行产物）

## 常用命令

在项目根目录执行：

- 全量回测
  - `python run_multifactor.py`
- 快速冒烟（推荐日常开发）
  - `python run_multifactor.py --smoke`
- 超小冒烟（更快）
  - `python run_multifactor.py --smoke --smoke-topk 3 --smoke-days 20`
- 调试模式（分类 DEBUG 日志写入 `logs/debug/*.log`）
  - `python run_multifactor.py --debug`（可与 `--smoke` 等组合）
- 主流程终端输出与 `logs/backtest.log` 一致（同一 logger：股票池提示、行情装载、在线抽样、接口重试、回测摘要等）；交易明细仍在 `logs/trading.log`，性能节点在 `logs/performance.log`。

## 数据接口调试（data/fetch/apis）

- 两个接口脚本都支持“文件头默认参数直接运行”（推荐先改默认值再跑）
  - `python data/fetch/apis/api_kline_dc.py`
  - `python data/fetch/apis/api_kline_daily_th.py`
- 接口A（`api_stock_kline_dc`，按代码+区间）
  - `python data/fetch/apis/api_kline_dc.py --codes 000001 --period 1d --start 2025-04-15 --end 2025-04-30 --adjust 0 --ty 个股`
- 接口B（`api_stock_kline_daily_th`，单日全市场）
  - `python data/fetch/apis/api_kline_daily_th.py --mode market --date 2025-10-24`
- 接口B（按交易日拼单只股票区间）
  - `python data/fetch/apis/api_kline_daily_th.py --mode code_range --code 000001 --start 2025-04-15 --end 2025-04-30 --cache-dir "C:\投资\STOCK_DATA"`

## 股票池与数据辅助（`data/universe/`、`data/fetch/apis`）

持久化股票池文件为 **`data/universe/a_share_codes.csv`**（自动抓取或手动 CSV 规范化后写入）。

- 联网刷新清单并写缓存：`python run_multifactor.py --refresh-universe`（或 `python data/universe/build_a_share_universe.py`）
- 本次回测使用自定义 CSV（写入 `a_share_codes.csv` 后再按 `Config` 过滤）：`python run_multifactor.py --manual-csv path/to/codes.csv`
- 从全 A 清单筛出示例 CSV：`python data/universe/build_manual_universe_from_all.py`（默认输出 `manual_universe_example.csv`；若要直接覆盖股票池文件可用 `--target data/universe/a_share_codes.csv`）
- 接口调试（含接口 B 单日全市预览）：见上文「数据接口调试」

说明：原 `scripts/` 下预抓取、catalog 查看、手工对账、`daily_th` 补仓、参数扫描等 CLI 已从本仓库移除；需要时可从 Git 历史恢复。

## 清理运行产物与缓存

在项目根目录执行。**默认只动本仓库内文件**，不会删除 `Config.MULTI_STOCK_CACHE_DIR` 下的 parquet / DuckDB。

- 常规清理：`python scripts/cleanup_project.py`
- 先看会删什么：`python scripts/cleanup_project.py --dry-run`
- 顺带清空仓库内遗留的 `data/multi_cache/`：`python scripts/cleanup_project.py --include-local-multi-cache`
- 只清理外盘缓存目录里的边角（失败代码列表、`locks/*.lock`），不误删 silver：`python scripts/cleanup_project.py --cache-dir-delete-junk "C:\投资\STOCK_DATA"`

覆盖范围：`reports/*.html`、`logs/` 下的 `.log`、`**/__pycache__`、常见测试缓存目录（`.pytest_cache`、`htmlcov`、`.coverage`、`.mypy_cache`、`.ruff_cache`）。

## 说明

- 冒烟模式会自动缩小股票池和日期窗，但保留在线抽样与报告生成，用于小规模全能力验证。
- 数据主仓目录由 `Config.MULTI_STOCK_CACHE_DIR` 控制（当前为 `C:\投资\STOCK_DATA`）。

## 常见问题（FAQ）

### 1) 接口返回 status=200 但 rows=0

常见原因是参数格式不符合接口要求：

- 多标的 `codes` 用 `|` 分隔（不要用逗号）
- 指数代码使用带前缀格式（如 `1.000300`）
- 时间区间过短或非交易日也可能导致空数据

### 2) 报网络超时（ReadTimeout）

- 这通常是接口波动，不一定是代码错误
- 先用冒烟命令验证主链路：`python run_multifactor.py --smoke`
- 如果全量回测频繁超时：缩小股票池与日期区间、或使用冒烟参数；必要时自备本地 Parquet/DuckDB 数据（或通过 `data/` 模块自写补仓逻辑）

### 3) 控制台中文乱码

- Windows 终端编码导致，通常不影响实际逻辑和结果
- 可切换到 UTF-8 终端或在 IDE 内查看日志/报告文件

### 4) 冒烟还是慢

- 进一步压缩参数：`python run_multifactor.py --smoke --smoke-topk 3 --smoke-days 20`
- 若仍慢，可再降到 `--smoke-topk 1 --smoke-days 10` 做最小链路验证

### 5) 本地数据是否可信，怎么快速核验

- 回测链路在装载结束后会按需做「按日全日快照 × 抽样日 × 标的池」在线抽样（见 `Config.DATA_SAMPLING_CHECK_*`，接口不可用时整段跳过并打日志）。
- 更细的手工区间对账可自行调用 `data.storage.bar_store.compare_local_vs_remote`，或从 Git 历史恢复已删除的运维脚本。
