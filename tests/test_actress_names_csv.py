from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from crawl_to_sqlite import ensure_db, replace_actress_names_from_csv


class TestActressNamesCsv(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = self.root / "t.db"
        self.conn = ensure_db(self.db)

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def _write_csv(self, path: Path, rows: list[tuple[str, str]]) -> None:
        with path.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["日文", "中文"])
            w.writerows(rows)

    def test_import_maps_columns_and_count(self) -> None:
        csv_path = self.root / "a.csv"
        self._write_csv(
            csv_path,
            [
                ("波多野結衣", "波多野结衣"),
                ("三上悠亜", ""),
                ("", "仅中文"),
                ("", ""),
            ],
        )
        n = replace_actress_names_from_csv(self.conn, csv_path)
        self.assertEqual(n, 3)
        rows = self.conn.execute(
            "SELECT ja_name, zh_name FROM actress_names ORDER BY id"
        ).fetchall()
        self.assertEqual(
            rows,
            [
                ("波多野結衣", "波多野结衣"),
                ("三上悠亜", ""),
                ("", "仅中文"),
            ],
        )

    def test_second_import_replaces_not_accumulates(self) -> None:
        csv1 = self.root / "1.csv"
        csv2 = self.root / "2.csv"
        self._write_csv(csv1, [("旧日文", "旧中文"), ("保留前", "保留前中")])
        self._write_csv(csv2, [("新日文", "新中文")])
        replace_actress_names_from_csv(self.conn, csv1)
        n = replace_actress_names_from_csv(self.conn, csv2)
        self.assertEqual(n, 1)
        rows = self.conn.execute(
            "SELECT ja_name, zh_name FROM actress_names"
        ).fetchall()
        self.assertEqual(rows, [("新日文", "新中文")])

    def test_dedupe_within_csv(self) -> None:
        csv_path = self.root / "dup.csv"
        self._write_csv(
            csv_path,
            [
                ("A", "甲"),
                ("A", "甲"),
            ],
        )
        n = replace_actress_names_from_csv(self.conn, csv_path)
        self.assertEqual(n, 1)
        count = self.conn.execute("SELECT COUNT(*) FROM actress_names").fetchone()[0]
        self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()
