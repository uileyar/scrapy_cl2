from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from crawl_to_sqlite import (
    ensure_db,
    list_items_for_thread,
    thread_assets_complete,
    upsert_item,
    upsert_thread,
)
from crawl_html import _build_work_queue, _process_thread_patch


class TestCrawlPipelineResume(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = self.root / "cl.db"
        self.dl = self.root / "dl"
        self.dl.mkdir()
        self.conn = ensure_db(self.db)

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_queue_includes_pending_not_on_list(self) -> None:
        upsert_thread(self.conn, "http://old", "Old", downloads=1, status="pending")
        queue = _build_work_queue(
            conn=self.conn,
            list_threads=[],
            source="zz",
        )
        self.assertEqual(len(queue), 1)
        self.assertEqual(queue[0]["url"], "http://old")
        self.assertEqual(queue[0]["mode"], "full")

    def test_queue_patch_for_done_missing_img(self) -> None:
        upsert_thread(self.conn, "http://t", "T", status="done")
        upsert_item(
            self.conn,
            "http://t",
            "C1",
            code_title="C1 Title",
            img_url="http://img/1.jpg",
            img_path=None,
            torrent_url=None,
            torrent_path=None,
        )
        queue = _build_work_queue(conn=self.conn, list_threads=[], source="zz")
        self.assertEqual(queue[0]["mode"], "patch")
        self.assertEqual(queue[0]["url"], "http://t")

    def test_patch_downloads_only_missing_and_skips_existing(self) -> None:
        files = self.root / "assets"
        files.mkdir()
        torrent = files / "C1 Title.torrent"
        torrent.write_bytes(b"d4:infod4:name4:testee")
        upsert_thread(self.conn, "http://t", "T", status="done")
        upsert_item(
            self.conn,
            "http://t",
            "C1",
            code_title="C1 Title",
            img_url="http://img/1.jpg",
            img_path=None,
            torrent_url="http://rm/1",
            torrent_path=str(torrent),
        )

        def fake_img(url, out_dir, filename=None, **kwargs):
            path = Path(out_dir) / f"{filename}.jpg"
            path.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 20)
            return path.resolve()

        with mock.patch("crawl_html.download_image_from_url", side_effect=fake_img) as image_download, \
             mock.patch("crawl_html.download_from_rmdown_url") as torrent_download:
            _process_thread_patch(
                conn=self.conn,
                thread_url="http://t",
                title="T",
                save_dir=files,
                source="zz",
                delay_sec=0,
            )
            image_download.assert_called_once()
            torrent_download.assert_not_called()
        self.assertTrue(thread_assets_complete(self.conn, "http://t"))
        items = list_items_for_thread(self.conn, "http://t")
        self.assertTrue(items[0]["img_path"])

    def test_torrent_fail_still_upserts_keeps_pending(self) -> None:
        """Regression: torrent error must not skip upsert_item; thread stays pending."""
        upsert_thread(self.conn, "http://t", "T", status="pending")

        def boom(*args, **kwargs):
            raise RuntimeError("rmdown down")

        with mock.patch("crawl_html.fetch_and_parse_detail", return_value=[{
                "code": "C1",
                "code_title": "C1 Title",
                "actress": None,
                "size_gb": "3.0",
                "img_url": None,
                "torrent_url": "http://rm/1",
            }]), \
             mock.patch("crawl_html.download_from_rmdown_url", side_effect=boom), \
             mock.patch("crawl_html.translate_code_title", return_value=None):
            from crawl_html import _process_thread_full

            _process_thread_full(
                conn=self.conn,
                thread={"url": "http://t", "title": "T", "downloads": 0},
                day_dir=self.dl,
                source="zz",
                delay_sec=0,
            )
        items = list_items_for_thread(self.conn, "http://t")
        self.assertEqual(len(items), 1)
        self.assertIsNone(items[0]["torrent_path"])
        row = self.conn.execute(
            "SELECT status FROM threads WHERE url=?", ("http://t",)
        ).fetchone()
        self.assertEqual(row[0], "pending")
