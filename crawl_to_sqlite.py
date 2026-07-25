#!/usr/bin/env python3
"""
SQLite 数据层：threads 表（列表页条目）+ items 表（详情页番号条目）
+ actress_names 表（维基女优日/中文名对照）
+ actress_names_minnano 表（みんなのAV 汉字/假名/罗马字）。
对外 API：ensure_db、thread_exists、upsert_thread、update_thread_status、upsert_item（含 title / title_transfer）、
replace_actress_names_from_csv、replace_minnano_actress_from_csv
"""
from __future__ import annotations

import csv
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from local_assets import asset_path_on_disk, is_valid_image_file, is_valid_torrent_file

# 续跑/补缺默认只扫近 N 天（按 crawled_at）
RESUME_LOOKBACK_DAYS = 3


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def resume_since_iso(days: int = RESUME_LOOKBACK_DAYS) -> str:
    """UTC ISO 下界：只处理 crawled_at >= 该时刻的库记录。"""
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


_CREATE_THREADS = """
CREATE TABLE IF NOT EXISTS threads (
    url TEXT PRIMARY KEY,
    title TEXT,
    downloads INTEGER DEFAULT 0,
    source TEXT DEFAULT '',
    status TEXT DEFAULT 'pending',
    crawled_at TEXT NOT NULL
);
"""

_CREATE_ITEMS = """
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_url TEXT NOT NULL,
    source TEXT DEFAULT '',
    code TEXT NOT NULL,
    actress TEXT,
    size_gb TEXT,
    title TEXT,
    code_title TEXT,
    title_transfer TEXT,
    img_url TEXT,
    img_path TEXT,
    torrent_url TEXT,
    torrent_path TEXT,
    crawled_at TEXT NOT NULL,
    UNIQUE(thread_url, code)
);
"""

_ITEMS_COLS = (
    "id",
    "thread_url",
    "source",
    "code",
    "actress",
    "size_gb",
    "title",
    "code_title",
    "title_transfer",
    "img_url",
    "img_path",
    "torrent_url",
    "torrent_path",
    "crawled_at",
)

_CREATE_ACTRESS_NAMES = """
CREATE TABLE IF NOT EXISTS actress_names (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ja_name TEXT NOT NULL DEFAULT '',
    zh_name TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    UNIQUE(ja_name, zh_name)
);
"""

_INSERT_ACTRESS_NAME = """
INSERT INTO actress_names (ja_name, zh_name, updated_at)
VALUES (?, ?, ?)
ON CONFLICT(ja_name, zh_name) DO NOTHING
"""

_CREATE_ACTRESS_NAMES_MINNANO = """
CREATE TABLE IF NOT EXISTS actress_names_minnano (
    source_id TEXT PRIMARY KEY,
    ja_name TEXT NOT NULL,
    kana TEXT DEFAULT '',
    romaji TEXT DEFAULT '',
    works_count INTEGER DEFAULT 0,
    detail_url TEXT DEFAULT '',
    img_url TEXT DEFAULT '',
    debut_text TEXT DEFAULT '',
    updated_at TEXT NOT NULL
);
"""

_UPSERT_MINNANO = """
INSERT INTO actress_names_minnano (
    source_id, ja_name, kana, romaji, works_count,
    detail_url, img_url, debut_text, updated_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(source_id) DO UPDATE SET
    ja_name = excluded.ja_name,
    kana = excluded.kana,
    romaji = excluded.romaji,
    works_count = excluded.works_count,
    detail_url = excluded.detail_url,
    img_url = excluded.img_url,
    debut_text = excluded.debut_text,
    updated_at = excluded.updated_at
"""


def _items_column_names(conn: sqlite3.Connection) -> list[str]:
    return [r[1] for r in conn.execute("PRAGMA table_info(items)")]


def _migrate_items_column_order(conn: sqlite3.Connection) -> None:
    """SQLite 无法 ALTER 改列序；列序与 _ITEMS_COLS 不一致时重建 items。"""
    cols = _items_column_names(conn)
    if not cols or cols == list(_ITEMS_COLS):
        return

    col_set = set(cols)
    select_exprs = []
    for name in _ITEMS_COLS:
        if name in col_set:
            select_exprs.append(name)
        else:
            select_exprs.append("NULL" if name != "source" else "''")
    col_list = ", ".join(_ITEMS_COLS)
    select_list = ", ".join(select_exprs)

    conn.execute("DROP TABLE IF EXISTS items__reorder")
    conn.execute(_CREATE_ITEMS.replace("items", "items__reorder", 1))
    conn.execute(
        f"INSERT INTO items__reorder ({col_list}) SELECT {select_list} FROM items"
    )
    conn.execute("DROP TABLE items")
    conn.execute("ALTER TABLE items__reorder RENAME TO items")


def ensure_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute(_CREATE_THREADS)
    conn.execute(_CREATE_ITEMS)
    conn.execute(_CREATE_ACTRESS_NAMES)
    conn.execute(_CREATE_ACTRESS_NAMES_MINNANO)
    for tbl in ("threads", "items"):
        try:
            conn.execute(f"ALTER TABLE {tbl} ADD COLUMN source TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass
    try:
        conn.execute("ALTER TABLE items ADD COLUMN title_transfer TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE items ADD COLUMN title TEXT")
    except sqlite3.OperationalError:
        pass
    for col_sql in (
        "ALTER TABLE actress_names_minnano ADD COLUMN detail_url TEXT DEFAULT ''",
        "ALTER TABLE actress_names_minnano ADD COLUMN img_url TEXT DEFAULT ''",
        "ALTER TABLE actress_names_minnano ADD COLUMN debut_text TEXT DEFAULT ''",
    ):
        try:
            conn.execute(col_sql)
        except sqlite3.OperationalError:
            pass
    _migrate_items_column_order(conn)
    conn.commit()
    return conn


def replace_actress_names_from_csv(conn: sqlite3.Connection, csv_path: Path) -> int:
    """读取日文/中文两列 CSV，清空 actress_names 后全量写入，返回写入行数。"""
    path = Path(csv_path)
    rows: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    now = _now_iso()
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ja = (row.get("日文") or "").strip()
            zh = (row.get("中文") or "").strip()
            if not ja and not zh:
                continue
            key = (ja, zh)
            if key in seen:
                continue
            seen.add(key)
            rows.append((ja, zh, now))

    try:
        conn.execute("DELETE FROM actress_names")
        if rows:
            conn.executemany(_INSERT_ACTRESS_NAME, rows)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return len(rows)


def replace_minnano_actress_from_csv(conn: sqlite3.Connection, csv_path: Path) -> int:
    """读取 minnano CSV，清空 actress_names_minnano 后全量写入，返回写入行数。"""
    path = Path(csv_path)
    rows: list[tuple[str, str, str, str, int, str, str, str, str]] = []
    seen: set[str] = set()
    now = _now_iso()
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            sid = (row.get("source_id") or "").strip()
            ja = (row.get("ja_name") or "").strip()
            kana = (row.get("kana") or "").strip()
            romaji = (row.get("romaji") or "").strip()
            detail_url = (row.get("detail_url") or "").strip()
            img_url = (row.get("img_url") or "").strip()
            debut_text = (row.get("debut_text") or "").strip()
            if not sid or not ja:
                continue
            if sid in seen:
                continue
            seen.add(sid)
            try:
                works = int((row.get("works_count") or "0").strip() or "0")
            except ValueError:
                works = 0
            if not detail_url:
                detail_url = f"https://www.minnano-av.com/actress{sid}.html"
            rows.append(
                (sid, ja, kana, romaji, works, detail_url, img_url, debut_text, now)
            )

    try:
        conn.execute("DELETE FROM actress_names_minnano")
        if rows:
            conn.executemany(_UPSERT_MINNANO, rows)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return len(rows)


def thread_exists(conn: sqlite3.Connection, url: str) -> bool:
    """查询详情页 URL 是否已在 threads 表中。"""
    row = conn.execute("SELECT 1 FROM threads WHERE status='done' AND url = ?", (url,)).fetchone()
    return row is not None


def upsert_thread(
    conn: sqlite3.Connection,
    url: str,
    title: str,
    downloads: int = 0,
    source: str = "",
    status: str = "pending",
) -> None:
    conn.execute(
        """
        INSERT INTO threads (url, title, downloads, source, status, crawled_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(url) DO UPDATE SET
            title = COALESCE(excluded.title, threads.title),
            downloads = excluded.downloads,
            source = excluded.source,
            status = excluded.status,
            crawled_at = excluded.crawled_at;
        """,
        (url, title, downloads, source, status, _now_iso()),
    )
    conn.commit()


def update_thread_status(conn: sqlite3.Connection, url: str, status: str) -> None:
    conn.execute(
        "UPDATE threads SET status = ?, crawled_at = ? WHERE url = ?",
        (status, _now_iso(), url),
    )
    conn.commit()


def upsert_item(
    conn: sqlite3.Connection,
    thread_url: str,
    code: str,
    title: Optional[str] = None,
    code_title: Optional[str] = None,
    title_transfer: Optional[str] = None,
    actress: Optional[str] = None,
    size_gb: Optional[str] = None,
    img_url: Optional[str] = None,
    img_path: Optional[str] = None,
    torrent_url: Optional[str] = None,
    torrent_path: Optional[str] = None,
    source: str = "",
) -> None:
    conn.execute(
        """
        INSERT INTO items (thread_url, source, code, actress, size_gb, title, code_title,
                           title_transfer, img_url, img_path, torrent_url, torrent_path,
                           crawled_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(thread_url, code) DO UPDATE SET
            source = excluded.source,
            actress = COALESCE(excluded.actress, items.actress),
            size_gb = COALESCE(excluded.size_gb, items.size_gb),
            title = COALESCE(excluded.title, items.title),
            code_title = COALESCE(excluded.code_title, items.code_title),
            title_transfer = COALESCE(excluded.title_transfer, items.title_transfer),
            img_url = COALESCE(excluded.img_url, items.img_url),
            img_path = COALESCE(excluded.img_path, items.img_path),
            torrent_url = COALESCE(excluded.torrent_url, items.torrent_url),
            torrent_path = COALESCE(excluded.torrent_path, items.torrent_path),
            crawled_at = excluded.crawled_at;
        """,
        (thread_url, source, code, actress, size_gb, title, code_title, title_transfer,
         img_url, img_path, torrent_url, torrent_path, _now_iso()),
    )
    conn.commit()


def list_pending_threads(
    conn: sqlite3.Connection,
    *,
    since: Optional[str] = None,
    source: Optional[str] = None,
) -> list[dict]:
    conn.row_factory = sqlite3.Row
    clauses = ["status = 'pending'"]
    params: list = []
    if source is not None:
        clauses.append("source = ?")
        params.append(source)
    if since:
        clauses.append("crawled_at >= ?")
        params.append(since)
    sql = (
        "SELECT url, title, downloads, source, status FROM threads WHERE "
        + " AND ".join(clauses)
    )
    rows = conn.execute(sql, params).fetchall()
    result = [dict(r) for r in rows]
    conn.row_factory = None
    return result


def list_items_for_thread(conn: sqlite3.Connection, thread_url: str) -> list[dict]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT id, thread_url, source, code, actress, size_gb, title, code_title,
               title_transfer, img_url, img_path, torrent_url, torrent_path
        FROM items WHERE thread_url = ?
        """,
        (thread_url,),
    ).fetchall()
    result = [dict(r) for r in rows]
    conn.row_factory = None
    return result


def list_candidate_missing_asset_rows(
    conn: sqlite3.Connection,
    *,
    since: Optional[str] = None,
    source: Optional[str] = None,
) -> list[dict]:
    """SQL candidates: 缺种子 URL，或有 URL 但 path 空。文件存在性在 Python 侧再验。"""
    conn.row_factory = sqlite3.Row
    sql = """
        SELECT thread_url, code, img_url, img_path, torrent_url, torrent_path
        FROM items
        WHERE
          (
            (torrent_url IS NULL OR torrent_url = '')
            OR
            (img_url IS NOT NULL AND img_url != '' AND (img_path IS NULL OR img_path = ''))
            OR
            (torrent_url IS NOT NULL AND torrent_url != ''
             AND (torrent_path IS NULL OR torrent_path = ''))
          )
    """
    params: list = []
    if source is not None:
        sql += " AND source = ?"
        params.append(source)
    if since:
        sql += " AND crawled_at >= ?"
        params.append(since)
    rows = conn.execute(sql, params).fetchall()
    result = [dict(r) for r in rows]
    conn.row_factory = None
    return result


def item_needs_asset_retry(item: dict) -> bool:
    """下载完成判定：必须有种子 URL，且已有 URL 的资源文件通过内容校验。

    种子 URL 为空时也算未完成——楼主晚编辑补链时，若标成 done 就永远不会重解析。
    """
    img_url = (item.get("img_url") or "").strip()
    tor_url = (item.get("torrent_url") or "").strip()
    if not tor_url:
        return True
    if img_url and not is_valid_image_file(item.get("img_path") or ""):
        return True
    if not is_valid_torrent_file(item.get("torrent_path") or ""):
        return True
    return False


def item_missing_for_queue(item: dict) -> bool:
    """建队列用：缺种子 URL，或有 URL 但 path 空/文件不存在（不读 magic）。"""
    img_url = (item.get("img_url") or "").strip()
    tor_url = (item.get("torrent_url") or "").strip()
    if not tor_url:
        return True
    if img_url and not asset_path_on_disk(item.get("img_path")):
        return True
    if not asset_path_on_disk(item.get("torrent_path")):
        return True
    return False


def item_needs_detail_reparse(item: dict) -> bool:
    """缺种子 URL 时必须 full 重拉详情，patch 无法补链。"""
    return not (item.get("torrent_url") or "").strip()


def thread_assets_complete(conn: sqlite3.Connection, thread_url: str) -> bool:
    items = list_items_for_thread(conn, thread_url)
    if not items:
        return False
    return not any(item_needs_asset_retry(it) for it in items)


def list_thread_urls_with_missing_assets(
    conn: sqlite3.Connection,
    *,
    since: Optional[str] = None,
    source: Optional[str] = None,
) -> list[str]:
    urls: set[str] = set()
    for row in list_candidate_missing_asset_rows(conn, since=since, source=source):
        urls.add(row["thread_url"])
    conn.row_factory = sqlite3.Row
    sql = """
        SELECT thread_url, img_url, img_path, torrent_url, torrent_path FROM items
        WHERE (
          (img_url IS NOT NULL AND img_url != '' AND img_path IS NOT NULL AND img_path != '')
          OR (torrent_url IS NOT NULL AND torrent_url != ''
              AND torrent_path IS NOT NULL AND torrent_path != '')
        )
    """
    params: list = []
    if source is not None:
        sql += " AND source = ?"
        params.append(source)
    if since:
        sql += " AND crawled_at >= ?"
        params.append(since)
    rows = conn.execute(sql, params).fetchall()
    for r in rows:
        if item_missing_for_queue(dict(r)):
            urls.add(r["thread_url"])
    conn.row_factory = None
    return sorted(urls)
