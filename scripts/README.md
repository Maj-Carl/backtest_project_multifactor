# `scripts/` 运维

## `cleanup_project.py`

清理「本仓库内」回测 HTML 报告、`logs/` 轮转日志、`**/__pycache__`、以及常见 IDE/测试缓存目录。

- **默认不动** `Config.MULTI_STOCK_CACHE_DIR`（例如 `C:\投资\STOCK_DATA`），避免误删本地行情 Parquet/catalog。
- 若需顺带删外盘里由本工程写入的边角文件（如 `smart_merge_failed_codes.txt`、遗留 `locks/*.lock`），请显式传 `--cache-dir-delete-junk`。

用法见根目录 `README.md`「清理运行产物与缓存」。

---

以下内容曾有一批 CLI，已全部删除；需要时从 Git 历史恢复：`git checkout <commit> -- scripts/`
