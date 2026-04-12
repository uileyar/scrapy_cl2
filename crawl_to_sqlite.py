#!/usr/bin/env python3
"""
SQLite 数据层：threads 表（列表页条目）+ items 表（详情页番号条目）。
对外 API：ensure_db、thread_exists、upsert_thread、update_thread_status、upsert_item
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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
    code TEXT NOT NULL,
    code_title TEXT,
    actress TEXT,
    size_gb TEXT,
    img_url TEXT,
    img_path TEXT,
    torrent_url TEXT,
    torrent_path TEXT,
    source TEXT DEFAULT '',
    crawled_at TEXT NOT NULL,
    UNIQUE(thread_url, code)
);
"""


def ensure_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute(_CREATE_THREADS)
    conn.execute(_CREATE_ITEMS)
    for tbl in ("threads", "items"):
        try:
            conn.execute(f"ALTER TABLE {tbl} ADD COLUMN source TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass
    conn.commit()
    return conn


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
    code_title: Optional[str] = None,
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
        INSERT INTO items (thread_url, code, code_title, actress, size_gb,
                           img_url, img_path, torrent_url, torrent_path,
                           source, crawled_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(thread_url, code) DO UPDATE SET
            code_title = COALESCE(excluded.code_title, items.code_title),
            actress = COALESCE(excluded.actress, items.actress),
            size_gb = COALESCE(excluded.size_gb, items.size_gb),
            img_url = COALESCE(excluded.img_url, items.img_url),
            img_path = COALESCE(excluded.img_path, items.img_path),
            torrent_url = COALESCE(excluded.torrent_url, items.torrent_url),
            torrent_path = COALESCE(excluded.torrent_path, items.torrent_path),
            source = excluded.source,
            crawled_at = excluded.crawled_at;
        """,
        (thread_url, code, code_title, actress, size_gb,
         img_url, img_path, torrent_url, torrent_path, source, _now_iso()),
    )
    conn.commit()
