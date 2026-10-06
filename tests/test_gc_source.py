"""source=gc（fid=25）：标题关键词、tid、无番号详情、管道不过滤体积/不翻译。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from crawl_to_sqlite import ensure_db, list_items_for_thread, upsert_thread
from list_parse import gc_actress_from_title, tid_from_thread_url, title_matches_gc


class TestGcTitleKeywords(unittest.TestCase):
    def test_simplified_suchang_matches(self) -> None:
        self.assertTrue(title_matches_gc("OF原档苏畅尤物女神～大奶肥臀御姐54V合集第三弹"))

    def test_traditional_suchang_matches(self) -> None:
        self.assertTrue(title_matches_gc("OF原檔蘇暢尤物女神合集"))

    def test_nana_case_insensitive_matches(self) -> None:
        self.assertTrue(title_matches_gc("合集 Nana 最新"))
        self.assertTrue(title_matches_gc("娜娜新作流出"))

    def test_banana_does_not_match(self) -> None:
        self.assertFalse(title_matches_gc("banana smoothie 合集"))

    def test_unrelated_title_does_not_match(self) -> None:
        self.assertFalse(title_matches_gc("漂亮纹身妹子，口交特写内射"))

    def test_duanju_matches(self) -> None:
        self.assertTrue(title_matches_gc("♈AI短剧♈《霸总沈清寒寻夫记》全集"))
        self.assertTrue(title_matches_gc("國產短劇合集"))

    def test_ai_real_person_matches(self) -> None:
        self.assertTrue(title_matches_gc("AI真人换脸高清合集"))
        self.assertTrue(title_matches_gc("ai真人最新"))

    def test_actress_normalized_from_title(self) -> None:
        self.assertEqual(gc_actress_from_title("OF原档苏畅尤物"), "苏畅")
        self.assertEqual(gc_actress_from_title("OF原檔蘇暢尤物"), "苏畅")
        self.assertEqual(gc_actress_from_title("Nana 最新"), "娜娜")
        self.assertEqual(gc_actress_from_title("娜娜新作"), "娜娜")
        self.assertIsNone(gc_actress_from_title("无关标题"))
        self.assertIsNone(gc_actress_from_title("♈AI短剧♈全集"))
        self.assertIsNone(gc_actress_from_title("AI真人换脸"))


class TestTidFromThreadUrl(unittest.TestCase):
    def test_htm_data_path(self) -> None:
        self.assertEqual(
            tid_from_thread_url("https://www.t66y.com/htm_data/2608/25/7406816.html"),
            "7406816",
        )

    def test_read_php_tid(self) -> None:
        self.assertEqual(
            tid_from_thread_url("https://www.t66y.com/read.php?tid=7406816&page=1"),
            "7406816",
        )

    def test_missing_tid_returns_none(self) -> None:
        self.assertIsNone(tid_from_thread_url("https://www.t66y.com/thread0806.php?fid=25"))


class TestParseGcDetailItem(unittest.TestCase):
    def test_no_code_from_chinese_h4(self) -> None:
        from detail_parse import parse_gc_detail_item

        html = """
        <html><body>
        <h4 class="f16">OF原档苏畅尤物女神～大奶肥臀御姐54V合集第三弹 [MP4/477MB]</h4>
        <div id="conttpc">
        <img src="https://picdcd.com/upload/files/suchang.jpg"/>
        <a href="https://www.rmdown.com/link.php?hash=abc123">down</a>
        </div>
        </body></html>
        """
        url = "https://www.t66y.com/htm_data/2608/25/7406816.html"
        title = "OF原档苏畅尤物女神～大奶肥臀御姐54V合集第三弹"
        items = parse_gc_detail_item(html, topic_title=title, thread_url=url)
        self.assertEqual(len(items), 1)
        it = items[0]
        self.assertEqual(it["code"], "7406816")
        self.assertEqual(it["title"], title)
        self.assertEqual(it["code_title"], title)
        self.assertEqual(it["actress"], "苏畅")
        self.assertEqual(it["img_url"], "https://picdcd.com/upload/files/suchang.jpg")
        self.assertIn("rmdown.com", it["torrent_url"] or "")
        self.assertNotRegex(it["code"], r"[A-Z]{2,}-")


class TestGcPipeline(unittest.TestCase):
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

    def test_gc_keeps_small_size_and_skips_translate(self) -> None:
        upsert_thread(self.conn, "http://t/htm_data/1/25/7406816.html", "苏畅合集", status="pending")
        parsed = [
            {
                "code": "7406816",
                "title": "苏畅合集",
                "code_title": "苏畅合集",
                "actress": "苏畅",
                "size_gb": "0.5",
                "img_url": None,
                "torrent_url": None,
            }
        ]
        with mock.patch(
            "crawl_html.fetch_and_parse_gc_detail", return_value=parsed
        ) as gc_fetch, mock.patch(
            "crawl_html.fetch_and_parse_detail"
        ) as zz_fetch, mock.patch(
            "crawl_html.title_transfer_value"
        ) as translate:
            from crawl_html import _process_thread_full

            _process_thread_full(
                conn=self.conn,
                thread={
                    "url": "http://t/htm_data/1/25/7406816.html",
                    "title": "苏畅合集",
                    "downloads": 0,
                },
                day_dir=self.dl,
                source="gc",
                delay_sec=0,
            )
        gc_fetch.assert_called_once()
        zz_fetch.assert_not_called()
        translate.assert_not_called()
        items = list_items_for_thread(self.conn, "http://t/htm_data/1/25/7406816.html")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["code"], "7406816")
        self.assertEqual(items[0]["size_gb"], "0.5")
        self.assertIsNone(items[0]["title_transfer"])

    def test_gc_keeps_item_without_size(self) -> None:
        upsert_thread(self.conn, "http://t/htm_data/1/25/111.html", "娜娜新作", status="pending")
        parsed = [
            {
                "code": "111",
                "title": "娜娜新作",
                "code_title": "娜娜新作",
                "actress": "娜娜",
                "size_gb": None,
                "img_url": None,
                "torrent_url": None,
            }
        ]
        with mock.patch(
            "crawl_html.fetch_and_parse_gc_detail", return_value=parsed
        ), mock.patch("crawl_html.title_transfer_value") as translate:
            from crawl_html import _process_thread_full

            _process_thread_full(
                conn=self.conn,
                thread={
                    "url": "http://t/htm_data/1/25/111.html",
                    "title": "娜娜新作",
                    "downloads": 0,
                },
                day_dir=self.dl,
                source="gc",
                delay_sec=0,
            )
        translate.assert_not_called()
        items = list_items_for_thread(self.conn, "http://t/htm_data/1/25/111.html")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["code"], "111")
        self.assertIsNone(items[0]["size_gb"])

    def test_pipeline_enqueues_only_keyword_titles(self) -> None:
        rows = [
            {
                "url": "http://t/htm_data/1/25/7406816.html",
                "title": "苏畅合集",
                "downloads": 1,
            },
            {
                "url": "http://t/htm_data/1/25/7400001.html",
                "title": "♈AI短剧♈全集",
                "downloads": 3,
            },
            {
                "url": "http://t/htm_data/1/25/7400002.html",
                "title": "AI真人换脸",
                "downloads": 4,
            },
            {
                "url": "http://t/htm_data/1/25/7400000.html",
                "title": "漂亮纹身妹子",
                "downloads": 2,
            },
        ]
        with mock.patch(
            "crawl_html.fetch_and_parse_list_page", return_value=rows
        ), mock.patch("crawl_html._process_thread_full") as full, mock.patch(
            "crawl_html._process_thread_patch"
        ):
            from crawl_html import crawl_pipeline

            crawl_pipeline(
                source="gc",
                download_dir=self.dl,
                db_path=self.db,
                max_pages=1,
                delay_sec=0,
                list_delay_sec=0,
            )
        self.conn.close()
        self.conn = ensure_db(self.db)
        urls = [r[0] for r in self.conn.execute("SELECT url FROM threads ORDER BY url")]
        self.assertEqual(
            urls,
            [
                "http://t/htm_data/1/25/7400001.html",
                "http://t/htm_data/1/25/7400002.html",
                "http://t/htm_data/1/25/7406816.html",
            ],
        )
        self.assertEqual(full.call_count, 3)


class TestGcSourceMap(unittest.TestCase):
    def test_source_gc_maps_to_fid_25(self) -> None:
        from crawl_html import SOURCE_URL_MAP, resolve_url

        self.assertEqual(
            SOURCE_URL_MAP["gc"],
            "https://www.t66y.com/thread0806.php?fid=25",
        )
        self.assertEqual(resolve_url("gc"), SOURCE_URL_MAP["gc"])

