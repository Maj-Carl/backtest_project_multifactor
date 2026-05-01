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

- `run_multifactor.py`：主入口（支持全量与冒烟）
- `backtest_main.py`：回测主流程编排
- `config/`：全局配置
- `data/`：数据获取、行情仓与股票池构建
- `strategies/`：多因子策略实现
- `backtest/`：Backtrader 引擎封装
- `reports/`：报告生成与输出文件
- `utils/`：日志等通用工具
- `scripts/`：运维/数据工具脚本（已从根目录收敛）

## 常用命令

在项目根目录执行：

- 全量回测
  - `python run_multifactor.py`
- 快速冒烟（推荐日常开发）
  - `python run_multifactor.py --smoke`
- 超小冒烟（更快）
  - `python run_multifactor.py --smoke --smoke-topk 3 --smoke-days 20`

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

## scripts 工具脚本

- 预抓取股票池行情到本地仓
  - `python scripts/prefetch_universe_data.py`
- 查看本地仓 catalog 状态
  - `python scripts/inspect_store_status.py`
- 校验本地与远端数据一致性
  - `python scripts/verify_stock_data.py --code 600000 --start 2025-04-15 --end 2026-04-15`
- 生成全 A 股票清单
  - `python scripts/build_a_share_universe.py`
- 从全 A 清单生成手动股票池模板
  - `python scripts/build_manual_universe_from_all.py`
- 参数扫描
  - `python scripts/tune_multifactor.py`
- 接口B 单日全市快照预览（可看字段与样例）
  - `python data/fetch/apis/api_kline_daily_th.py --mode market --date 2025-10-24`
- 按 Config 区间用 daily_th 向 `MULTI_STOCK_CACHE_DIR` 补 Parquet（约每个交易日全市一次）
  - `python scripts/supplement_silver_daily_th.py`
  - 仅回测 universe、加快：`python scripts/supplement_silver_daily_th.py --universe-only`
  - 先试跑 3 个交易日：`python scripts/supplement_silver_daily_th.py --universe-only --max-days 3`

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
- 如果全量回测频繁超时，优先先做本地预抓取：`python scripts/prefetch_universe_data.py`

### 3) 控制台中文乱码

- Windows 终端编码导致，通常不影响实际逻辑和结果
- 可切换到 UTF-8 终端或在 IDE 内查看日志/报告文件

### 4) 冒烟还是慢

- 进一步压缩参数：`python run_multifactor.py --smoke --smoke-topk 3 --smoke-days 20`
- 若仍慢，可再降到 `--smoke-topk 1 --smoke-days 10` 做最小链路验证

### 5) 本地数据是否可信，怎么快速核验

- 使用：`python scripts/verify_stock_data.py --code 600000 --start 2026-03-01 --end 2026-03-31`
- 接口不可用时，在线抽样会被跳过并提示，不会无限阻塞
