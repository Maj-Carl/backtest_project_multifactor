# data 目录结构说明

当前按职责分层为 `fetch + storage + orchestration + features + universe`。

```text
data/
  README.md
  __pycache__/                    # Python 字节码缓存（自动生成，可删除）
  multi_cache/                    # 本地行情缓存根目录（运行时生成，可迁移到外部盘）
  fetch/                          # 抓取层
    __init__.py
    api_keys.py
    trade_calendar.py
    apis/
      __init__.py
      api_kline_dc.py
      api_kline_daily_th.py
      README.md
  storage/                        # 存储/归一化层
    __init__.py
    bar_store.py
    column_normalize.py
  orchestration/                  # 数据编排层（单标的/多标的）
    __init__.py
    single_symbol.py
    batch_symbols.py
  features/                       # 特征加工层
    __init__.py
    price_factors.py
  universe/                       # 股票池
    builder.py
    a_share_codes.csv
    manual_universe_template.csv
```

## 推荐导入入口

- 单标的行情：`data.orchestration.single_symbol.get_stock_data`
- 批量行情：`data.orchestration.batch_symbols.get_multiple_stock_data`
- 因子特征：`data.features.price_factors.add_factor_columns`
- 股票池：`data.universe.builder.build_universe_codes`
- 存储能力：`data.storage.bar_store`
