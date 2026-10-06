from __future__ import annotations

import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from crawl_to_sqlite import (
    ensure_db,
    list_items_for_thread,
    thread_assets_complete,
    upsert_item,
    upsert_thread,
)
from crawl_html import _build_work_queue, _ensure_torrent, _process_thread_patch


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
        upsert_thread(
            self.conn, "http://old", "Old", downloads=1, source="zz", status="pending"
        )
        queue = _build_work_queue(
            conn=self.conn,
            list_threads=[],
            source="zz",
        )
        self.assertEqual(len(queue), 1)
        self.assertEqual(queue[0]["url"], "http://old")
        self.assertEqual(queue[0]["mode"], "full")

    def test_queue_skips_pending_older_than_lookback(self) -> None:
        upsert_thread(self.conn, "http://fresh", "Fresh", source="zz", status="pending")
        upsert_thread(self.conn, "http://stale", "Stale", source="zz", status="pending")
        stale_ts = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        self.conn.execute(
            "UPDATE threads SET crawled_at = ? WHERE url = ?",
            (stale_ts, "http://stale"),
        )
        self.conn.commit()
        queue = _build_work_queue(conn=self.conn, list_threads=[], source="zz")
        urls = [t["url"] for t in queue]
        self.assertIn("http://fresh", urls)
        self.assertNotIn("http://stale", urls)

    def test_queue_only_same_source(self) -> None:
        upsert_thread(self.conn, "http://zz-p", "Z", source="zz", status="pending")
        upsert_thread(self.conn, "http://ym-p", "Y", source="ym", status="pending")
        upsert_thread(self.conn, "http://zz-m", "ZM", source="zz", status="done")
        upsert_thread(self.conn, "http://ym-m", "YM", source="ym", status="done")
        upsert_item(
            self.conn,
            "http://zz-m",
            "C1",
            img_url="http://img/z.jpg",
            img_path=None,
            source="zz",
        )
        upsert_item(
            self.conn,
            "http://ym-m",
            "C2",
            img_url="http://img/y.jpg",
            img_path=None,
            source="ym",
        )
        queue = _build_work_queue(conn=self.conn, list_threads=[], source="zz")
        urls = {t["url"] for t in queue}
        self.assertEqual(urls, {"http://zz-p", "http://zz-m"})

    def test_queue_patch_for_done_missing_img(self) -> None:
        upsert_thread(self.conn, "http://t", "T", source="zz", status="done")
        upsert_item(
            self.conn,
            "http://t",
            "C1",
            code_title="C1 Title",
            img_url="http://img/1.jpg",
            img_path=None,
            torrent_url="http://rm/1",
            torrent_path="/missing/x.torrent",
            source="zz",
        )
        queue = _build_work_queue(conn=self.conn, list_threads=[], source="zz")
        self.assertEqual(queue[0]["mode"], "patch")
        self.assertEqual(queue[0]["url"], "http://t")

    def test_queue_full_when_torrent_url_missing(self) -> None:
        """缺种子 URL 必须 full 重解析，不能 patch。"""
        upsert_thread(self.conn, "http://t", "T", source="zz", status="done")
        upsert_item(
            self.conn,
            "http://t",
            "C1",
            code_title="C1 Title",
            img_url="http://img/1.jpg",
            img_path=str(self.root / "a.jpg"),
            torrent_url=None,
            torrent_path=None,
            source="zz",
        )
        (self.root / "a.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 20)
        queue = _build_work_queue(conn=self.conn, list_threads=[], source="zz")
        self.assertEqual(len(queue), 1)
        self.assertEqual(queue[0]["mode"], "full")
        self.assertEqual(queue[0]["url"], "http://t")

    def test_full_without_torrent_url_stays_pending(self) -> None:
        upsert_thread(self.conn, "http://t", "T", status="pending")
        with mock.patch(
            "crawl_html.fetch_and_parse_detail",
            return_value=[
                {
                    "code": "C1",
                    "code_title": "C1 Title",
                    "actress": None,
                    "size_gb": "3.0",
                    "img_url": None,
                    "torrent_url": None,
                }
            ],
        ), mock.patch("crawl_html.title_transfer_value", return_value=None):
            from crawl_html import _process_thread_full

            _process_thread_full(
                conn=self.conn,
                thread={"url": "http://t", "title": "T", "downloads": 0},
                day_dir=self.dl,
                source="zz",
                delay_sec=0,
            )
        row = self.conn.execute(
            "SELECT status FROM threads WHERE url=?", ("http://t",)
        ).fetchone()
        self.assertEqual(row[0], "pending")
        self.assertFalse(thread_assets_complete(self.conn, "http://t"))

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

        with mock.patch(
            "crawl_html.download_image_from_url", side_effect=fake_img
        ) as image_download, mock.patch(
            "crawl_html.download_from_rmdown_url"
        ) as torrent_download:
            _process_thread_patch(
                conn=self.conn,
                thread_url="http://t",
                title="T",
                save_dir=files,
                source="zz",
                delay_sec=0,
            )
            image_download.assert_called_once()
            self.assertEqual(
                image_download.call_args.kwargs.get("referer"),
                "http://t/",
            )
            torrent_download.assert_not_called()
        self.assertTrue(thread_assets_complete(self.conn, "http://t"))
        items = list_items_for_thread(self.conn, "http://t")
        self.assertTrue(items[0]["img_path"])

    def test_torrent_fail_still_upserts_keeps_pending(self) -> None:
        """Regression: torrent error must not skip upsert_item; thread stays pending."""
        upsert_thread(self.conn, "http://t", "T", status="pending")

        def boom(*args, **kwargs):
            raise RuntimeError("rmdown down")

        with mock.patch(
            "crawl_html.fetch_and_parse_detail",
            return_value=[
                {
                    "code": "C1",
                    "code_title": "C1 Title",
                    "actress": None,
                    "size_gb": "3.0",
                    "img_url": None,
                    "torrent_url": "http://rm/1",
                }
            ],
        ), mock.patch(
            "crawl_html.download_from_rmdown_url", side_effect=boom
        ), mock.patch(
            "crawl_html.title_transfer_value", return_value=None
        ):
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

    def test_full_thread_all_size_filtered_marks_done_without_downloads(self) -> None:
        upsert_thread(self.conn, "http://t", "T", status="pending")
        parsed_items = [
            {
                "code": "C1",
                "code_title": "C1 Title",
                "actress": None,
                "size_gb": "1.5",
                "img_url": "http://img/1.jpg",
                "torrent_url": "http://rm/1",
            },
            {
                "code": "C2",
                "code_title": "C2 Title",
                "actress": None,
                "size_gb": "1.0",
                "img_url": "http://img/2.jpg",
                "torrent_url": "http://rm/2",
            },
        ]

        with mock.patch(
            "crawl_html.fetch_and_parse_detail", return_value=parsed_items
        ), mock.patch(
            "crawl_html.download_image_from_url"
        ) as image_download, mock.patch(
            "crawl_html.download_from_rmdown_url"
        ) as torrent_download:
            from crawl_html import _process_thread_full

            _process_thread_full(
                conn=self.conn,
                thread={"url": "http://t", "title": "T", "downloads": 0},
                day_dir=self.dl,
                source="zz",
                delay_sec=0,
            )

        self.assertEqual(list_items_for_thread(self.conn, "http://t"), [])
        image_download.assert_not_called()
        torrent_download.assert_not_called()
        row = self.conn.execute(
            "SELECT status FROM threads WHERE url=?", ("http://t",)
        ).fetchone()
        self.assertEqual(row[0], "done")

    def test_full_thread_mixed_size_filter_and_kept_item_marks_done(self) -> None:
        upsert_thread(self.conn, "http://t", "T", status="pending")
        parsed_items = [
            {
                "code": "C1",
                "code_title": "C1 Title",
                "actress": None,
                "size_gb": "1.5",
                "img_url": "http://img/1.jpg",
                "torrent_url": "http://rm/1",
            },
            {
                "code": "C2",
                "code_title": "C2 Title",
                "actress": None,
                "size_gb": "3.0",
                "img_url": None,
                "torrent_url": "http://rm/2",
            },
        ]

        def fake_torrent(url, out_dir, filename=None, **kwargs):
            path = Path(out_dir) / f"{filename}.torrent"
            path.write_bytes(b"d4:infod4:name4:testee")
            return path.resolve()

        with mock.patch(
            "crawl_html.fetch_and_parse_detail", return_value=parsed_items
        ), mock.patch(
            "crawl_html.download_image_from_url"
        ) as image_download, mock.patch(
            "crawl_html.download_from_rmdown_url", side_effect=fake_torrent
        ) as torrent_download, mock.patch(
            "crawl_html.title_transfer_value", return_value=None
        ):
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
        self.assertEqual(items[0]["code"], "C2")
        image_download.assert_not_called()
        torrent_download.assert_called_once()
        row = self.conn.execute(
            "SELECT status FROM threads WHERE url=?", ("http://t",)
        ).fetchone()
        self.assertEqual(row[0], "done")

    def test_patch_deletes_corrupt_db_image_in_old_dir(self) -> None:
        old_dir = self.root / "old_assets"
        old_dir.mkdir()
        corrupt_img = old_dir / "C1 Title.jpg"
        corrupt_img.write_bytes(b"not-a-jpg")

        new_dir = self.root / "new_assets"
        new_dir.mkdir()

        upsert_thread(self.conn, "http://t", "T", status="done")
        upsert_item(
            self.conn,
            "http://t",
            "C1",
            code_title="C1 Title",
            img_url="http://img/1.jpg",
            img_path=str(corrupt_img),
            torrent_url=None,
            torrent_path=None,
        )

        def fake_img(url, out_dir, filename=None, **kwargs):
            path = Path(out_dir) / f"{filename}.jpg"
            path.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 20)
            return path.resolve()

        with mock.patch(
            "crawl_html.download_image_from_url", side_effect=fake_img
        ) as image_download:
            _process_thread_patch(
                conn=self.conn,
                thread_url="http://t",
                title="T",
                save_dir=new_dir,
                source="zz",
                delay_sec=0,
            )
            image_download.assert_called_once()

        self.assertFalse(corrupt_img.exists())
        items = list_items_for_thread(self.conn, "http://t")
        new_path = Path(items[0]["img_path"])
        self.assertTrue(new_path.is_file())
        self.assertEqual(new_path.parent.resolve(), new_dir.resolve())

    def test_patch_deletes_corrupt_db_torrent_in_old_dir(self) -> None:
        old_dir = self.root / "old_assets"
        old_dir.mkdir()
        corrupt_torrent = old_dir / "C1 Title.torrent"
        corrupt_torrent.write_bytes(b"")

        new_dir = self.root / "new_assets"
        new_dir.mkdir()

        upsert_thread(self.conn, "http://t", "T", status="done")
        upsert_item(
            self.conn,
            "http://t",
            "C1",
            code_title="C1 Title",
            img_url=None,
            img_path=None,
            torrent_url="http://rm/1",
            torrent_path=str(corrupt_torrent),
        )

        def fake_torrent(url, out_dir, filename=None, **kwargs):
            path = Path(out_dir) / f"{filename}.torrent"
            path.write_bytes(b"d4:infod4:name4:testee")
            return path.resolve()

        with mock.patch(
            "crawl_html.download_from_rmdown_url", side_effect=fake_torrent
        ) as torrent_download:
            _process_thread_patch(
                conn=self.conn,
                thread_url="http://t",
                title="T",
                save_dir=new_dir,
                source="zz",
                delay_sec=0,
            )
            torrent_download.assert_called_once()

        self.assertFalse(corrupt_torrent.exists())
        items = list_items_for_thread(self.conn, "http://t")
        new_path = Path(items[0]["torrent_path"])
        self.assertTrue(new_path.is_file())
        self.assertEqual(new_path.parent.resolve(), new_dir.resolve())

    def test_magnet_url_skips_rmdown_download(self) -> None:
        mag = (
            "magnet:?xt=urn:btih:9C9954F1B14117D967A5A547BC5D23EC3BF63D48"
            "&dn=kks11.cc@ADN-385C"
        )
        with mock.patch("crawl_html.download_from_rmdown_url") as torrent_download:
            path = _ensure_torrent(mag, None, self.dl, "ADN-385")
            torrent_download.assert_not_called()
        self.assertIsNone(path)

    def test_full_does_not_call_translate_on_critical_path(self) -> None:
        upsert_thread(self.conn, "http://t", "T", status="pending")
        parsed = [
            {
                "code": "ADN-385",
                "code_title": "隣の人妻は僕の元カノ",
                "actress": None,
                "size_gb": "3.0",
                "img_url": None,
                "torrent_url": None,
            }
        ]
        with mock.patch(
            "crawl_html.fetch_and_parse_detail", return_value=parsed
        ), mock.patch(
            "crawl_html.title_transfer_value", return_value="不该出现在主路径"
        ) as translate:
            from crawl_html import _process_thread_full

            _process_thread_full(
                conn=self.conn,
                thread={"url": "http://t", "title": "T", "downloads": 0},
                day_dir=self.dl,
                source="zz",
                delay_sec=0,
            )
        translate.assert_not_called()
        items = list_items_for_thread(self.conn, "http://t")
        self.assertEqual(len(items), 1)
        self.assertIsNone(items[0]["title_transfer"])
        self.assertEqual(items[0]["code_title"], "隣の人妻は僕の元カノ")

    def test_full_preserves_existing_title_transfer(self) -> None:
        upsert_thread(self.conn, "http://t", "T", status="pending")
        upsert_item(
            self.conn,
            "http://t",
            "ADN-385",
            code_title="隣の人妻は僕の元カノ",
            title_transfer="隔壁人妻是我前女友",
            size_gb="3.0",
            source="zz",
        )
        parsed = [
            {
                "code": "ADN-385",
                "code_title": "隣の人妻は僕の元カノ",
                "actress": None,
                "size_gb": "3.0",
                "img_url": None,
                "torrent_url": None,
            }
        ]
        with mock.patch(
            "crawl_html.fetch_and_parse_detail", return_value=parsed
        ), mock.patch(
            "crawl_html.title_transfer_value", return_value="不该覆盖"
        ):
            from crawl_html import _process_thread_full

            _process_thread_full(
                conn=self.conn,
                thread={"url": "http://t", "title": "T", "downloads": 0},
                day_dir=self.dl,
                source="zz",
                delay_sec=0,
            )
        items = list_items_for_thread(self.conn, "http://t")
        self.assertEqual(items[0]["title_transfer"], "隔壁人妻是我前女友")

    def test_title_worker_fills_after_download_without_blocking(self) -> None:
        from crawl_html import TitleTransferWorker, _process_thread_full

        upsert_thread(self.conn, "http://t", "T", status="pending")
        parsed = [
            {
                "code": "ADN-385",
                "code_title": "隣の人妻は僕の元カノ",
                "actress": None,
                "size_gb": "3.0",
                "img_url": None,
                "torrent_url": None,
            }
        ]
        started = threading.Event()
        release = threading.Event()

        def slow_zh(code_title):
            started.set()
            self.assertTrue(release.wait(timeout=2))
            return "隔壁人妻是我前女友"

        worker = TitleTransferWorker(self.db, translate=slow_zh)
        worker.start()
        try:
            with mock.patch("crawl_html.fetch_and_parse_detail", return_value=parsed):
                t0 = time.monotonic()
                _process_thread_full(
                    conn=self.conn,
                    thread={"url": "http://t", "title": "T", "downloads": 0},
                    day_dir=self.dl,
                    source="zz",
                    delay_sec=0,
                    title_worker=worker,
                )
                elapsed = time.monotonic() - t0
            self.assertLess(elapsed, 0.5)
            items = list_items_for_thread(self.conn, "http://t")
            self.assertIsNone(items[0]["title_transfer"])
            self.assertTrue(started.wait(timeout=2))
            release.set()
            worker.close()
            self.conn.commit()
            items = list_items_for_thread(self.conn, "http://t")
            self.assertEqual(items[0]["title_transfer"], "隔壁人妻是我前女友")
        finally:
            release.set()
            worker.close()
