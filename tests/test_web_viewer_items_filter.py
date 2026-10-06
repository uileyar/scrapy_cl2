from __future__ import annotations

import re
import sqlite3
import tempfile
import unittest
from pathlib import Path

import web_viewer
from web_viewer import TEMPLATE_ITEMS, app


class TestItemsFilterUi(unittest.TestCase):
    def test_query_waits_and_skips_ime_composition(self) -> None:
        delay = re.search(r"var delay = (\d+)", TEMPLATE_ITEMS)
        self.assertIsNotNone(delay)
        self.assertGreaterEqual(int(delay.group(1)), 800)
        self.assertIn('addEventListener("compositionstart"', TEMPLATE_ITEMS)
        self.assertIn("if (composing) return", TEMPLATE_ITEMS)

    def test_query_input_width_is_capped(self) -> None:
        rule = re.search(
            r"\.filters-items input\[type=text\] \{([^}]+)\}",
            TEMPLATE_ITEMS,
        )
        self.assertIsNotNone(rule)
        body = rule.group(1)
        self.assertIn("max-width: 420px", body)
        self.assertNotRegex(body, r"flex:\s*1\b")

    def test_actress_filter_is_typeable(self) -> None:
        self.assertIn('id="items-actress"', TEMPLATE_ITEMS)
        self.assertIn('id="actress-suggest"', TEMPLATE_ITEMS)
        self.assertNotIn('<select name="actress"', TEMPLATE_ITEMS)


class TestActressSuggest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "t.db"
        conn = sqlite3.connect(self.db)
        conn.execute("CREATE TABLE items (actress TEXT, source TEXT)")
        conn.executemany(
            "INSERT INTO items (actress, source) VALUES (?, ?)",
            [
                ("白石茉莉奈", "zz"),
                ("春菜はな", "zz"),
                ("白石茉莉奈", "gc"),
            ],
        )
        conn.commit()
        conn.close()
        self.old = web_viewer.DB_PATH
        web_viewer.DB_PATH = self.db
        self.client = app.test_client()

    def tearDown(self) -> None:
        web_viewer.DB_PATH = self.old
        self.tmp.cleanup()

    def test_suggest_matches_substring_within_source(self) -> None:
        rv = self.client.get("/items/actresses?q=白石&source=zz")
        self.assertEqual(rv.status_code, 200)
        self.assertEqual(rv.get_json(), ["白石茉莉奈"])

    def test_suggest_empty_query_returns_nothing(self) -> None:
        rv = self.client.get("/items/actresses?q=")
        self.assertEqual(rv.status_code, 200)
        self.assertEqual(rv.get_json(), [])
