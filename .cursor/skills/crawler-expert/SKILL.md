---
name: crawler-expert
description: 为通用网页与API抓取提供 Python/Scrapy 开发与 SQLite 入库规范。用于用户提到爬虫、抓取、scrapy、解析、反爬、去重、sqlite、入库、数据清洗、pipeline 时自动应用。
---

# Crawler Expert

## 适用范围

- 目标：稳定抓取结构化数据并可靠写入 SQLite。
- 默认技术路线：Python 3 + Scrapy + SQLite。
- 非目标：绕过高风险安全机制、违反站点条款、规避法律限制。

## 快速决策

1. **仅 JSON/API**：优先直接请求 API。
2. **静态 HTML**：使用 Scrapy 解析（`css`/`xpath`）。
3. **强 JS 渲染**：先确认是否有 API；无 API 再建议浏览器渲染方案。
4. **高频更新数据**：使用 UPSERT + 唯一键，避免重复写入。

## 交付工作流

按以下顺序执行并在输出中体现：

1. 明确抓取边界：站点范围、字段、频率、分页方式。
2. 设计数据模型：字段类型、唯一键、更新时间字段。
3. 编写 Spider：请求参数、分页、解析与清洗。
4. 配置可靠性：超时、重试、限流、退避。
5. 入库与幂等：建表、索引、UPSERT、事务回滚。
6. 验证质量：抽样校验、重复率、空值率、失败日志。

## 运行与可靠性默认值

- `DOWNLOAD_TIMEOUT=20`
- `RETRY_ENABLED=True`
- `RETRY_TIMES=3`
- `AUTOTHROTTLE_ENABLED=True`
- `ROBOTSTXT_OBEY=True`（除非用户明确要求并承担合规责任）
- 遇到 `429/503`：指数退避重试（带随机抖动）

## 代码模板

### 1) Item 模板（字段约束）

```python
import scrapy


class ProductItem(scrapy.Item):
    source = scrapy.Field()          # 数据来源域名或站点标识
    product_id = scrapy.Field()      # 业务唯一标识
    title = scrapy.Field()
    price = scrapy.Field()           # 统一为 float 或 decimal 字符串
    currency = scrapy.Field()
    url = scrapy.Field()
    crawled_at = scrapy.Field()      # ISO8601 时间戳
```

### 2) Spider 模板（分页 + 解析 + 清洗）

```python
import scrapy
from datetime import datetime, timezone


class ProductSpider(scrapy.Spider):
    name = "products"
    allowed_domains = ["example.com"]
    start_urls = ["https://example.com/products?page=1"]

    custom_settings = {
        "DOWNLOAD_TIMEOUT": 20,
        "RETRY_ENABLED": True,
        "RETRY_TIMES": 3,
        "AUTOTHROTTLE_ENABLED": True,
    }

    def parse(self, response):
        for card in response.css("div.product-card"):
            product_id = card.attrib.get("data-id")
            title = (card.css("h2::text").get() or "").strip()
            price_text = (card.css("span.price::text").get() or "").replace(",", "")

            yield {
                "source": "example.com",
                "product_id": product_id,
                "title": title,
                "price": self._safe_float(price_text),
                "currency": "USD",
                "url": response.urljoin(card.css("a::attr(href)").get() or ""),
                "crawled_at": datetime.now(timezone.utc).isoformat(),
            }

        next_href = response.css("a.next::attr(href)").get()
        if next_href:
            yield response.follow(next_href, callback=self.parse)

    @staticmethod
    def _safe_float(text: str):
        try:
            return float(text.strip().replace("$", ""))
        except Exception:
            return None
```

### 3) SQLite Pipeline 模板（建表 + UPSERT + 事务）

```python
import sqlite3
from contextlib import closing


CREATE_TABLE_SQL = """
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
"""

UPSERT_SQL = """
INSERT INTO products (
    source, product_id, title, price, currency, url, crawled_at, updated_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(source, product_id) DO UPDATE SET
    title=excluded.title,
    price=excluded.price,
    currency=excluded.currency,
    url=excluded.url,
    crawled_at=excluded.crawled_at,
    updated_at=excluded.updated_at;
"""


class SQLitePipeline:
    def open_spider(self, spider):
        db_path = spider.settings.get("SQLITE_PATH", "data.db")
        self.conn = sqlite3.connect(db_path)
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self.conn.execute("PRAGMA synchronous=NORMAL;")
        with closing(self.conn.cursor()) as cur:
            cur.execute(CREATE_TABLE_SQL)
        self.conn.commit()

    def process_item(self, item, spider):
        payload = (
            item.get("source"),
            item.get("product_id"),
            item.get("title"),
            item.get("price"),
            item.get("currency"),
            item.get("url"),
            item.get("crawled_at"),
            item.get("crawled_at"),
        )
        try:
            self.conn.execute(UPSERT_SQL, payload)
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return item

    def close_spider(self, spider):
        if getattr(self, "conn", None):
            self.conn.close()
```

### 4) settings.py 关键配置模板

```python
ITEM_PIPELINES = {
    "myproject.pipelines.SQLitePipeline": 300,
}

ROBOTSTXT_OBEY = True
DOWNLOAD_TIMEOUT = 20
RETRY_ENABLED = True
RETRY_TIMES = 3
AUTOTHROTTLE_ENABLED = True
AUTOTHROTTLE_START_DELAY = 1.0
AUTOTHROTTLE_MAX_DELAY = 10.0
RANDOMIZE_DOWNLOAD_DELAY = True
```

## 输出要求

- 先给“抓取策略”，再给“可运行最小模板”。
- 标注唯一键与去重策略（例如 `UNIQUE(source, product_id)`）。
- 标注异常处理位置（请求失败、解析失败、入库失败）。
- 给出最小验证清单：记录数、重复率、空值率、失败率。

## 常见问题处理

- **分页错漏**：检查“下一页选择器”与相对链接拼接。
- **字段波动**：对选择器做多路径兜底，字段统一清洗。
- **重复数据**：确认业务唯一键是否稳定、是否包含来源维度。
- **被限流**：降低并发、开启 AutoThrottle、增加退避与抖动。

## 附加资源

- 详细 SQLite 与排障清单见 [reference.md](reference.md)。
