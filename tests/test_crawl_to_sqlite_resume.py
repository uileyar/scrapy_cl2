from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from crawl_to_sqlite import (
    ensure_db,
    item_needs_asset_retry,
    list_candidate_missing_asset_rows,
    list_items_for_thread,
    list_pending_threads,
    thread_assets_complete,
    update_thread_status,
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
            torrent_url=None, torrent_path=None,
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


if __name__ == "__main__":
    unittest.main()
