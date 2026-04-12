# Crawler Expert Reference

## SQLite 操作清单

### 建表与索引建议

```sql
CREATE TABLE IF NOT EXISTS products (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source TEXT NOT NULL,
  product_id TEXT NOT NULL,
  title TEXT,
  price REAL,
  currency TEXT,
  url TEXT,
  crawled_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(source, product_id)
);

CREATE INDEX IF NOT EXISTS idx_products_crawled_at ON products(crawled_at);
CREATE INDEX IF NOT EXISTS idx_products_price ON products(price);
```

### 常用查询模板

```sql
-- 最近 24 小时采集量
SELECT COUNT(*) AS cnt
FROM products
WHERE crawled_at >= datetime('now', '-1 day');

-- 重复检查（理论应为 0）
SELECT source, product_id, COUNT(*) AS c
FROM products
GROUP BY source, product_id
HAVING c > 1;

-- 分页读取
SELECT source, product_id, title, price, crawled_at
FROM products
ORDER BY crawled_at DESC
LIMIT 100 OFFSET 0;
```

### UPSERT 规则

- 业务唯一键优先：`(source, product_id)`。
- `updated_at` 始终在冲突更新时刷新。
- 对价格等易变化字段执行覆盖更新，不保留旧值。

## Scrapy 排障清单

### 抓不到数据

1. 先看响应状态码与响应体是否为空。
2. 检查页面是否为 JS 渲染占位。
3. 在 `scrapy shell <url>` 中验证选择器。

### 频繁 429/503

1. 降低并发（`CONCURRENT_REQUESTS`）。
2. 提高延迟并启用 `AUTOTHROTTLE_ENABLED`。
3. 对特定状态码使用指数退避重试。

### 字段空值率高

1. 为关键字段设置多选择器兜底。
2. 增加文本清洗（`strip`、去货币符号、去千分位）。
3. 输出解析失败日志并附 URL。

## 数据质量验收模板

- 记录数：是否达到预期区间。
- 去重率：`1 - unique_count / total_count` 是否在可接受范围。
- 空值率：核心字段（如 `product_id`、`title`、`price`）是否低于阈值。
- 错误率：请求失败+解析失败+入库失败总占比。

## 推荐输出格式

```markdown
## 抓取策略
- 站点与入口：
- 分页方式：
- 字段映射：
- 去重键：

## 实现片段
- Spider:
- Pipeline:
- SQLite SQL:

## 验证结果
- 记录数：
- 重复率：
- 空值率：
- 错误率：
```
