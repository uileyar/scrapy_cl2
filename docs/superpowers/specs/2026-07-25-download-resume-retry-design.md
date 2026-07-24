# 下载中断续传与失败重试设计

日期：2026-07-25  
状态：已定稿  
范围：主编排 `crawl_html.py` + SQLite 查询 API（`crawl_to_sqlite.py`）

## 背景与问题

当前流水线把「帖子处理过」和「资源下载成功」混为一谈，导致：

1. **中断无法续跑**：任务只从列表前 N 页收集；DB 中 `pending` 且已滚出列表的帖不会再被处理。
2. **失败无法重下**：图片失败仍入库后帖被标 `done`；种子失败 `continue` 跳过入库后帖仍标 `done`；`thread_exists` 只认 `done`，下次永久跳过。
3. **重复下载风险**：重跑时未先检查本地是否已有文件。

## 目标

- 中断后再次启动能继续未完成帖。
- `done` 但缺图/缺种（有 URL、无有效本地文件）的已入库条目可被补下。
- 下载前判断本地已存在则跳过网络请求。
- 本来就没有图/种、以及被体积过滤的条目，不进入补下、不反复下载。

## 非目标

- 不改 `image_download.py` / `rmdown_download.py` 的核心下载协议。
- 不给 items 增加 `img_status` / `torrent_status` / `skip_reason` 字段。
- 不改体积过滤阈值（仍为 `size_gb <= 1.5`）。
- 不做真正的 HTTP Range 断点续传（文件级仍是整文件重下或跳过）。

## 决策摘要

| 项 | 选择 |
|---|---|
| 补下范围 | B：列表新帖 + DB `pending` + 已入库缺资源 item |
| 实现路线 | 编排层合并队列；不迁库 |
| 过滤项 | C：过滤项不入库；补下只扫已入库 item |
| 本地预检 | 下载前必做；库 path 优先，其次目标路径 |

## 架构

```
启动
  ├─ 列表页收集非 done 帖          → 全量处理
  ├─ DB 查询 status=pending        → 全量处理
  └─ DB 查询缺资源 item 的 thread  → 补缺处理
         │
         ▼
   按 thread_url 去重合并队列
         │
    ┌────┴────┐
    全量模式   补缺模式
    (新/pending) (仅缺 path 的已入库 item)
         │
         ▼
   每资源：本地预检 → 必要时下载 → upsert_item
         │
         ▼
   该 thread 所有已入库 item 资源齐？
     是 → status=done
     否 → status=pending
```

## 任务收集

### 全量候选

1. 列表分页：`status != 'done'`（沿用现有 `thread_exists`：仅 `done` 视为已存在）的帖进入队列，模式=`full`。
2. DB：`SELECT url, title, ... FROM threads WHERE status='pending'`，合并进队列，模式=`full`（若已在列表中则以列表行为准）。

### 补缺候选

查询已入库 items，满足任一：

- `img_url` 非空，且（`img_path` 空 **或** 文件不存在）
- `torrent_url` 非空，且（`torrent_path` 空 **或** 文件不存在）

取其 `thread_url` 去重后并入队列。若该 thread 尚未在队列中，模式=`patch`；若已是 `full`，保持 `full`。

**被体积过滤且从未入库的条目不会出现在此查询中，因此不会被补下、不会多次下载。**

## 处理模式

### 全量（`full`）

1. 拉详情并解析。
2. 对每个番号：`size_gb <= 1.5` → 跳过（不入库、不下载）。
3. 其余条目：本地预检 → 下载缺失资源 → `upsert_item`。
4. 按「已入库资源是否齐」更新 thread status。

### 补缺（`patch`）

1. **不**因补缺而重跑体积过滤逻辑去「发现」新条目。
2. 只加载该 thread 下已入库、且 path 无效的 item。
3. 对每个待补资源：本地预检 → 下载 → 更新对应 path。
4. 按同一套 done 判定更新 thread status。

说明：`full` 重跑 pending 时仍会再解析详情并过滤——仅多解析请求，过滤项仍不下。这是 C 方案的明确代价。

## 下载前本地预检

图片与种子在发起网络请求前均执行：

1. **库 path 有效**：字段非空且 `Path(path).is_file()` → 跳过下载，沿用该 path（跨日期目录有效）。
2. **否则看目标路径**：本次将写入的 `save_dir / 由 code_title 得到的文件名`（含扩展名推断规则与现网一致）若已存在且通过校验 → 跳过下载，将该路径写回库。
3. **否则**才下载。

### 文件合法性

- **图片**：非空，且能通过现有 magic 识别为图片；损坏/半截视为无效，允许重下。
- **种子**：非空；优先校验以合理 torrent 特征为准（至少非空且扩展名为 `.torrent`；实现计划可再收紧）。损坏视为无效，允许重下。

### 损坏文件处理

目标路径上存在但校验失败时：删除或覆盖该损坏文件后重下（优先覆盖，避免堆积 `_1` `_2`）。

## upsert 与失败行为

- 图片失败：记录错误日志，`img_path` 可为空，**仍入库**，不中断同帖其它条目。
- 种子失败：**取消**当前的 `continue` 跳过入库；照样 `upsert_item`（`torrent_path` 可空）。
- `upsert_item` 对 path 的 `COALESCE` 行为保留：新值为 `NULL` 时不覆盖已有非空 path（补缺成功写入非空 path 时可更新）。

注意：补缺成功时必须传入新的非空 path；失败时不要用 `NULL` 把旧的有效 path 清掉（当前 COALESCE 已保证这点）。

## done 判定

对某个 `thread_url`，检查其**所有已入库 items**：

- 若 `img_url` 非空 → 必须有有效本地 `img_path` 文件。
- 若 `torrent_url` 非空 → 必须有有效本地 `torrent_path` 文件。
- 无对应 URL → 不要求本地文件。

全部满足 → `update_thread_status(..., "done")`；否则 → `"pending"`。

本来就没有图、没有种子的条目不影响 done。

## API 变更（`crawl_to_sqlite.py`）

新增只读查询（名称可微调，语义固定）：

- `list_pending_threads(conn) -> list[dict]`
- `list_thread_urls_with_missing_assets(conn) -> list[str]`  
  （缺资源定义与上文一致；文件存在性可在 Python 侧二次过滤，因 SQLite 不知磁盘状态）
- `list_items_for_thread(conn, thread_url) -> list[dict]`（补缺用）
- `thread_assets_complete(conn, thread_url) -> bool`（done 判定；文件存在性在 Python 侧）

不强制改表结构。

## 错误处理

- 详情页请求失败：该 thread 保持/设为 `pending`，继续下一条。
- 单资源下载失败：入库（path 空），thread 最终为 `pending`。
- 用户 `KeyboardInterrupt`：已 commit 的状态保留；未标 done 的保持 `pending`，下次可续。

## 测试要点

1. pending 帖不在列表前 N 页时，启动仍会被处理。
2. 仅图片失败 → thread=`pending`；再次启动只补图（若种子已齐）。
3. 种子失败后 item 仍在库；再次启动可补种。
4. 库 path 指向存在文件 → 不发起下载。
5. 无 `img_url` 的条目不阻塞 done。
6. `size_gb <= 1.5` 的条目不入库；补缺查询不会点名它们，不会下载。
7. 损坏本地文件会被视为无效并重下。

## 风险与边界

- `full` 模式的 `save_dir` 仍按「当天日期」建目录；跨日续跑时以**库中绝对 path** 为准跳过下载，避免重复；新补文件可能落在新日期目录（可接受）。
- 列表标题变化会导致多番号子目录名变化；预检第 2 步可能找不到旧文件，但第 1 步（库 path）仍可命中。
- 体积过滤条件若将来变更，历史「本应下载却被滤掉」的条目不会自动出现；需另做回填（本设计不包含）。
