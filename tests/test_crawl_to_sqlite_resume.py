from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from crawl_to_sqlite import (
    ensure_db,
    is_magnet_url,
    item_missing_for_queue,
    item_needs_asset_retry,
    list_candidate_missing_asset_rows,
    list_items_for_thread,
    list_pending_threads,
    list_thread_urls_with_missing_assets,
    thread_assets_complete,
    upsert_item,
    upsert_thread,
)


class TestCrawlToSqliteResume(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "t.db"
        self.conn = ensure_db(self.db)
        self.files = Path(self.tmp.name) / "files"
        self.files.mkdir()

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_list_pending_threads(self) -> None:
        upsert_thread(self.conn, "http://a", "A", status="pending")
        upsert_thread(self.conn, "http://b", "B", status="done")
        rows = list_pending_threads(self.conn)
        self.assertEqual([r["url"] for r in rows], ["http://a"])

    def test_missing_when_path_null(self) -> None:
        upsert_thread(self.conn, "http://t", "T", status="done")
        upsert_item(
            self.conn, "http://t", "CODE-1",
            img_url="http://img/1.jpg", img_path=None,
            torrent_url="http://rm/1", torrent_path=None,
        )
        cands = list_candidate_missing_asset_rows(self.conn)
        self.assertTrue(any(r["thread_url"] == "http://t" for r in cands))
        items = list_items_for_thread(self.conn, "http://t")
        self.assertTrue(item_needs_asset_retry(items[0]))
        self.assertFalse(thread_assets_complete(self.conn, "http://t"))

    def test_complete_when_files_exist_and_no_url_ok(self) -> None:
        img = self.files / "x.jpg"
        img.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 20)
        tor = self.files / "x.torrent"
        tor.write_bytes(b"d4:infod4:name4:testee")
        upsert_thread(self.conn, "http://t", "T", status="pending")
        upsert_item(
            self.conn, "http://t", "CODE-1",
            img_url="http://img/1.jpg", img_path=str(img),
            torrent_url="http://rm/1", torrent_path=str(tor),
        )
        upsert_item(
            self.conn, "http://t", "CODE-2",
            img_url=None, img_path=None,
            torrent_url="http://rm/2", torrent_path=str(tor),
        )
        self.assertTrue(thread_assets_complete(self.conn, "http://t"))
        self.assertFalse(item_needs_asset_retry(list_items_for_thread(self.conn, "http://t")[0]))

    def test_path_set_but_file_missing_needs_retry(self) -> None:
        upsert_item(
            self.conn, "http://t", "CODE-1",
            img_url="http://img/1.jpg",
            img_path=str(self.files / "gone.jpg"),
            torrent_url=None, torrent_path=None,
        )
        item = list_items_for_thread(self.conn, "http://t")[0]
        self.assertTrue(item_needs_asset_retry(item))
        self.assertTrue(item_missing_for_queue(item))

    def test_pending_and_missing_respect_since(self) -> None:
        upsert_thread(self.conn, "http://new", "New", status="pending")
        upsert_thread(self.conn, "http://old", "Old", status="pending")
        old_ts = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        self.conn.execute(
            "UPDATE threads SET crawled_at = ? WHERE url = ?",
            (old_ts, "http://old"),
        )
        upsert_item(
            self.conn, "http://miss-new", "C1",
            img_url="http://img/1.jpg", img_path=None,
        )
        upsert_item(
            self.conn, "http://miss-old", "C2",
            img_url="http://img/2.jpg", img_path=None,
        )
        self.conn.execute(
            "UPDATE items SET crawled_at = ? WHERE thread_url = ?",
            (old_ts, "http://miss-old"),
        )
        self.conn.commit()
        since = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        pending = [r["url"] for r in list_pending_threads(self.conn, since=since)]
        self.assertIn("http://new", pending)
        self.assertNotIn("http://old", pending)
        missing = list_thread_urls_with_missing_assets(self.conn, since=since)
        self.assertIn("http://miss-new", missing)
        self.assertNotIn("http://miss-old", missing)

    def test_pending_and_missing_respect_source(self) -> None:
        upsert_thread(self.conn, "http://zz", "Z", source="zz", status="pending")
        upsert_thread(self.conn, "http://ym", "Y", source="ym", status="pending")
        upsert_item(
            self.conn, "http://zz-i", "C1",
            img_url="http://img/1.jpg", img_path=None, source="zz",
        )
        upsert_item(
            self.conn, "http://ym-i", "C2",
            img_url="http://img/2.jpg", img_path=None, source="ym",
        )
        pending = [r["url"] for r in list_pending_threads(self.conn, source="zz")]
        self.assertEqual(pending, ["http://zz"])
        missing = list_thread_urls_with_missing_assets(self.conn, source="zz")
        self.assertEqual(missing, ["http://zz-i"])

    def test_magnet_url_complete_without_torrent_file(self) -> None:
        mag = (
            "magnet:?xt=urn:btih:9C9954F1B14117D967A5A547BC5D23EC3BF63D48"
            "&dn=kks11.cc@ADN-385C"
        )
        self.assertTrue(is_magnet_url(mag))
        upsert_thread(self.conn, "http://t", "T", status="pending")
        upsert_item(
            self.conn, "http://t", "ADN-385",
            torrent_url=mag, torrent_path=None,
        )
        item = list_items_for_thread(self.conn, "http://t")[0]
        self.assertFalse(item_needs_asset_retry(item))
        self.assertFalse(item_missing_for_queue(item))
        self.assertTrue(thread_assets_complete(self.conn, "http://t"))
        self.assertEqual(list_candidate_missing_asset_rows(self.conn), [])
        self.assertEqual(list_thread_urls_with_missing_assets(self.conn), [])

    def test_upsert_item_keeps_original_crawled_at(self) -> None:
        upsert_item(self.conn, "http://t", "SORA-368", actress="星川まい")
        first = self.conn.execute(
            "SELECT crawled_at FROM items WHERE code = ?", ("SORA-368",)
        ).fetchone()[0]
        with mock.patch(
            "crawl_to_sqlite._now_iso",
            return_value="2099-01-01T00:00:00+00:00",
        ):
            upsert_item(self.conn, "http://t", "SORA-368", actress="星川まい", size_gb="7.79")
        row = self.conn.execute(
            "SELECT crawled_at, size_gb FROM items WHERE code = ?", ("SORA-368",)
        ).fetchone()
        self.assertEqual(row[0], first)
        self.assertNotEqual(row[0], "2099-01-01T00:00:00+00:00")
        self.assertEqual(row[1], "7.79")

    def test_queue_missing_uses_exists_not_magic(self) -> None:
        """Corrupt-but-present file: queue scan skips; done check still retries."""
        bad = self.files / "bad.jpg"
        bad.write_bytes(b"<html>not image</html>")
        tor = self.files / "ok.torrent"
        tor.write_bytes(b"d4:infod4:name4:testee")
        upsert_item(
            self.conn, "http://t", "C1",
            img_url="http://img/1.jpg", img_path=str(bad),
            torrent_url="http://rm/1", torrent_path=str(tor),
        )
        item = list_items_for_thread(self.conn, "http://t")[0]
        self.assertFalse(item_missing_for_queue(item))
        self.assertTrue(item_needs_asset_retry(item))
        self.assertEqual(list_thread_urls_with_missing_assets(self.conn), [])

    def test_items_column_order(self) -> None:
        cols = [r[1] for r in self.conn.execute("PRAGMA table_info(items)")]
        self.assertEqual(
            cols,
            [
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
            ],
        )

    def test_ensure_db_reorders_items_columns_on_legacy_schema(self) -> None:
        self.conn.close()
        legacy = Path(self.tmp.name) / "legacy.db"
        raw = __import__("sqlite3").connect(legacy)
        try:
            raw.execute(
                """
                CREATE TABLE items (
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
                    crawled_at TEXT NOT NULL,
                    source TEXT DEFAULT '',
                    title_transfer TEXT,
                    title TEXT,
                    UNIQUE(thread_url, code)
                )
                """
            )
            raw.execute(
                """
                INSERT INTO items (
                    id, thread_url, code, code_title, actress, size_gb,
                    img_url, img_path, torrent_url, torrent_path,
                    crawled_at, source, title_transfer, title
                ) VALUES (
                    7, 'http://t', 'AB-1', 'old title', '花子', '1.2',
                    'http://img', 'p.jpg', 'http://tor', 't.torrent',
                    '2026-01-01T00:00:00+00:00', 'src', 'zh', '片名'
                )
                """
            )
            raw.commit()
        finally:
            raw.close()

        conn = ensure_db(legacy)
        try:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(items)")]
            self.assertEqual(cols[cols.index("thread_url") + 1], "source")
            self.assertEqual(cols[cols.index("source") + 1], "code")
            self.assertEqual(cols[cols.index("actress") + 1], "size_gb")
            self.assertEqual(cols[cols.index("size_gb") + 1], "title")
            row = conn.execute(
                "SELECT id, source, code, actress, size_gb, title, code_title FROM items"
            ).fetchone()
            self.assertEqual(
                row, (7, "src", "AB-1", "花子", "1.2", "片名", "old title")
            )
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
